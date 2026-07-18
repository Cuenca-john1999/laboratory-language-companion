from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Protocol

import httpx

from .cache import LibraryCache, stable_cache_key
from .database import LibraryDatabase
from .schemas import SearchResponse, SearchResult, SearchTimings
from .service import json_load, utc_text


class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    model_version: str

    async def available(self) -> bool: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def metadata(self) -> tuple[str, str]: ...


class OllamaEmbeddingProvider:
    provider_name = "ollama"
    model_version = "ollama-embed-api.v1"

    def __init__(self, base_url: str, model: str, *, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.timeout = timeout
        self._digest = ""

    async def available(self) -> bool:
        if not self.model_name:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                models = response.json().get("models", [])
                for item in models:
                    if not isinstance(item, dict):
                        continue
                    if (item.get("model") or item.get("name")) == self.model_name:
                        self._digest = str(item.get("digest") or self.model_version)
                        return True
                return False
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model_name, "input": list(texts), "keep_alive": "5m"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("El proveedor local de embeddings no está disponible.") from exc
        embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError("El proveedor local devolvió embeddings inválidos.")
        parsed: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or not vector:
                raise RuntimeError("El proveedor local devolvió un vector vacío.")
            parsed.append([float(value) for value in vector])
        return parsed

    async def metadata(self) -> tuple[str, str]:
        if not self._digest:
            await self.available()
        return self.model_version, self._digest or self.model_version


EMBEDDING_NORMALIZATION_VERSION = "embedding-text.v1"


def _embedding_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _text_hash(text: str) -> str:
    return hashlib.sha256(_embedding_text(text).encode("utf-8")).hexdigest()


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\wÀ-ÿÄÖÜäöüß-]+", query, flags=re.UNICODE)
    if not tokens:
        raise ValueError("La consulta no contiene términos buscables.")
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:20])


