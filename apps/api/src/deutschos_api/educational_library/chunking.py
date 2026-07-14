from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .schemas import ExtractedSection

CHUNKER_VERSION = "pedagogical-chunker.v1"
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ¿¡])")
_GERMAN_MARKERS = {
    "der",
    "die",
    "das",
    "und",
    "ist",
    "nicht",
    "ein",
    "eine",
    "ich",
    "du",
    "sie",
    "wir",
    "deutsch",
    "aufgabe",
    "übung",
}
_SPANISH_MARKERS = {
    "el",
    "la",
    "los",
    "las",
    "una",
    "que",
    "para",
    "alemán",
    "ejercicio",
    "explicación",
    "gramática",
}
_TOPICS = {
    "grammar": re.compile(
        r"\b(grammatik|gramática|verb|artikel|pronomen|kasus|nominativ|akkusativ)\b", re.I
    ),
    "vocabulary": re.compile(r"\b(wortschatz|vokabel|vocabulario|glossar|glosario)\b", re.I),
    "writing": re.compile(r"\b(schreiben|escritura|brief|e-mail|correo)\b", re.I),
    "reading": re.compile(r"\b(lesen|lectura|textverständnis|comprensión)\b", re.I),
    "listening": re.compile(r"\b(hören|escucha|hörverstehen)\b", re.I),
    "speaking": re.compile(r"\b(sprechen|pronunciación|aussprache|mündlich)\b", re.I),
    "laboratory": re.compile(r"\b(labor|laboratorio|mikroskop|probe|reagenz)\b", re.I),
    "exam": re.compile(r"\b(prüfung|test|examen|zertifikat|goethe|telc)\b", re.I),
}


@dataclass(frozen=True)
class ChunkDraft:
    sequence: int
    section_sequence: int
    title: str | None
    hierarchy: list[str]
    text: str
    character_start: int
    character_end: int
    page_start: int | None
    page_end: int | None
    start_seconds: float | None
    end_seconds: float | None
    token_count: int
    content_hash: str
    language: str
    cefr_level: str
    topics: list[str]
    content_role: str


def detect_language(text: str) -> str:
    words = {word.casefold() for word in re.findall(r"[A-Za-zÀ-ÿÄÖÜäöüß]+", text[:8_000])}
    german = len(words & _GERMAN_MARKERS)
    spanish = len(words & _SPANISH_MARKERS)
    if german >= 2 and spanish >= 2:
        return "de-es"
    if german > spanish and german >= 2:
        return "de"
    if spanish > german and spanish >= 2:
        return "es"
    return "unknown"


def estimate_cefr(text: str, language: str) -> str:
    if language not in {"de", "de-es"}:
        return "unknown"
    words = re.findall(r"[A-Za-zÄÖÜäöüß]+", text)
    if not words:
        return "unknown"
    sentences = max(1, len(re.findall(r"[.!?](?:\s|$)", text)))
    average_sentence = len(words) / sentences
    subordinate = len(
        re.findall(r"\b(weil|obwohl|während|damit|sodass|dennoch|hingegen)\b", text, re.I)
    )
    if average_sentence <= 8 and subordinate == 0:
        return "A1"
    if average_sentence <= 12 and subordinate <= 1:
        return "A2"
    if average_sentence <= 18 and subordinate <= 3:
        return "B1"
    return "B2"


def detect_topics(text: str, title: str | None) -> list[str]:
    sample = f"{title or ''}\n{text[:10_000]}"
    return [topic for topic, pattern in _TOPICS.items() if pattern.search(sample)]


def _limit_for_role(role: str) -> int:
    return {
        "glossary": 900,
        "exercise": 900,
        "solution": 700,
        "transcript": 1_100,
        "example": 1_000,
    }.get(role, 1_500)


def _units(text: str) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= 1_500:
            units.append(paragraph)
        else:
            units.extend(
                item.strip() for item in _SENTENCE_BOUNDARY.split(paragraph) if item.strip()
            )
    return units


def _window(text: str, limit: int, overlap: int = 160) -> list[tuple[int, int, str]]:
    if len(text) <= limit:
        return [(0, len(text), text)]
    windows: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            boundary = max(text.rfind(" ", start + limit // 2, end), text.rfind("\n", start, end))
            if boundary > start:
                end = boundary
        body = text[start:end].strip()
        if body:
            windows.append((start, end, body))
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return windows


def chunk_sections(sections: list[ExtractedSection]) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    seen_hashes: set[str] = set()
    for section in sections:
        limit = _limit_for_role(section.content_role)
        grouped: list[str] = []
        current = ""
        for unit in _units(section.text):
            if not current:
                current = unit
            elif len(current) + 2 + len(unit) <= limit:
                current = f"{current}\n\n{unit}"
            else:
                grouped.append(current)
                current = unit
        if current:
            grouped.append(current)
        if not grouped and section.text.strip():
            grouped = [section.text.strip()]

        offset = 0
        for group in grouped:
            group_start = section.text.find(group, offset)
            if group_start < 0:
                group_start = offset
            for local_start, local_end, body in _window(group, limit):
                absolute_start = group_start + local_start
                absolute_end = group_start + local_end
                digest = hashlib.sha256(
                    (
                        f"{section.sequence}\0{section.title or ''}\0{section.page_start}\0"
                        f"{section.start_seconds}\0{body}"
                    ).encode()
                ).hexdigest()
                if digest in seen_hashes:
                    continue
                seen_hashes.add(digest)
                language = detect_language(body)
                chunks.append(
                    ChunkDraft(
                        sequence=len(chunks),
                        section_sequence=section.sequence,
                        title=section.title,
                        hierarchy=list(section.hierarchy),
                        text=body,
                        character_start=absolute_start,
                        character_end=absolute_end,
                        page_start=section.page_start,
                        page_end=section.page_end,
                        start_seconds=section.start_seconds,
                        end_seconds=section.end_seconds,
                        token_count=len(re.findall(r"\S+", body)),
                        content_hash=digest,
                        language=language,
                        cefr_level=estimate_cefr(body, language),
                        topics=detect_topics(body, section.title),
                        content_role=section.content_role,
                    )
                )
            offset = group_start + len(group)
    return chunks
