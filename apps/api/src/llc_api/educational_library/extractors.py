from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from llc_api.core.config import Settings

from .inventory import InventoryEntry
from .schemas import ExtractedSection, ExtractionResult, ProcessingState, SourceKind

EXTRACTOR_VERSION = "educational-extractors.v1"
_TIMESTAMP = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{3})")
_ROLE_PATTERNS = {
    "solution": re.compile(r"\b(lösung(?:en)?|soluci[oó]n(?:es)?|answer key|respuestas)\b", re.I),
    "exercise": re.compile(
        r"\b(übung(?:en)?|aufgabe(?:n)?|ejercicio(?:s)?|exercise(?:s)?)\b", re.I
    ),
    "glossary": re.compile(r"\b(glossar|glosario|wortschatz|vokabeln)\b", re.I),
    "index": re.compile(r"\b(inhaltsverzeichnis|índice|index|contents)\b", re.I),
    "example": re.compile(r"\b(beispiel(?:e)?|ejemplo(?:s)?|example(?:s)?)\b", re.I),
}


def _role(title: str | None, text: str) -> str:
    sample = f"{title or ''}\n{text[:500]}"
    for role, pattern in _ROLE_PATTERNS.items():
        if pattern.search(sample):
            return role
    return "theory" if len(text.strip()) > 80 else "other"


def _decode(data: bytes, limit: int) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)[:limit]
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")[:limit]


def _plain_sections(text: str, *, markdown: bool = False) -> list[ExtractedSection]:
    sections: list[ExtractedSection] = []
    title: str | None = None
    hierarchy: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if body or title:
            sections.append(
                ExtractedSection(
                    sequence=len(sections),
                    kind="section" if title else "document",
                    title=title,
                    hierarchy=hierarchy,
                    text=body,
                    content_role=_role(title, body),
                )
            )
        buffer = []

    for line in text.splitlines():
        heading: str | None = None
        depth = 1
        if markdown and (match := re.match(r"^(#{1,6})\s+(.+?)\s*$", line)):
            depth = len(match.group(1))
            heading = match.group(2).strip()
        elif line.strip() and len(line.strip()) < 100 and line.strip().isupper():
            heading = line.strip()
        if heading:
            flush()
            title = heading
            hierarchy = [*hierarchy[: depth - 1], heading]
        else:
            buffer.append(line)
    flush()
    if not sections:
        sections.append(ExtractedSection(sequence=0, kind="document", text=text))
    return sections