def _snippet(text: str, query: str, limit: int = 360) -> str:
    folded = text.casefold()
    positions = [folded.find(token.casefold()) for token in re.findall(r"\w+", query)]
    start_match = min((position for position in positions if position >= 0), default=0)
    start = max(0, start_match - 100)
    end = min(len(text), start + limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return prefix + re.sub(r"\s+", " ", text[start:end]).strip() + suffix


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0
    return numerator / (left_norm * right_norm)


class EducationalSearchService:
    def __init__(
        self,
        database: LibraryDatabase,
        embedding_provider: EmbeddingProvider | None = None,
        *,
        cache: LibraryCache | None = None,
    ):
        self.database = database
        self.embedding_provider = embedding_provider
        self.cache = cache or LibraryCache(database)

    def source_fingerprint(self) -> str:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT s.id,sv.content_hash,s.pedagogical_role,s.priority,s.editorial_status "
                "FROM sources s JOIN source_versions sv ON sv.id=s.current_version_id "
                "WHERE s.status='present' AND s.excluded=0 ORDER BY s.id"
            ).fetchall()
        payload = [tuple(row) for row in rows]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def _provider_metadata(self) -> tuple[str, str]:
        provider = self.embedding_provider
        if provider is None:
            return "", ""
        method = getattr(provider, "metadata", None)
        if method:
            return await method()
        version = str(getattr(provider, "model_version", "unknown"))
        return version, version

    async def _query_vector(self, query: str) -> tuple[list[float], bool]:
        provider = self.embedding_provider
        if provider is None:
            raise RuntimeError("El proveedor local de embeddings no está configurado.")
        model_version, model_digest = await self._provider_metadata()
        normalised = _embedding_text(query)
        config_hash = stable_cache_key(
            provider.provider_name,
            provider.model_name,
            model_version,
            model_digest,
            EMBEDDING_NORMALIZATION_VERSION,
        )
        key = stable_cache_key(normalised, provider.model_name, model_digest)
        cached = self.cache.get(
            "query_embedding",
            key,
            source_fingerprint="query-independent",
            config_hash=config_hash,
        )
        if isinstance(cached, list) and cached:
            return [float(value) for value in cached], True
        vector = (await provider.embed([normalised]))[0]
        self.cache.put(
            "query_embedding",
            key,
            vector,
            source_fingerprint="query-independent",
            config_hash=config_hash,
            model=provider.model_name,
            model_digest=model_digest,
            ttl_seconds=604_800,
        )
        return vector, False

    def _filters(
        self,
        *,
        language: str | None,
        level: str | None,
        source_id: str | None,
        include_solutions: bool,
        role_scope: str = "all",
    ) -> tuple[str, list[object]]:
        clauses = ["s.status='present'", "s.excluded=0", "sv.id=s.current_version_id"]
        parameters: list[object] = []
        if not include_solutions:
            clauses.append("c.content_role!='solution'")
        if language:
            clauses.append("c.language=?")
            parameters.append(language)
        if level:
            clauses.append("c.cefr_level=?")
            parameters.append(level)
        if source_id:
            clauses.append("s.id=?")
            parameters.append(source_id)
        if role_scope == "core":
            clauses.append(
                "s.pedagogical_role IN ('core_theory','core_workbook','core_answer_key')"
            )
        elif role_scope == "supplementary":
            clauses.append(
                "s.pedagogical_role NOT IN ('core_theory','core_workbook','core_answer_key')"
            )
        return " AND ".join(clauses), parameters

    def lexical(
        self,
        query: str,
        *,
        limit: int = 20,
        language: str | None = None,
        level: str | None = None,
        source_id: str | None = None,
        include_solutions: bool = False,
        role_scope: str = "all",
    ) -> list[SearchResult]:
        match = _fts_query(query)
        filters, parameters = self._filters(
            language=language,
            level=level,
            source_id=source_id,
            include_solutions=include_solutions,
            role_scope=role_scope,
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.*,s.id source_id,coalesce(s.display_alias,s.name) source_name,"
                "s.current_path source_path,"
                "s.rights,s.review_status source_review,sv.version_number,s.pedagogical_role,"
                "s.priority source_priority,coalesce(d.extraction_quality,0) extraction_quality,"
                "pq.quality page_quality,bm25(chunk_fts) rank "
                "FROM chunk_fts JOIN chunks c ON c.id=chunk_fts.rowid "
                "JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id LEFT JOIN documents d "
                "ON d.source_version_id=sv.id LEFT JOIN page_quality pq "
                "ON pq.source_version_id=sv.id AND pq.page_number=c.page_start "
                "WHERE chunk_fts MATCH ? AND "
                + filters
                + " AND coalesce(pq.quality,'acceptable')!='unusable'"
                + " ORDER BY rank LIMIT ?",
                [match, *parameters, limit],
            ).fetchall()
        results: list[SearchResult] = []
        for index, row in enumerate(rows):
            lexical_score = 1 / (1 + abs(float(row["rank"])))
            results.append(
                self._result(
                    row,
                    query=query,
                    lexical_score=lexical_score,
                    semantic_score=None,
                    combined_score=1 / (60 + index + 1),
                )
            )
        return results

    async def semantic(
        self,
        query: str,
        *,
        limit: int = 20,
        language: str | None = None,
        level: str | None = None,
        source_id: str | None = None,
        include_solutions: bool = False,
        role_scope: str = "all",
    ) -> tuple[list[SearchResult], bool, int, int, int]:
        provider = self.embedding_provider
        if provider is None or not await provider.available():
            return [], False, 0, 0, 0
        embedding_started = perf_counter()
        query_vector, cache_hit = await self._query_vector(query)
        embedding_ms = round((perf_counter() - embedding_started) * 1_000)
        model_version, model_digest = await self._provider_metadata()
        filters, parameters = self._filters(
            language=language,
            level=level,
            source_id=source_id,
            include_solutions=include_solutions,
            role_scope=role_scope,
        )
        vector_started = perf_counter()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.*,s.id source_id,coalesce(s.display_alias,s.name) source_name,"
                "s.current_path source_path,"
                "s.rights,s.review_status source_review,sv.version_number,s.pedagogical_role,"
                "s.priority source_priority,coalesce(d.extraction_quality,0) extraction_quality,"
                "pq.quality page_quality,e.vector_json "
                "FROM embeddings e JOIN chunks c ON c.id=e.chunk_id "
                "JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id LEFT JOIN documents d "
                "ON d.source_version_id=sv.id LEFT JOIN page_quality pq "
                "ON pq.source_version_id=sv.id AND pq.page_number=c.page_start "
                "WHERE e.provider=? AND e.model=? AND e.model_version=? AND e.model_digest=? "
                "AND e.status='indexed' AND e.source_version_id=c.source_version_id "
                "AND e.normalization_version=? "
                "AND coalesce(pq.quality,'acceptable')!='unusable' "
                "AND " + filters,
                [
                    provider.provider_name,
                    provider.model_name,
                    model_version,
                    model_digest,
                    EMBEDDING_NORMALIZATION_VERSION,
                    *parameters,
                ],
            ).fetchall()
        scored = []
        for row in rows:
            vector = json_load(row["vector_json"], [])
            if isinstance(vector, list):
                score = _cosine(query_vector, [float(value) for value in vector])
                scored.append((score, row))
        vector_ms = round((perf_counter() - vector_started) * 1_000)
        ranking_started = perf_counter()
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        results = [
            self._result(
                row,
                query=query,
                lexical_score=None,
                semantic_score=score,
                combined_score=1 / (60 + index + 1),
            )
            for index, (score, row) in enumerate(scored[:limit])
        ]
        ranking_ms = round((perf_counter() - ranking_started) * 1_000)
        return results, cache_hit, embedding_ms, vector_ms, ranking_ms

    async def search(
        self,
        query: str,
        *,
        mode: str = "lexical",
        limit: int = 20,
        language: str | None = None,
        level: str | None = None,
        source_id: str | None = None,
        include_solutions: bool = False,
        role_scope: str = "all",
    ) -> SearchResponse:
        total_started = perf_counter()
        lexical_started = perf_counter()
        lexical = self.lexical(
            query,
            limit=max(limit, 30),
            language=language,
            level=level,
            source_id=source_id,
            include_solutions=include_solutions,
            role_scope=role_scope,
        )
        fts_ms = round((perf_counter() - lexical_started) * 1_000)
        semantic_available = bool(
            self.embedding_provider and await self.embedding_provider.available()
        )
        if mode == "lexical":
            return SearchResponse(
                query=query,
                requested_mode="lexical",
                effective_mode="lexical",
                semantic_available=semantic_available,
                results=lexical[:limit],
                timings=SearchTimings(
                    fts_ms=fts_ms,
                    total_ms=round((perf_counter() - total_started) * 1_000),
                ),
            )
        if not semantic_available:
            return SearchResponse(
                query=query,
                requested_mode=mode,
                effective_mode="lexical",
                semantic_available=False,
                results=lexical[:limit],
                warning="La búsqueda semántica local no está configurada; se usó FTS5.",
                timings=SearchTimings(
                    fts_ms=fts_ms,
                    total_ms=round((perf_counter() - total_started) * 1_000),
                ),
            )
        (
            semantic,
            query_cache_hit,
            embedding_ms,
            vector_ms,
            semantic_ranking_ms,
        ) = await self.semantic(
            query,
            limit=max(limit, 30),
            language=language,
            level=level,
            source_id=source_id,
            include_solutions=include_solutions,
            role_scope=role_scope,
        )
        if mode == "semantic":
            return SearchResponse(
                query=query,
                requested_mode="semantic",
                effective_mode="semantic",
                semantic_available=True,
                results=semantic[:limit],
                query_embedding_cache_hit=query_cache_hit,
                timings=SearchTimings(
                    fts_ms=fts_ms,
                    query_embedding_ms=embedding_ms,
                    vector_ms=vector_ms,
                    ranking_ms=semantic_ranking_ms,
                    total_ms=round((perf_counter() - total_started) * 1_000),
                ),
            )
        ranking_started = perf_counter()
        scores: dict[int, float] = {}
        by_id: dict[int, SearchResult] = {}
        origins = (("fts", lexical), ("vector", semantic))
        for origin, ranking in origins:
            for rank, result in enumerate(ranking, start=1):
                scores[result.id] = scores.get(result.id, 0) + 1 / (60 + rank)
                previous = by_id.get(result.id)
                existing_origins = previous.retrieval_origins if previous else []
                by_id[result.id] = result.model_copy(
                    update={"retrieval_origins": list(dict.fromkeys([*existing_origins, origin]))}
                )
        for chunk_id, result in by_id.items():
            role_factor = {
                "core_theory": 1.22,
                "core_workbook": 1.14,
                "core_answer_key": 0.92,
                "supplementary": 1.0,
                "reference": 0.98,
                "glossary": 1.06,
                "answer_key": 0.86,
                "unknown": 0.96,
            }.get(result.pedagogical_role.value, 0.96)
            priority_factor = 1 + max(-0.1, min(0.1, result.source_priority / 1_000))
            quality_factor = 0.8 + 0.2 * result.extraction_quality
            scores[chunk_id] *= role_factor * priority_factor * quality_factor
        ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
        hybrid = [
            by_id[chunk_id].model_copy(update={"combined_score": scores[chunk_id]})
            for chunk_id in ordered
        ]
        ranking_ms = semantic_ranking_ms + round((perf_counter() - ranking_started) * 1_000)
        return SearchResponse(
            query=query,
            requested_mode="hybrid",
            effective_mode="hybrid",
            semantic_available=True,
            results=hybrid,
            core_results=sum(
                result.pedagogical_role.value.startswith("core_") for result in hybrid
            ),
            supplementary_results=sum(
                not result.pedagogical_role.value.startswith("core_") for result in hybrid
            ),
            query_embedding_cache_hit=query_cache_hit,
            timings=SearchTimings(
                fts_ms=fts_ms,
                query_embedding_ms=embedding_ms,
                vector_ms=vector_ms,
                ranking_ms=ranking_ms,
                total_ms=round((perf_counter() - total_started) * 1_000),
            ),
        )

    async def index_embeddings(
        self,
        *,
        batch_size: int = 16,
        limit: int | None = None,
        core_first: bool = True,
        retry_failed: bool = False,
        checkpoint: Callable[[int, int], bool] | None = None,
    ) -> int:
        provider = self.embedding_provider
        if provider is None or not await provider.available():
            return 0
        model_version, model_digest = await self._provider_metadata()
        processed = 0
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE chunks SET embedding_status='excluded' WHERE id IN ("
                "SELECT c.id FROM chunks c JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id WHERE sv.id=s.current_version_id AND "
                "(s.status!='present' OR s.excluded=1 OR s.duplicate_of_source_id IS NOT NULL "
                "OR length(trim(c.text))<20 OR c.content_role IN ('solution','index')))"
            )
        while limit is None or processed < limit:
            current_limit = min(batch_size, limit - processed) if limit is not None else batch_size
            with self.database.connect() as connection:
                rows = connection.execute(
                    "SELECT c.id,c.text,c.content_hash,c.source_version_id FROM chunks c "
                    "JOIN source_versions sv ON sv.id=c.source_version_id "
                    "JOIN sources s ON s.id=sv.source_id LEFT JOIN embeddings e ON e.chunk_id=c.id "
                    "AND e.provider=? AND e.model=? AND e.model_version=? "
                    "WHERE sv.id=s.current_version_id AND s.status='present' AND s.excluded=0 "
                    "AND s.duplicate_of_source_id IS NULL AND length(trim(c.text))>=20 "
                    "AND c.content_role NOT IN ('solution','index') AND (e.chunk_id IS NULL "
                    "OR coalesce(e.source_version_id,-1)!=c.source_version_id "
                    "OR coalesce(e.model_digest,'')!=? "
                    "OR e.normalization_version!=? OR e.status='stale' "
                    + ("OR e.status='failed' " if retry_failed else "")
                    + ") ORDER BY "
                    + (
                        "CASE WHEN s.pedagogical_role='core_theory' THEN 0 "
                        "WHEN s.pedagogical_role='core_workbook' THEN 1 ELSE 2 END,"
                        if core_first
                        else ""
                    )
                    + "s.priority DESC,c.id LIMIT ?",
                    (
                        provider.provider_name,
                        provider.model_name,
                        model_version,
                        model_digest,
                        EMBEDDING_NORMALIZATION_VERSION,
                        current_limit,
                    ),
                ).fetchall()
            if not rows:
                break
            texts = [_embedding_text(row["text"]) for row in rows]
            try:
                vectors = await provider.embed(texts)
            except Exception as exc:
                with self.database.transaction(immediate=True) as connection:
                    for row in rows:
                        connection.execute(
                            "INSERT INTO embeddings(chunk_id,provider,model,model_version,"
                            "vector_json,dimension,created_at,source_version_id,model_digest,"
                            "text_hash,normalization_version,chunk_quality,status,error_code,"
                            "error_detail,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(chunk_id,provider,model,model_version) DO UPDATE SET "
                            "status='failed',error_code=excluded.error_code,"
                            "error_detail=excluded.error_detail,updated_at=excluded.updated_at",
                            (
                                row["id"],
                                provider.provider_name,
                                provider.model_name,
                                model_version,
                                "[]",
                                1,
                                utc_text(),
                                row["source_version_id"],
                                model_digest,
                                _text_hash(row["text"]),
                                EMBEDDING_NORMALIZATION_VERSION,
                                1,
                                "failed",
                                type(exc).__name__,
                                str(exc)[:300],
                                utc_text(),
                            ),
                        )
                        connection.execute(
                            "UPDATE chunks SET embedding_status='failed' WHERE id=?", (row["id"],)
                        )
                raise
            dimensions = {len(vector) for vector in vectors}
            if len(dimensions) != 1 or 0 in dimensions:
                with self.database.transaction(immediate=True) as connection:
                    for row in rows:
                        connection.execute(
                            "INSERT INTO embeddings(chunk_id,provider,model,model_version,"
                            "vector_json,dimension,created_at,source_version_id,model_digest,"
                            "text_hash,normalization_version,chunk_quality,status,error_code,"
                            "error_detail,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(chunk_id,provider,model,model_version) DO UPDATE SET "
                            "status='failed',error_code=excluded.error_code,"
                            "error_detail=excluded.error_detail,updated_at=excluded.updated_at",
                            (
                                row["id"],
                                provider.provider_name,
                                provider.model_name,
                                model_version,
                                "[]",
                                1,
                                utc_text(),
                                row["source_version_id"],
                                model_digest,
                                _text_hash(row["text"]),
                                EMBEDDING_NORMALIZATION_VERSION,
                                1,
                                "failed",
                                "EmbeddingDimensionError",
                                "El lote contiene dimensiones incompatibles.",
                                utc_text(),
                            ),
                        )
                        connection.execute(
                            "UPDATE chunks SET embedding_status='failed' WHERE id=?",
                            (row["id"],),
                        )
                raise RuntimeError("El modelo local devolvió dimensiones incompatibles.")
            dimension = next(iter(dimensions))
            with self.database.transaction(immediate=True) as connection:
                for row, vector in zip(rows, vectors, strict=True):
                    connection.execute(
                        "INSERT INTO embeddings(chunk_id,provider,model,model_version,vector_json,"
                        "dimension,created_at,source_version_id,model_digest,text_hash,"
                        "normalization_version,chunk_quality,status,error_code,error_detail,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chunk_id,provider,model,"
                        "model_version) DO UPDATE SET vector_json=excluded.vector_json,"
                        "dimension=excluded.dimension,source_version_id=excluded.source_version_id,"
                        "model_digest=excluded.model_digest,text_hash=excluded.text_hash,"
                        "normalization_version=excluded.normalization_version,"
                        "chunk_quality=excluded.chunk_quality,status='indexed',error_code=NULL,"
                        "error_detail=NULL,updated_at=excluded.updated_at",
                        (
                            row["id"],
                            provider.provider_name,
                            provider.model_name,
                            model_version,
                            json.dumps(vector, separators=(",", ":")),
                            dimension,
                            utc_text(),
                            row["source_version_id"],
                            model_digest,
                            _text_hash(row["text"]),
                            EMBEDDING_NORMALIZATION_VERSION,
                            1,
                            "indexed",
                            None,
                            None,
                            utc_text(),
                        ),
                    )
                    connection.execute(
                        "UPDATE chunks SET embedding_status='indexed' WHERE id=?", (row["id"],)
                    )
            processed += len(rows)
            if checkpoint and checkpoint(
                processed, self.embedding_candidate_count(model_digest=model_digest)
            ):
                break
        return processed

    def embedding_candidate_count(self, *, model_digest: str | None = None) -> int:
        provider = self.embedding_provider
        if provider is None:
            return 0
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT count(*) FROM chunks c JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id LEFT JOIN embeddings e ON e.chunk_id=c.id "
                "AND e.provider=? AND e.model=? AND e.model_version=? WHERE sv.id=s.current_version_id "
                "AND s.status='present' AND s.excluded=0 AND s.duplicate_of_source_id IS NULL "
                "AND length(trim(c.text))>=20 AND c.content_role NOT IN ('solution','index') "
                "AND (e.chunk_id IS NULL OR coalesce(e.source_version_id,-1)!=c.source_version_id "
                "OR coalesce(e.model_digest,'')!=? "
                "OR e.normalization_version!=? OR e.status='stale')",
                (
                    provider.provider_name,
                    provider.model_name,
                    provider.model_version,
                    model_digest or str(getattr(provider, "_digest", "")),
                    EMBEDDING_NORMALIZATION_VERSION,
                ),
            ).fetchone()
        return int(row[0] or 0)

    @staticmethod
    def _result(
        row: sqlite3.Row,
        *,
        query: str,
        lexical_score: float | None,
        semantic_score: float | None,
        combined_score: float,
    ) -> SearchResult:
        return SearchResult(
            id=row["id"],
            source_id=row["source_id"],
            source_version_id=row["source_version_id"],
            source_name=row["source_name"],
            source_path=row["source_path"],
            text=row["text"],
            title=row["title"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            start_seconds=row["start_seconds"],
            end_seconds=row["end_seconds"],
            level=row["cefr_level"],
            topics=json_load(row["topics_json"], []),
            review_status=row["source_review"],
            rights=row["rights"],
            source_version=row["version_number"],
            content_role=row["content_role"],
            lexical_score=lexical_score,
            semantic_score=semantic_score,
            combined_score=combined_score,
            snippet=_snippet(row["text"], query),
            pedagogical_role=row["pedagogical_role"],
            source_priority=row["source_priority"],
            extraction_quality=row["extraction_quality"],
            page_quality=row["page_quality"],
        )
