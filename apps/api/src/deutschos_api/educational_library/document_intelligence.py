from __future__ import annotations

import base64
import hashlib
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import httpx

from deutschos_api.core.config import Settings

from .database import LibraryDatabase
from .schemas import (
    LibraryContractError,
    LibraryNotFoundError,
    LibraryProviderUnavailableError,
    PageQualityRead,
    PageVariantRead,
)
from .service import json_dump, json_load, utc_text

PAGE_QUALITY_VERSION = "page-quality.v1"
PDFTEXT_VARIANT_VERSION = "pdftotext-layout.v1"
VISION_VARIANT_VERSION = "lm_studio-vision-transcription.v1"


def _ratios(text: str) -> dict[str, float | bool | list[str] | str]:
    length = len(text)
    if not length:
        return {
            "replacement_ratio": 0.0,
            "weird_character_ratio": 0.0,
            "repeated_line_ratio": 0.0,
            "damaged_german_ratio": 0.0,
            "ordering_warning": False,
            "columns_warning": False,
            "tables_warning": False,
            "quality": "unusable",
            "warnings": ["La página no contiene texto extraído."],
        }
    replacement = text.count("\ufffd") / length
    weird = (
        sum(not character.isprintable() and character not in "\n\r\t" for character in text)
        / length
    )
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    counts = Counter(lines)
    repeated = (
        sum(total - 1 for total in counts.values() if total > 1) / len(lines) if lines else 0.0
    )
    damaged_tokens = re.findall(r"(?:\b\w*[0-9|]{2,}\w*\b|Ã.|â€|mUssen|KiJnnen|daB)", text)
    words = max(1, len(re.findall(r"\b\w+\b", text)))
    damaged = min(1.0, len(damaged_tokens) / words)
    columns = sum(bool(re.search(r"\S\s{6,}\S", line)) for line in text.splitlines()) >= 3
    tables = sum(line.count("|") >= 2 for line in text.splitlines()) >= 2
    ordering = bool(re.search(r"\b(?:[A-Za-zÄÖÜäöüß]\s+){8,}", text))
    warnings: list[str] = []
    if length < 40:
        warnings.append("Muy poco texto extraído.")
    if replacement > 0.01:
        warnings.append("Hay caracteres de reemplazo.")
    if weird > 0.01:
        warnings.append("Hay caracteres de control o ilegibles.")
    if repeated > 0.25:
        warnings.append("Se repiten líneas con frecuencia.")
    if damaged > 0.02:
        warnings.append("Posible texto alemán dañado por extracción u OCR.")
    if columns:
        warnings.append("La página puede contener varias columnas.")
    if tables:
        warnings.append("La página puede contener una tabla.")
    if ordering:
        warnings.append("El orden de lectura puede estar alterado.")
    if length < 20 or replacement > 0.15 or weird > 0.10:
        quality = "unusable"
    elif length < 100 or replacement > 0.03 or damaged > 0.08:
        quality = "poor"
    elif length < 350 or columns or tables or damaged > 0.02:
        quality = "acceptable"
    else:
        quality = "good"
    return {
        "replacement_ratio": replacement,
        "weird_character_ratio": weird,
        "repeated_line_ratio": repeated,
        "damaged_german_ratio": damaged,
        "ordering_warning": ordering,
        "columns_warning": columns,
        "tables_warning": tables,
        "quality": quality,
        "warnings": warnings,
    }