class _StructuredHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[str | None, str]] = []
        self._title: str | None = None
        self._buffer: list[str] = []
        self._ignored_depth = 0
        self._heading_tag: str | None = None
        self._heading_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in {"script", "style", "nav", "noscript", "svg"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
            self._heading_tag = tag
            self._heading_buffer = []
        elif not self._ignored_depth and tag in {"p", "li", "br", "tr", "div"}:
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "nav", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._heading_tag == tag:
            self._title = " ".join(self._heading_buffer).strip() or None
            self._heading_tag = None
        elif not self._ignored_depth and tag in {"p", "li", "tr", "div"}:
            self._buffer.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._heading_tag:
            self._heading_buffer.append(data)
        else:
            self._buffer.append(data)

    def _flush(self) -> None:
        text = re.sub(r"[ \t]+", " ", "".join(self._buffer))
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        if text or self._title:
            self.sections.append((self._title, text))
        self._buffer = []

    def finish(self) -> list[tuple[str | None, str]]:
        self._flush()
        return self.sections


def _html_sections(html: str) -> list[ExtractedSection]:
    parser = _StructuredHTMLParser()
    parser.feed(html)
    return [
        ExtractedSection(
            sequence=index,
            kind="section",
            title=title,
            hierarchy=[title] if title else [],
            text=text,
            content_role=_role(title, text),
        )
        for index, (title, text) in enumerate(parser.finish())
        if title or text
    ]


def _text(path: Path, settings: Settings) -> ExtractionResult:
    data = path.read_bytes()
    text = _decode(data, settings.educational_library_max_text_characters)
    sections = _plain_sections(text, markdown=path.suffix.lower() in {".md", ".markdown"})
    return ExtractionResult(
        title=sections[0].title if sections else path.stem,
        sections=sections,
        extraction_quality=1 if text.strip() else 0,
        processing_state=ProcessingState.PROCESSED if text.strip() else ProcessingState.PARTIAL,
        extractor="plain_text",
        extractor_version=EXTRACTOR_VERSION,
    )


def _html(path: Path, settings: Settings) -> ExtractionResult:
    text = _decode(path.read_bytes(), settings.educational_library_max_text_characters)
    sections = _html_sections(text)
    return ExtractionResult(
        title=next((item.title for item in sections if item.title), path.stem),
        sections=sections,
        extraction_quality=0.9 if sections else 0,
        processing_state=ProcessingState.PROCESSED if sections else ProcessingState.PARTIAL,
        extractor="html_parser",
        extractor_version=EXTRACTOR_VERSION,
    )


def _pdf_info(path: Path) -> dict[str, str]:
    executable = shutil.which("pdfinfo")
    if not executable:
        return {}
    result = subprocess.run(
        [executable, str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return {}
    metadata: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip().lower().replace(" ", "_")] = value.strip()
    return metadata


def _pdf(path: Path, settings: Settings) -> ExtractionResult:
    executable = shutil.which("pdftotext")
    if not executable:
        return ExtractionResult(
            title=path.stem,
            processing_state=ProcessingState.UNSUPPORTED,
            extractor="pdftotext_unavailable",
            extractor_version=EXTRACTOR_VERSION,
        )
    metadata = _pdf_info(path)
    declared_pages = int(metadata.get("pages", "0") or 0)
    page_limit = min(
        declared_pages or settings.educational_library_max_pdf_pages,
        settings.educational_library_max_pdf_pages,
    )
    settings.ensure_educational_library_directory()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="pdf-",
            suffix=".txt",
            dir=settings.educational_library_runtime_dir,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        result = subprocess.run(
            [
                executable,
                "-f",
                "1",
                "-l",
                str(max(1, page_limit)),
                str(path),
                str(temporary_path),
            ],
            capture_output=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("pdf_text_extraction_failed")
        with temporary_path.open("rb") as extracted:
            data = extracted.read(settings.educational_library_max_text_characters * 4 + 1)
        text = _decode(data, settings.educational_library_max_text_characters)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    sections = [
        ExtractedSection(
            sequence=index,
            kind="page",
            title=f"Página {index + 1}",
            text=page.strip(),
            page_start=index + 1,
            page_end=index + 1,
            content_role=_role(None, page),
        )
        for index, page in enumerate(pages)
        if page.strip()
    ]
    page_count = declared_pages or len(pages)
    density = len(text.strip()) / max(1, min(page_count, page_limit))
    needs_ocr = page_count > 0 and (not sections or density < 40)
    state = ProcessingState.NEEDS_OCR if needs_ocr else ProcessingState.PROCESSED
    if declared_pages > settings.educational_library_max_pdf_pages:
        state = ProcessingState.PARTIAL
    return ExtractionResult(
        title=metadata.get("title") or path.stem,
        author=metadata.get("author") or None,
        sections=sections,
        page_count=page_count,
        metadata={**metadata, "text_density_per_processed_page": density},
        extraction_quality=min(1, density / 800),
        needs_ocr=needs_ocr,
        processing_state=state,
        extractor="pdftotext",
        extractor_version=EXTRACTOR_VERSION,
    )


def _docx(path: Path, settings: Settings) -> ExtractionResult:
    with zipfile.ZipFile(path) as archive:
        document = ElementTree.fromstring(
            _safe_zip_read(
                archive,
                "word/document.xml",
                settings.educational_library_max_text_characters * 4,
            )
        )
        core: dict[str, str] = {}
        if "docProps/core.xml" in archive.namelist():
            core_root = ElementTree.fromstring(
                _safe_zip_read(archive, "docProps/core.xml", 2_000_000)
            )
            for element in core_root.iter():
                if element.text and "}" in element.tag:
                    core[element.tag.split("}")[-1]] = element.text
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    sections: list[ExtractedSection] = []
    current_title: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        text = "\n".join(buffer).strip()
        if text or current_title:
            sections.append(
                ExtractedSection(
                    sequence=len(sections),
                    kind="section",
                    title=current_title,
                    hierarchy=[current_title] if current_title else [],
                    text=text,
                    content_role=_role(current_title, text),
                )
            )
        buffer = []

    for paragraph in document.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
        if not text:
            continue
        style = paragraph.find("./w:pPr/w:pStyle", namespace)
        style_value = style.get(f"{{{namespace['w']}}}val", "") if style is not None else ""
        if style_value.lower().startswith(("heading", "titel", "título")):
            flush()
            current_title = text
        else:
            buffer.append(text)
        if (
            sum(len(item.text) for item in sections) + sum(map(len, buffer))
            > settings.educational_library_max_text_characters
        ):
            break
    flush()
    return ExtractionResult(
        title=core.get("title") or path.stem,
        author=core.get("creator"),
        language=core.get("language"),
        sections=sections,
        metadata=core,
        extraction_quality=0.95 if sections else 0,
        processing_state=ProcessingState.PROCESSED if sections else ProcessingState.PARTIAL,
        extractor="docx_xml",
        extractor_version=EXTRACTOR_VERSION,
    )


def _epub(path: Path, settings: Settings) -> ExtractionResult:
    with zipfile.ZipFile(path) as archive:
        container = ElementTree.fromstring(
            _safe_zip_read(archive, "META-INF/container.xml", 2_000_000)
        )
        rootfile = next(
            element.attrib["full-path"]
            for element in container.iter()
            if element.tag.endswith("rootfile")
        )
        opf = ElementTree.fromstring(_safe_zip_read(archive, rootfile, 8_000_000))
        base = Path(rootfile).parent
        manifest = {
            element.attrib["id"]: element.attrib["href"]
            for element in opf.iter()
            if element.tag.endswith("item") and "id" in element.attrib and "href" in element.attrib
        }
        spine = [
            element.attrib["idref"]
            for element in opf.iter()
            if element.tag.endswith("itemref") and "idref" in element.attrib
        ]
        metadata: dict[str, str] = {}
        for element in opf.iter():
            name = element.tag.split("}")[-1]
            if name in {"title", "creator", "language", "publisher", "date"} and element.text:
                metadata[name] = element.text.strip()
        sections: list[ExtractedSection] = []
        total = 0
        for item_id in spine:
            href = manifest.get(item_id)
            if not href:
                continue
            member = (base / href).as_posix()
            try:
                remaining = settings.educational_library_max_text_characters - total
                html = _decode(_safe_zip_read(archive, member, remaining * 4), remaining)
            except KeyError:
                continue
            for title, text in _StructuredHTMLParserFromText.parse(html):
                if not text and not title:
                    continue
                sections.append(
                    ExtractedSection(
                        sequence=len(sections),
                        kind="chapter",
                        title=title,
                        hierarchy=[title] if title else [],
                        text=text,
                        content_role=_role(title, text),
                    )
                )
                total += len(text)
                if total >= settings.educational_library_max_text_characters:
                    break
            if total >= settings.educational_library_max_text_characters:
                break
    return ExtractionResult(
        title=metadata.get("title") or path.stem,
        author=metadata.get("creator"),
        language=metadata.get("language"),
        sections=sections,
        metadata=metadata,
        extraction_quality=0.9 if sections else 0,
        processing_state=ProcessingState.PROCESSED if sections else ProcessingState.PARTIAL,
        extractor="epub_spine",
        extractor_version=EXTRACTOR_VERSION,
    )


class _StructuredHTMLParserFromText:
    @staticmethod
    def parse(html: str) -> list[tuple[str | None, str]]:
        parser = _StructuredHTMLParser()
        parser.feed(html)
        return parser.finish()


def _safe_zip_read(archive: zipfile.ZipFile, member: str, max_bytes: int) -> bytes:
    info = archive.getinfo(member)
    if info.file_size > max_bytes:
        raise ValueError("archive_member_exceeds_extraction_limit")
    if info.compress_size and info.file_size / info.compress_size > 1_000:
        raise ValueError("archive_member_compression_ratio_exceeds_limit")
    with archive.open(info) as source:
        payload = source.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError("archive_member_exceeds_extraction_limit")
    return payload


def _seconds(value: str) -> float:
    match = _TIMESTAMP.search(value)
    if not match:
        raise ValueError("invalid_subtitle_timestamp")
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("ms")) / 1000
    )


def _subtitle(path: Path, settings: Settings) -> ExtractionResult:
    text = _decode(path.read_bytes(), settings.educational_library_max_text_characters)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: list[ExtractedSection] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line == "WEBVTT" or line.isdigit():
            index += 1
            continue
        if "-->" not in line:
            index += 1
            continue
        start_raw, end_raw = line.split("-->", 1)
        start = _seconds(start_raw)
        end = _seconds(end_raw)
        index += 1
        buffer: list[str] = []
        while index < len(lines) and lines[index].strip():
            clean = re.sub(r"<[^>]+>", "", lines[index]).strip()
            if clean:
                buffer.append(clean)
            index += 1
        body = " ".join(buffer)
        if body:
            sections.append(
                ExtractedSection(
                    sequence=len(sections),
                    kind="subtitle",
                    text=body,
                    start_seconds=start,
                    end_seconds=end,
                    content_role="transcript",
                )
            )
    return ExtractionResult(
        title=path.stem,
        sections=sections,
        duration_seconds=max((section.end_seconds or 0 for section in sections), default=0),
        extraction_quality=1 if sections else 0,
        processing_state=ProcessingState.PROCESSED if sections else ProcessingState.PARTIAL,
        extractor="subtitle_parser",
        extractor_version=EXTRACTOR_VERSION,
    )


def _ffprobe(path: Path) -> dict[str, object]:
    executable = shutil.which("ffprobe")
    if not executable:
        return {}
    result = subprocess.run(
        [executable, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _media(path: Path) -> ExtractionResult:
    metadata = _ffprobe(path)
    format_data = metadata.get("format", {})
    duration_raw = format_data.get("duration") if isinstance(format_data, dict) else None
    try:
        duration = float(duration_raw) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration = None
    return ExtractionResult(
        title=path.stem,
        duration_seconds=duration,
        metadata=metadata,
        needs_transcription=True,
        processing_state=ProcessingState.AWAITING_TRANSCRIBER,
        extractor="ffprobe" if metadata else "media_registry",
        extractor_version=EXTRACTOR_VERSION,
    )


def _image(path: Path) -> ExtractionResult:
    return ExtractionResult(
        title=path.stem,
        metadata={"format": path.suffix.lower()},
        needs_ocr=True,
        processing_state=ProcessingState.NEEDS_OCR,
        extractor="image_registry",
        extractor_version=EXTRACTOR_VERSION,
    )


def _unsupported(path: Path) -> ExtractionResult:
    return ExtractionResult(
        title=path.stem,
        processing_state=ProcessingState.UNSUPPORTED,
        extractor="unsupported_registry",
        extractor_version=EXTRACTOR_VERSION,
    )


def extract(entry: InventoryEntry, settings: Settings) -> ExtractionResult:
    if entry.kind in {SourceKind.AUDIO, SourceKind.VIDEO}:
        return _media(entry.path)
    if entry.kind == SourceKind.IMAGE:
        return _image(entry.path)
    if entry.size_bytes > settings.educational_library_max_extract_bytes:
        return ExtractionResult(
            title=entry.path.stem,
            metadata={"reason": "file_exceeds_extraction_limit", "size_bytes": entry.size_bytes},
            processing_state=ProcessingState.PARTIAL,
            extractor="size_limited_registry",
            extractor_version=EXTRACTOR_VERSION,
            needs_transcription=entry.kind in {SourceKind.AUDIO, SourceKind.VIDEO},
        )
    suffix = entry.extension
    if suffix in {".txt", ".md", ".markdown"}:
        return _text(entry.path, settings)
    if suffix in {".html", ".htm"}:
        return _html(entry.path, settings)
    if suffix == ".pdf":
        return _pdf(entry.path, settings)
    if suffix == ".docx":
        return _docx(entry.path, settings)
    if suffix == ".epub":
        return _epub(entry.path, settings)
    if suffix in {".srt", ".vtt"}:
        return _subtitle(entry.path, settings)
    return _unsupported(entry.path)


def supported_extensions() -> Iterable[str]:
    return (
        ".txt",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".pdf",
        ".docx",
        ".epub",
        ".srt",
        ".vtt",
    )
