from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import ValidationError

from .canonical_route import ROUTE_PARSER_VERSION, CanonicalRouteService
from .schemas import (
    LibraryContractError,
    LibraryProviderUnavailableError,
    ReferenceIndexPage,
)

INDEX_VISUAL_PROMPT_VERSION = "herder-index-vision.v1"
_PROMPT = """Transcribe esta página fotografiada del índice bilingüe español/alemán.
Devuelve solo JSON estricto con reference_pdf_page, physical_index_page y entries.
Cada entry contiene hierarchy_level (top_level_theme, section, subsection, item,
front_matter o back_matter), theme_number, local_number, parent_path, title_es,
title_de, printed_page_label, raw_visible_text, confidence, visual_region, notes y
parse_status (verified, uncertain o cropped). No traduzcas, no completes texto
ilegible y no leas la página enfrentada. Separa en los encabezados Tema N el
título español y el alemán visibles alrededor de la barra. Conserva diéresis,
ß, signos y numeración local. Esta es la página {page_number} del PDF de referencia."""


def load_visual_bundle(directory: Path) -> list[ReferenceIndexPage]:
    if not directory.is_dir():
        raise LibraryContractError("El bundle visual no existe.")
    files = sorted(directory.glob("page-*.json"))
    if not files:
        raise LibraryContractError("El bundle visual no contiene páginas JSON.")
    pages: list[ReferenceIndexPage] = []
    for path in files:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise LibraryContractError("Una salida visual supera el límite seguro.")
        try:
            pages.append(ReferenceIndexPage.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValidationError, ValueError) as exc:
            raise LibraryContractError(f"Salida visual inválida: {path.name}.") from exc
    numbers = [page.reference_pdf_page for page in pages]
    if len(numbers) != len(set(numbers)):
        raise LibraryContractError("El bundle contiene páginas duplicadas.")
    return sorted(pages, key=lambda page: page.reference_pdf_page)


def load_canonical_index(path: Path) -> list[ReferenceIndexPage]:
    """Adapt the reviewed editorial JSON without re-running visual extraction."""
    if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
        raise LibraryContractError("El JSON canónico no existe o supera el límite seguro.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryContractError("El JSON canónico no es legible.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("themes"), list):
        raise LibraryContractError("El JSON canónico no contiene la jerarquía esperada.")
    document = payload.get("document")
    page_count = document.get("reference_pdf_pages") if isinstance(document, dict) else None
    if not isinstance(page_count, int) or page_count < 1:
        raise LibraryContractError("El JSON canónico no declara sus páginas de referencia.")
    entries_by_page: dict[int, list[dict[str, object]]] = {
        number: [] for number in range(1, page_count + 1)
    }

    def add_editorial_entry(
        item: dict[str, object],
        *,
        hierarchy_level: str,
        theme_number: int | None,
        parent_path: list[str],
    ) -> None:
        reference_page = item.get("reference_pdf_page")
        if not isinstance(reference_page, int) or reference_page not in entries_by_page:
            raise LibraryContractError("Una entrada canónica referencia una página inexistente.")
        title_es = item.get("title_es")
        title_de = item.get("title_de")
        if not isinstance(title_es, str) or not title_es.strip():
            raise LibraryContractError("Una entrada canónica carece de título español.")
        status = item.get("status")
        editorial_status = "user_confirmed" if status == "user_confirmed" else "verified"
        entries_by_page[reference_page].append(
            {
                "hierarchy_level": hierarchy_level,
                "theme_number": theme_number,
                "local_number": item.get("local_number"),
                "parent_path": parent_path,
                "title_es": title_es,
                "title_de": title_de if isinstance(title_de, str) else None,
                "printed_page_label": item.get("printed_page"),
                "raw_visible_text": title_es,
                "confidence": float(item.get("confidence", 1.0)),
                "visual_region": "unknown",
                "notes": item.get("notes") if isinstance(item.get("notes"), str) else None,
                "parse_status": "verified",
                "editorial_status": editorial_status,
            }
        )

    for item in payload.get("front_matter", []):
        if isinstance(item, dict):
            add_editorial_entry(
                item, hierarchy_level="front_matter", theme_number=None, parent_path=[]
            )
    seen_numbers: set[int] = set()
    for theme in payload["themes"]:
        if not isinstance(theme, dict) or not isinstance(theme.get("theme_number"), int):
            raise LibraryContractError("Un tema canónico tiene un número inválido.")
        number = int(theme["theme_number"])
        if number in seen_numbers:
            raise LibraryContractError("El JSON canónico contiene temas duplicados.")
        seen_numbers.add(number)
        reference_page = theme.get("reference_pdf_page")
        title_es = theme.get("title_es")
        if not isinstance(reference_page, int) or reference_page not in entries_by_page:
            raise LibraryContractError("Un tema canónico referencia una página inexistente.")
        if not isinstance(title_es, str) or not title_es.strip():
            raise LibraryContractError("Un tema canónico carece de título español.")
        entries_by_page[reference_page].append(
            {
                "hierarchy_level": "top_level_theme",
                "theme_number": number,
                "local_number": None,
                "parent_path": [],
                "title_es": title_es,
                "title_de": theme.get("title_de"),
                "printed_page_label": theme.get("printed_start_page"),
                "raw_visible_text": f"Tema {number}. {title_es}",
                "confidence": float(theme.get("confidence", 1.0)),
                "visual_region": "unknown",
                "notes": theme.get("notes"),
                "parse_status": "verified",
                "editorial_status": (
                    "user_confirmed" if theme.get("status") == "user_confirmed" else "verified"
                ),
            }
        )

        def flatten(
            children: object,
            *,
            depth: int,
            parent_path: list[str],
            owning_theme: int = number,
        ) -> None:
            if not isinstance(children, list):
                return
            for child in children:
                if not isinstance(child, dict):
                    raise LibraryContractError("Un subapartado canónico no es un objeto.")
                level = "section" if depth == 1 else "subsection" if depth == 2 else "item"
                add_editorial_entry(
                    child,
                    hierarchy_level=level,
                    theme_number=owning_theme,
                    parent_path=parent_path,
                )
                child_title = child.get("title_es")
                flatten(
                    child.get("children"),
                    depth=depth + 1,
                    parent_path=[*parent_path, str(child_title)],
                    owning_theme=owning_theme,
                )

        flatten(theme.get("sections"), depth=1, parent_path=[f"Tema {number}"])
    for item in payload.get("back_matter", []):
        if isinstance(item, dict):
            add_editorial_entry(
                item, hierarchy_level="back_matter", theme_number=None, parent_path=[]
            )
    try:
        return [
            ReferenceIndexPage.model_validate(
                {
                    "reference_pdf_page": number,
                    "physical_index_page": number,
                    "entries": entries_by_page[number],
                }
            )
            for number in range(1, page_count + 1)
        ]
    except ValidationError as exc:
        raise LibraryContractError("El JSON canónico no cumple el contrato interno.") from exc


