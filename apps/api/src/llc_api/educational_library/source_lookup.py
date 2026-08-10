from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from time import perf_counter

from .cache import LibraryCache, stable_cache_key
from .database import LibraryDatabase
from .memory import PedagogicalMemoryService, normalize_concept
from .schemas import (
    EvidenceLocationCreate,
    EvidenceLocationRead,
    EvidenceRegion,
    PedagogicalConceptCreate,
    PedagogicalConceptSummary,
    PedagogicalMemoryStatus,
    SourceLookupConceptRead,
    SourceLookupLocationRead,
    SourceLookupMode,
    SourceLookupRead,
    SourceLookupStatus,
)
from .search import EducationalSearchService

SOURCE_LOOKUP_VERSION = "deterministic-source-lookup.v1"
MAX_VISIBLE_LOCATIONS = 3
MAX_EXPANDED_LOCATIONS = 10

_LOOKUP_PATTERNS = (
    re.compile(
        r"\ben que (?:pagina|parte|libro|capitulo)(?: de [\w.-]+)?\s+"
        r"(?:aparecen?|esta|se explica|se encuentra)?\s*(?P<target>.+)",
    ),
    re.compile(
        r"\bdonde (?:aparecen?|esta|se encuentran?|explica)\s+(?P<target>.+)",
    ),
    re.compile(r"\bmuestrame la seccion de\s+(?P<target>.+)"),
    re.compile(r"\blocaliza\s+(?P<target>.+)"),
    re.compile(r"\bbusca la tabla de\s+(?P<target>.+)"),
    re.compile(r"\bbusca mas ubicaciones de\s+(?P<target>.+)"),
    re.compile(r"\bdonde puedo leer sobre\s+(?P<target>.+)"),
    re.compile(r"\bwo steht\s+(?P<target>.+)"),
    re.compile(r"\bauf welcher seite steht\s+(?P<target>.+)"),
    re.compile(r"\bin welchem kapitel steht\s+(?P<target>.+)"),
)
_EXPLANATION_PATTERNS = (
    re.compile(r"\bque es\b"),
    re.compile(r"\bpor que\b"),
    re.compile(r"\bcual es la diferencia\b"),
    re.compile(r"\bexplicame\b"),
    re.compile(r"\bwas ist\b"),
    re.compile(r"\bwarum\b"),
    re.compile(r"\berklare(?: mir)?\b"),
)
_INCOMPLETE_LOOKUP_PATTERNS = (
    re.compile(r"\bdonde (?:aparecen?|esta|se encuentran?|explica)\s*$"),
    re.compile(r"\ben que (?:pagina|parte|libro|capitulo)(?: de [\w.-]+)?\s*$"),
    re.compile(
        r"\b(?:muestrame la seccion de|localiza|busca la tabla de|"
        r"busca mas ubicaciones de)\s*$"
    ),
    re.compile(r"\b(?:wo steht|auf welcher seite steht|in welchem kapitel steht)\s*$"),
)
_TARGET_TRAILING_SOURCE = re.compile(r"\s+(?:en|de|del|im|in)\s+(?:el\s+)?(?:manual\s+)?herder\s*$")
_TARGET_LEADING_SOURCE = re.compile(
    r"^(?:(?:en|de|del|im|in)\s+)?(?:el\s+)?(?:manual\s+)?herder(?:\s+|$)"
)
_TARGET_ARTICLE = re.compile(r"^(?:el|la|los|las|der|die|das)\s+")
_TARGET_STOPWORDS = {
    "aparece",
    "capitulo",
    "donde",
    "esta",
    "herder",
    "libro",
    "pagina",
    "parte",
    "seccion",
    "sobre",
}


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_marks_removed = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", ascii_marks_removed).strip(" \t\n\r?.!¡¿")


@dataclass(frozen=True)
class SourceLookupDetection:
    mode: SourceLookupMode
    target: str | None


@dataclass(frozen=True)
class SourceLookupExecution:
    detection: SourceLookupDetection
    result: SourceLookupRead
    concept_ids: tuple[str, ...]
    location_ids: tuple[str, ...]
    retrieval_mode: str
    semantic_available: bool
    detection_ms: int
    memory_ms: int
    hybrid_ms: int