class DocumentIntelligenceService:
    def __init__(self, database: LibraryDatabase, settings: Settings):
        self.database = database
        self.settings = settings

    def analyze_source(self, source_id: str) -> list[PageQualityRead]:
        with self.database.transaction(immediate=True) as connection:
            source = self._source(connection, source_id)
            version_id = source["current_version_id"]
            if not version_id:
                raise LibraryContractError("La fuente no tiene una versión activa.")
            rows = connection.execute(
                "SELECT se.page_start page_number,group_concat(se.text,char(10)) text "
                "FROM sections se JOIN documents d ON d.id=se.document_id "
                "WHERE d.source_version_id=? AND se.page_start IS NOT NULL "
                "GROUP BY se.page_start ORDER BY se.page_start",
                (version_id,),
            ).fetchall()
            page_count_row = connection.execute(
                "SELECT page_count FROM documents WHERE source_version_id=?", (version_id,)
            ).fetchone()
            page_count = int(page_count_row["page_count"] or 0) if page_count_row else 0
            by_page = {int(row["page_number"]): str(row["text"] or "") for row in rows}
            now = utc_text()
            for page_number in range(1, page_count + 1):
                text = by_page.get(page_number, "")
                metrics = _ratios(text)
                connection.execute(
                    "INSERT INTO page_quality(source_version_id,page_number,extraction_method,"
                    "character_count,detected_language,text_density,replacement_ratio,"
                    "weird_character_ratio,repeated_line_ratio,ordering_warning,columns_warning,"
                    "tables_warning,damaged_german_ratio,quality,warnings_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,'de-es',?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_version_id,"
                    "page_number) DO UPDATE SET extraction_method=excluded.extraction_method,"
                    "character_count=excluded.character_count,text_density=excluded.text_density,"
                    "replacement_ratio=excluded.replacement_ratio,"
                    "weird_character_ratio=excluded.weird_character_ratio,"
                    "repeated_line_ratio=excluded.repeated_line_ratio,"
                    "ordering_warning=excluded.ordering_warning,columns_warning=excluded.columns_warning,"
                    "tables_warning=excluded.tables_warning,damaged_german_ratio=excluded.damaged_german_ratio,"
                    "quality=excluded.quality,warnings_json=excluded.warnings_json,updated_at=excluded.updated_at",
                    (
                        version_id,
                        page_number,
                        "pdftotext" if source["format"] == ".pdf" else "source_extractor",
                        len(text),
                        len(text),
                        metrics["replacement_ratio"],
                        metrics["weird_character_ratio"],
                        metrics["repeated_line_ratio"],
                        int(bool(metrics["ordering_warning"])),
                        int(bool(metrics["columns_warning"])),
                        int(bool(metrics["tables_warning"])),
                        metrics["damaged_german_ratio"],
                        metrics["quality"],
                        json_dump(metrics["warnings"]),
                        now,
                        now,
                    ),
                )
        return self.list_quality(source_id)

    def list_quality(self, source_id: str) -> list[PageQualityRead]:
        with self.database.connect() as connection:
            source = self._source(connection, source_id)
            if not source["current_version_id"]:
                return []
            rows = connection.execute(
                "SELECT * FROM page_quality WHERE source_version_id=? ORDER BY page_number",
                (source["current_version_id"],),
            ).fetchall()
        return [self._quality_read(row) for row in rows]

    def list_variants(self, source_id: str, page_number: int) -> list[PageVariantRead]:
        with self.database.connect() as connection:
            source = self._source(connection, source_id)
            rows = connection.execute(
                "SELECT * FROM page_extraction_variants WHERE source_version_id=? AND page_number=? "
                "ORDER BY created_at,id",
                (source["current_version_id"], page_number),
            ).fetchall()
        return [self._variant_read(row) for row in rows]

    def reprocess_pdftotext(self, source_id: str, page_number: int) -> PageVariantRead:
        executable = shutil.which("pdftotext")
        if not executable:
            raise LibraryProviderUnavailableError("pdftotext no está disponible localmente.")
        source, path = self._source_path(source_id)
        if source["format"] != ".pdf":
            raise LibraryContractError("El reprocesamiento por página solo admite PDF.")
        result = subprocess.run(
            [executable, "-f", str(page_number), "-l", str(page_number), "-layout", str(path), "-"],
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            raise LibraryProviderUnavailableError("No se pudo reprocesar la página solicitada.")
        text = result.stdout.decode("utf-8", errors="replace").strip()
        return self._store_variant(
            source,
            page_number,
            "pdftotext",
            PDFTEXT_VARIANT_VERSION,
            text,
            {"command": "pdftotext -layout", "exit_code": result.returncode},
        )

    async def reprocess_vision(self, source_id: str, page_number: int) -> PageVariantRead:
        renderer = shutil.which("pdftoppm")
        if not renderer:
            raise LibraryProviderUnavailableError("pdftoppm no está disponible localmente.")
        source, path = self._source_path(source_id)
        model = self.settings.educational_library_vision_model
        with tempfile.TemporaryDirectory(dir=self.settings.educational_library_runtime_dir) as tmp:
            prefix = Path(tmp) / "page"
            result = subprocess.run(
                [
                    renderer,
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-png",
                    "-r",
                    "120",
                    str(path),
                    str(prefix),
                ],
                capture_output=True,
                check=False,
                timeout=90,
            )
            images = sorted(Path(tmp).glob("page-*.png"))
            if result.returncode != 0 or len(images) != 1:
                raise LibraryProviderUnavailableError("No se pudo renderizar la página solicitada.")
            if images[0].stat().st_size > 20 * 1024 * 1024:
                raise LibraryContractError("La página renderizada supera el límite seguro.")
            encoded = base64.b64encode(images[0].read_bytes()).decode("ascii")
            try:
                async with httpx.AsyncClient(timeout=180) as client:
                    response = await client.post(
                        f"{self.settings.lm_studio_base_url}/chat/completions",
                        json={
                            "model": model,
                            "stream": False,
                            "temperature": 0,
                            "max_tokens": 2_048,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": (
                                                "Transcribe fielmente esta página. Conserva alemán y "
                                                "español, saltos útiles y signos. No expliques ni "
                                                "completes texto ilegible."
                                            ),
                                        },
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:image/png;base64,{encoded}"
                                            },
                                        },
                                    ],
                                }
                            ],
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise LibraryProviderUnavailableError(
                    "El modelo visual local no pudo transcribir la página."
                ) from exc
        choices = payload.get("choices", []) if isinstance(payload, dict) else []
        text = (
            str(choices[0].get("message", {}).get("content", "")).strip()
            if choices
            else ""
        )
        if not text:
            raise LibraryProviderUnavailableError(
                "El modelo visual devolvió una transcripción vacía."
            )
        return self._store_variant(
            source,
            page_number,
            "vision",
            VISION_VARIANT_VERSION,
            text,
            {
                "model": model,
                "render_dpi": 120,
                "thinking": False,
                "temporary_image_deleted": True,
            },
        )

    def review_variant(self, source_id: str, page_number: int, variant_id: int) -> PageQualityRead:
        with self.database.transaction(immediate=True) as connection:
            source = self._source(connection, source_id)
            variant = connection.execute(
                "SELECT id FROM page_extraction_variants WHERE id=? AND source_version_id=? "
                "AND page_number=?",
                (variant_id, source["current_version_id"], page_number),
            ).fetchone()
            if not variant:
                raise LibraryNotFoundError("La variante de extracción no existe.")
            cursor = connection.execute(
                "UPDATE page_quality SET reviewed_variant_id=?,review_status='user_confirmed',"
                "updated_at=? WHERE source_version_id=? AND page_number=?",
                (variant_id, utc_text(), source["current_version_id"], page_number),
            )
            if cursor.rowcount != 1:
                raise LibraryNotFoundError("La calidad de la página todavía no fue analizada.")
            row = connection.execute(
                "SELECT * FROM page_quality WHERE source_version_id=? AND page_number=?",
                (source["current_version_id"], page_number),
            ).fetchone()
        return self._quality_read(row)

    def _store_variant(
        self,
        source: object,
        page_number: int,
        method: str,
        method_version: str,
        text: str,
        provenance: dict[str, object],
    ) -> PageVariantRead:
        if page_number < 1:
            raise LibraryContractError("El número de página no es válido.")
        metrics = _ratios(text)
        quality_score = {"good": 1.0, "acceptable": 0.72, "poor": 0.35, "unusable": 0.0}[
            str(metrics["quality"])
        ]
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO page_extraction_variants(source_version_id,page_number,"
                "method,method_version,text,text_hash,quality_score,warnings_json,provenance_json,"
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    source["current_version_id"],
                    page_number,
                    method,
                    method_version,
                    text,
                    text_hash,
                    quality_score,
                    json_dump(metrics["warnings"]),
                    json_dump(provenance),
                    utc_text(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM page_extraction_variants WHERE source_version_id=? AND page_number=? "
                "AND method=? AND method_version=? AND text_hash=?",
                (source["current_version_id"], page_number, method, method_version, text_hash),
            ).fetchone()
        return self._variant_read(row)

    def _source_path(self, source_id: str):
        with self.database.connect() as connection:
            source = self._source(connection, source_id)
        root = self.settings.educational_materials_dir.resolve(strict=True)
        path = (root / source["current_path"]).resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise LibraryContractError("La ruta catalogada está fuera de la biblioteca.") from exc
        if path.is_symlink() or not path.is_file():
            raise LibraryContractError("La fuente original no está disponible de forma segura.")
        return source, path

    @staticmethod
    def _source(connection, source_id: str):
        row = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise LibraryNotFoundError("La fuente no existe.")
        return row

    @staticmethod
    def _quality_read(row) -> PageQualityRead:
        return PageQualityRead(
            id=row["id"],
            source_version_id=row["source_version_id"],
            page_number=row["page_number"],
            extraction_method=row["extraction_method"],
            character_count=row["character_count"],
            detected_language=row["detected_language"],
            text_density=row["text_density"],
            replacement_ratio=row["replacement_ratio"],
            weird_character_ratio=row["weird_character_ratio"],
            repeated_line_ratio=row["repeated_line_ratio"],
            ordering_warning=bool(row["ordering_warning"]),
            columns_warning=bool(row["columns_warning"]),
            tables_warning=bool(row["tables_warning"]),
            damaged_german_ratio=row["damaged_german_ratio"],
            quality=row["quality"],
            warnings=json_load(row["warnings_json"], []),
            review_status=row["review_status"],
            reviewed_variant_id=row["reviewed_variant_id"],
        )

    @staticmethod
    def _variant_read(row) -> PageVariantRead:
        return PageVariantRead(
            id=row["id"],
            source_version_id=row["source_version_id"],
            page_number=row["page_number"],
            method=row["method"],
            method_version=row["method_version"],
            text_preview=row["text"][:2_000],
            text_hash=row["text_hash"],
            quality_score=row["quality_score"],
            warnings=json_load(row["warnings_json"], []),
            provenance=json_load(row["provenance_json"], {}),
            created_at=row["created_at"],
        )