class HerderIndexVisualExtractor:
    """Local-only, one-page-at-a-time visual extraction for reference indexes."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = "qwen3-vl:8b",
        timeout_seconds: float = 300,
        keep_alive: str = "10m",
    ):
        if "qwen3.5:27b" in model.casefold():
            raise LibraryContractError("qwen3.5:27b no admite esta extracción visual.")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive
        self.page_seconds: list[float] = []

    async def extract(self, reference_path: Path) -> list[ReferenceIndexPage]:
        _, _, page_count = CanonicalRouteService.inspect_reference(reference_path)
        renderer = shutil.which("pdftoppm")
        if not renderer:
            raise LibraryProviderUnavailableError("pdftoppm no está disponible localmente.")
        pages: list[ReferenceIndexPage] = []
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for page_number in range(1, page_count + 1):
                started = time.monotonic()
                image = self._render(renderer, reference_path, output, page_number)
                pages.append(await self._extract_page(image, page_number))
                self.page_seconds.append(time.monotonic() - started)
        return pages

    @staticmethod
    def _render(renderer: str, reference: Path, output: Path, page_number: int) -> Path:
        prefix = output / f"page-{page_number:02d}"
        result = subprocess.run(
            [
                renderer,
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-r",
                "180",
                "-singlefile",
                "-png",
                str(reference),
                str(prefix),
            ],
            capture_output=True,
            check=False,
            timeout=90,
        )
        image = prefix.with_suffix(".png")
        if result.returncode != 0 or not image.is_file():
            raise LibraryProviderUnavailableError(
                f"No se pudo renderizar la página {page_number} del índice."
            )
        if image.stat().st_size > 20 * 1024 * 1024:
            raise LibraryContractError("Una página renderizada supera el límite seguro.")
        return image

    async def _extract_page(self, image: Path, page_number: int) -> ReferenceIndexPage:
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "stream": False,
                        "think": False,
                        "keep_alive": self.keep_alive,
                        "format": "json",
                        "options": {"temperature": 0, "num_predict": 16_384, "seed": 0},
                        "messages": [
                            {
                                "role": "user",
                                "content": _PROMPT.format(page_number=page_number),
                                "images": [encoded],
                            }
                        ],
                    },
                )
                response.raise_for_status()
                payload = response.json()
            content = str(payload.get("message", {}).get("content", ""))
            page = ReferenceIndexPage.model_validate_json(content)
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise LibraryProviderUnavailableError(
                f"La extracción visual local falló en la página {page_number}; "
                "puede repararse selectivamente y reanudarse desde un bundle."
            ) from exc
        if page.reference_pdf_page != page_number:
            page = page.model_copy(update={"reference_pdf_page": page_number})
        return page

    @property
    def total_seconds(self) -> float:
        return sum(self.page_seconds)

    @property
    def parser_version(self) -> str:
        return f"{ROUTE_PARSER_VERSION}+{INDEX_VISUAL_PROMPT_VERSION}"