def detect_source_lookup(
    question: str,
    *,
    parent_target: str | None = None,
) -> SourceLookupDetection:
    folded = _fold(question)
    target: str | None = None
    matched = False
    for pattern in _LOOKUP_PATTERNS:
        match = pattern.search(folded)
        if not match:
            continue
        matched = True
        target = match.groupdict().get("target")
        break
    if not matched:
        if any(pattern.search(folded) for pattern in _INCOMPLETE_LOOKUP_PATTERNS):
            if parent_target:
                return SourceLookupDetection(SourceLookupMode.PURE, parent_target)
            return SourceLookupDetection(SourceLookupMode.AMBIGUOUS, None)
        return SourceLookupDetection(SourceLookupMode.NONE, None)
    explanatory = any(pattern.search(folded) for pattern in _EXPLANATION_PATTERNS)
    if target:
        target = _TARGET_TRAILING_SOURCE.sub("", target)
        target = _TARGET_LEADING_SOURCE.sub("", target)
        target = _TARGET_ARTICLE.sub("", target).strip(" ,.;:?!")
    if not target or set(target.split()) <= _TARGET_STOPWORDS:
        target = parent_target
    mode = SourceLookupMode.MIXED if explanatory else SourceLookupMode.PURE
    if not target and not parent_target and not explanatory:
        mode = SourceLookupMode.AMBIGUOUS
    return SourceLookupDetection(mode, target or parent_target)


class DeterministicSourceLookupService:
    def __init__(
        self,
        database: LibraryDatabase,
        search: EducationalSearchService,
        *,
        memory: PedagogicalMemoryService | None = None,
        cache: LibraryCache | None = None,
    ):
        self.database = database
        self.search = search
        self.memory = memory or PedagogicalMemoryService(database)
        self.cache = cache or LibraryCache(database)

    def refresh_persisted(self, result: SourceLookupRead) -> SourceLookupRead:
        """Rebuild mutable public citations from current structured memory."""
        concept_id = result.concept.concept_id if result.concept else None
        concepts: list[PedagogicalConceptSummary] = []
        if concept_id:
            try:
                concepts = [self.memory.get_concept(concept_id)]
            except Exception:
                concepts = []
        return self._repair_cached_result(
            result,
            concepts,
            None,
            max_locations=max(MAX_VISIBLE_LOCATIONS, len(result.locations)),
        )

    async def resolve(
        self,
        question: str,
        *,
        source_id: str | None = None,
        parent_target: str | None = None,
        detection: SourceLookupDetection | None = None,
        expanded: bool = False,
    ) -> SourceLookupExecution:
        detection_started = perf_counter()
        detection = detection or detect_source_lookup(question, parent_target=parent_target)
        detection_ms = self._elapsed_ms(detection_started)
        memory_started = perf_counter()
        concepts = self._matched_concepts(question, detection.target)
        source_ids = self._source_ids(question, source_id)
        fingerprint = self._fingerprint()
        cache_key = stable_cache_key(
            SOURCE_LOOKUP_VERSION,
            normalize_concept(question),
            detection.target,
            source_ids,
            expanded,
        )
        cached = self.cache.get(
            "source_lookup",
            cache_key,
            source_fingerprint=fingerprint,
            config_hash=SOURCE_LOOKUP_VERSION,
        )
        if isinstance(cached, dict):
            cached_result = SourceLookupRead.model_validate(cached.get("result", {}))
            repaired = self._repair_cached_result(
                cached_result,
                concepts,
                source_ids,
                max_locations=(MAX_EXPANDED_LOCATIONS if expanded else MAX_VISIBLE_LOCATIONS),
            )
            repaired = repaired.model_copy(update={"lookup_cache_hit": True})
            return SourceLookupExecution(
                detection=detection,
                result=repaired,
                concept_ids=tuple(str(value) for value in cached.get("concept_ids", [])),
                location_ids=tuple(location.location_id for location in repaired.locations),
                retrieval_mode=str(cached.get("retrieval_mode", "lexical")),
                semantic_available=bool(cached.get("semantic_available", False)),
                detection_ms=detection_ms,
                memory_ms=self._elapsed_ms(memory_started),
                hybrid_ms=0,
            )

        locations = self.memory.lookup_locations(
            [concept.id for concept in concepts], source_ids=source_ids
        )
        memory_ms = self._elapsed_ms(memory_started)
        max_locations = MAX_EXPANDED_LOCATIONS if expanded else MAX_VISIBLE_LOCATIONS
        result = self._result(
            concepts,
            locations,
            hybrid_ids=set(),
            max_locations=max_locations,
        )
        retrieval_mode = "lexical"
        semantic_available = False
        hybrid_ms = 0
        if expanded or result.status not in {
            SourceLookupStatus.VERIFIED_LOCATION,
            SourceLookupStatus.MULTIPLE_LOCATIONS,
            SourceLookupStatus.CONFLICT,
        }:
            hybrid_started = perf_counter()
            try:
                created_ids, retrieval_mode, semantic_available = await self._hybrid_candidates(
                    question,
                    detection.target,
                    concepts,
                    source_ids,
                )
                if created_ids:
                    concepts = self._matched_concepts(question, detection.target)
                    locations = self.memory.lookup_locations(
                        [concept.id for concept in concepts], source_ids=source_ids
                    )
                result = self._result(
                    concepts,
                    locations,
                    hybrid_ids=created_ids,
                    max_locations=max_locations,
                )
                result = result.model_copy(update={"hybrid_fallback": True})
            except Exception:
                if not locations:
                    result = SourceLookupRead(
                        status=SourceLookupStatus.RETRIEVAL_ERROR,
                        concept=self._concept_read(concepts[0]) if concepts else None,
                        summary=(
                            "No pude consultar las ubicaciones documentales en este momento. "
                            "La memoria local no fue modificada."
                        ),
                        warnings=["Falló la recuperación local de ubicaciones."],
                        hybrid_fallback=True,
                    )
                else:
                    result = result.model_copy(
                        update={
                            "warnings": [
                                *result.warnings,
                                "No se pudo ampliar la búsqueda; se muestran los candidatos guardados.",
                            ],
                            "hybrid_fallback": True,
                        }
                    )
            hybrid_ms = self._elapsed_ms(hybrid_started)

        result = result.model_copy(
            update={
                "memory_hit": bool(locations),
                "lookup_cache_hit": False,
            }
        )
        if result.status != SourceLookupStatus.RETRIEVAL_ERROR:
            final_fingerprint = self._fingerprint()
            self.cache.put(
                "source_lookup",
                cache_key,
                {
                    "result": result.model_dump(mode="json"),
                    "concept_ids": [concept.id for concept in concepts],
                    "retrieval_mode": retrieval_mode,
                    "semantic_available": semantic_available,
                },
                source_fingerprint=final_fingerprint,
                config_hash=SOURCE_LOOKUP_VERSION,
                prompt_version=SOURCE_LOOKUP_VERSION,
            )
        return SourceLookupExecution(
            detection=detection,
            result=result,
            concept_ids=tuple(concept.id for concept in concepts),
            location_ids=tuple(location.location_id for location in result.locations),
            retrieval_mode=retrieval_mode,
            semantic_available=semantic_available,
            detection_ms=detection_ms,
            memory_ms=memory_ms,
            hybrid_ms=hybrid_ms,
        )

    def _matched_concepts(
        self,
        question: str,
        target: str | None,
    ) -> list[PedagogicalConceptSummary]:
        matches = self.memory.match_concepts(" ".join(filter(None, [question, target])))
        rank = {
            PedagogicalMemoryStatus.USER_CONFIRMED: 0,
            PedagogicalMemoryStatus.SYSTEM_VERIFIED: 1,
            PedagogicalMemoryStatus.CANDIDATE: 2,
            PedagogicalMemoryStatus.CONFLICT: 3,
            PedagogicalMemoryStatus.REJECTED: 4,
            PedagogicalMemoryStatus.STALE: 5,
        }
        matches.sort(
            key=lambda concept: (
                rank[concept.status],
                -sum(concept.location_counts.values()),
                concept.canonical_name.casefold(),
                concept.id,
            )
        )
        unique: list[PedagogicalConceptSummary] = []
        seen: set[str] = set()
        for concept in matches:
            normalized = normalize_concept(concept.canonical_name)
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(concept)
        return unique[:3]

    def _source_ids(self, question: str, explicit_source_id: str | None) -> list[str] | None:
        if explicit_source_id:
            return [explicit_source_id]
        normalized = normalize_concept(question)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id,name,display_alias,canonical_title,pedagogical_role FROM sources "
                "WHERE status='present' AND excluded=0 ORDER BY CASE pedagogical_role "
                "WHEN 'core_theory' THEN 0 WHEN 'core_workbook' THEN 1 ELSE 2 END,priority DESC,id"
            ).fetchall()
        matched: list[str] = []
        for row in rows:
            values = " ".join(
                str(row[key] or "") for key in ("name", "display_alias", "canonical_title")
            )
            source_tokens = set(normalize_concept(values).split())
            if "herder" in normalized.split() and "herder" in source_tokens:
                matched.append(str(row["id"]))
                continue
            aliases = [
                normalize_concept(str(row[key] or ""))
                for key in ("display_alias", "canonical_title")
                if row[key]
            ]
            if any(len(alias) >= 4 and alias in normalized for alias in aliases):
                matched.append(str(row["id"]))
        return matched or None

    async def _hybrid_candidates(
        self,
        question: str,
        target: str | None,
        concepts: list[PedagogicalConceptSummary],
        source_ids: list[str] | None,
    ) -> tuple[set[str], str, bool]:
        query = target or question
        explicit_source = source_ids[0] if source_ids and len(source_ids) == 1 else None
        response = await self.search.search(
            query,
            mode="hybrid",
            source_id=explicit_source,
            limit=10,
            include_solutions=False,
            role_scope="all",
        )
        results = [
            item
            for item in response.results
            if (not source_ids or item.source_id in source_ids)
            and item.page_quality != "unusable"
            and self._search_result_is_relevant(item, query)
        ][:3]
        if not results:
            return set(), response.effective_mode, response.semantic_available
        if not concepts:
            concept = self.memory.create_concept(
                PedagogicalConceptCreate(
                    canonical_name=(target or query)[:200],
                    language="de",
                    category="other",
                    status=PedagogicalMemoryStatus.CANDIDATE,
                    origin="query_detected",
                )
            )
            concepts = [concept]
        concept_id = concepts[0].id
        created_ids: set[str] = set()
        for item in results:
            if self._is_rejected(concept_id, item.source_version_id, item.id, item.page_start):
                continue
            location = self.memory.create_location(
                EvidenceLocationCreate(
                    concept_id=concept_id,
                    source_version_id=item.source_version_id,
                    chunk_id=item.id,
                    pdf_page_number=item.page_start,
                    region=EvidenceRegion.UNKNOWN,
                    heading=item.title,
                    evidence_snippet=item.snippet,
                    extraction_quality=item.extraction_quality,
                    status=PedagogicalMemoryStatus.CANDIDATE,
                    origin="source_lookup_hybrid",
                )
            )
            created_ids.add(location.id)
        return created_ids, response.effective_mode, response.semantic_available

    @staticmethod
    def _search_result_is_relevant(item: object, query: str) -> bool:
        lexical_score = getattr(item, "lexical_score", None)
        if lexical_score is not None:
            return True
        searchable = normalize_concept(
            f"{getattr(item, 'title', '') or ''} {getattr(item, 'snippet', '') or ''}"
        )
        query_tokens = {
            token
            for token in normalize_concept(query).split()
            if len(token) >= 4 and token not in _TARGET_STOPWORDS
        }
        return bool(query_tokens & set(searchable.split()))

    def _is_rejected(
        self,
        concept_id: str,
        source_version_id: int,
        chunk_id: int,
        pdf_page: int | None,
    ) -> bool:
        with self.database.connect() as connection:
            return bool(
                connection.execute(
                    "SELECT 1 FROM pedagogical_evidence_locations WHERE concept_id=? "
                    "AND source_version_id=? AND ifnull(chunk_id,-1)=ifnull(?,-1) "
                    "AND ifnull(pdf_page_number,-1)=ifnull(?,-1) AND status='rejected' LIMIT 1",
                    (concept_id, source_version_id, chunk_id, pdf_page),
                ).fetchone()
            )

    def _repair_cached_result(
        self,
        cached: SourceLookupRead,
        concepts: list[PedagogicalConceptSummary],
        source_ids: list[str] | None,
        *,
        max_locations: int,
    ) -> SourceLookupRead:
        allowed_ids = {location.location_id for location in cached.locations}
        locations = [
            location
            for location in self.memory.lookup_locations(
                [concept.id for concept in concepts], source_ids=source_ids
            )
            if location.id in allowed_ids
        ]
        provenance = {location.location_id: location.provenance for location in cached.locations}
        repaired = self._result(
            concepts,
            locations,
            hybrid_ids=set(),
            max_locations=max_locations,
        )
        repaired_locations = [
            location.model_copy(
                update={"provenance": provenance.get(location.location_id, "memory")}
            )
            for location in repaired.locations
        ]
        return repaired.model_copy(
            update={
                "locations": repaired_locations,
                "memory_hit": bool(locations),
                "hybrid_fallback": cached.hybrid_fallback,
                "lookup_cache_hit": cached.lookup_cache_hit,
                "used_generation": cached.used_generation,
            }
        )

    def _result(
        self,
        concepts: list[PedagogicalConceptSummary],
        locations: list[EvidenceLocationRead],
        *,
        hybrid_ids: set[str],
        max_locations: int = MAX_VISIBLE_LOCATIONS,
    ) -> SourceLookupRead:
        concept = concepts[0] if concepts else None
        current = [
            location
            for location in locations
            if location.source_current and location.source_present
        ]
        trusted = [
            location
            for location in current
            if location.status
            in {
                PedagogicalMemoryStatus.USER_CONFIRMED,
                PedagogicalMemoryStatus.SYSTEM_VERIFIED,
            }
        ]
        candidates = [
            location for location in current if location.status == PedagogicalMemoryStatus.CANDIDATE
        ]
        conflicts = [
            location for location in current if location.status == PedagogicalMemoryStatus.CONFLICT
        ]
        stale = [
            location
            for location in locations
            if location.status == PedagogicalMemoryStatus.STALE
            or not location.source_current
            or not location.source_present
        ]
        if len(trusted) > 1:
            status = SourceLookupStatus.MULTIPLE_LOCATIONS
            visible = [*trusted, *candidates]
        elif trusted:
            status = SourceLookupStatus.VERIFIED_LOCATION
            visible = [*trusted, *candidates]
        elif conflicts:
            status = SourceLookupStatus.CONFLICT
            visible = conflicts
        elif candidates:
            status = SourceLookupStatus.CANDIDATE_LOCATIONS
            visible = candidates
        elif stale:
            status = SourceLookupStatus.STALE
            visible = stale
        else:
            status = SourceLookupStatus.NO_LOCATION
            visible = []
        visible = visible[:max_locations]
        public_locations = [
            self._public_location(location, hybrid_ids=hybrid_ids) for location in visible
        ]
        warnings: list[str] = []
        if conflicts and trusted:
            warnings.append("También existen ubicaciones en conflicto pendientes de revisión.")
        if len(locations) > len(public_locations):
            warnings.append(
                f"Hay {len(locations) - len(public_locations)} ubicaciones adicionales en la memoria."
            )
        return SourceLookupRead(
            status=status,
            concept=self._concept_read(concept) if concept else None,
            summary=self._summary(status, concept, visible),
            locations=public_locations,
            available_location_count=len(
                [
                    location
                    for location in locations
                    if location.status != PedagogicalMemoryStatus.REJECTED
                ]
            ),
            warnings=warnings,
            memory_hit=bool(locations),
            hybrid_fallback=bool(hybrid_ids),
        )

    @staticmethod
    def _concept_read(concept: PedagogicalConceptSummary) -> SourceLookupConceptRead:
        return SourceLookupConceptRead(
            concept_id=concept.id,
            canonical_name=concept.canonical_name,
            display_name_es=concept.display_name_es,
            display_name_de=concept.display_name_de,
        )

    @staticmethod
    def _public_location(
        location: EvidenceLocationRead,
        *,
        hybrid_ids: set[str],
    ) -> SourceLookupLocationRead:
        return SourceLookupLocationRead(
            location_id=location.id,
            source_id=location.source_id,
            source_name=location.source_name,
            source_role=location.source_role,
            source_version=location.source_version,
            pdf_page=location.pdf_page_number,
            printed_page=location.printed_page_label,
            scan_layout=location.scan_layout,
            region=location.region,
            heading=location.heading,
            review_status=location.status,
            citation=location.public_citation,
            snippet=location.evidence_snippet,
            provenance="hybrid" if location.id in hybrid_ids else "memory",
        )

    @classmethod
    def _summary(
        cls,
        status: SourceLookupStatus,
        concept: PedagogicalConceptSummary | None,
        locations: list[EvidenceLocationRead],
    ) -> str:
        name = (
            concept.display_name_es or concept.canonical_name if concept else "el tema solicitado"
        )
        name = name[:1].lower() + name[1:] if name else "el tema solicitado"
        if status == SourceLookupStatus.NO_LOCATION:
            return "Todavía no tengo una ubicación fiable guardada para este tema."
        if status == SourceLookupStatus.RETRIEVAL_ERROR:
            return "No pude consultar las ubicaciones documentales en este momento."
        if status == SourceLookupStatus.STALE:
            return (
                "La ubicación conocida pertenece a una versión anterior del archivo y necesita "
                "revalidación."
            )
        if status == SourceLookupStatus.CONFLICT:
            return (
                "Hay ubicaciones incompatibles y todavía no puedo presentar una como definitiva. "
                "Puedes revisarlas en Memoria verificada."
            )
        if status == SourceLookupStatus.MULTIPLE_LOCATIONS:
            verified_count = sum(
                location.status
                in {
                    PedagogicalMemoryStatus.USER_CONFIRMED,
                    PedagogicalMemoryStatus.SYSTEM_VERIFIED,
                }
                for location in locations
            )
            return (
                f"He encontrado {verified_count} ubicaciones verificadas sobre {name}. "
                f"La principal es {locations[0].public_citation}."
            )
        location = locations[0]
        if status == SourceLookupStatus.CANDIDATE_LOCATIONS:
            page = (
                f"la página {location.pdf_page_number} del PDF"
                if location.pdf_page_number
                else "una ubicación documental"
            )
            return (
                f"He encontrado una posible ubicación para {name} en {page}, "
                "pero todavía no ha sido verificada."
            )
        source = (
            "el manual Herder"
            if location.source_role.value == "core_theory"
            and "herder" in location.source_name.casefold()
            else f"la fuente {location.source_name}"
        )
        page = (
            f"en la página {location.pdf_page_number} del PDF"
            if location.pdf_page_number
            else "en una ubicación documental"
        )
        sentences = [f"El {name} aparece en {source} {page}."]
        if location.scan_layout.value == "double_page":
            sentences.append("La página corresponde a un escaneo doble.")
        unknown_printed = location.pdf_page_number and not location.printed_page_label
        unknown_region = location.region == EvidenceRegion.UNKNOWN
        if unknown_printed and unknown_region:
            sentences.append(
                "La numeración impresa y la mitad concreta todavía no están identificadas."
            )
        elif unknown_printed:
            sentences.append("La numeración impresa todavía no está identificada.")
        elif unknown_region:
            sentences.append("La región concreta todavía no está identificada.")
        if location.status == PedagogicalMemoryStatus.USER_CONFIRMED:
            sentences.append("Esta ubicación fue confirmada por ti.")
        else:
            sentences.append("Esta ubicación fue verificada automáticamente.")
        return " ".join(sentences)

    def _fingerprint(self) -> str:
        with self.database.connect() as connection:
            payload = {
                "concepts": [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT id,editorial_status,updated_at FROM pedagogical_concepts ORDER BY id"
                    )
                ],
                "aliases": [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT id,concept_id,normalized_text,status,updated_at "
                        "FROM pedagogical_concept_aliases ORDER BY id"
                    )
                ],
                "locations": [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT id,concept_id,source_version_id,chunk_id,pdf_page_number,"
                        "region_kind,status,updated_at FROM pedagogical_evidence_locations ORDER BY id"
                    )
                ],
                "mappings": [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT id,mapping_version,mapping_status,updated_at "
                        "FROM document_page_mappings ORDER BY id"
                    )
                ],
                "sources": [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT id,current_version_id,current_hash,status,excluded FROM sources ORDER BY id"
                    )
                ],
            }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1_000))
