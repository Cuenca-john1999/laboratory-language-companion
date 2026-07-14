from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Sequence
from typing import Protocol

import httpx

from .database import LibraryDatabase
from .schemas import SearchResponse, SearchResult
from .service import json_load, utc_text


class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    model_version: str

    async def available(self) -> bool: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OllamaEmbeddingProvider:
    provider_name = "ollama"
    model_version = "ollama-embed-api.v1"

    def __init__(self, base_url: str, model: str, *, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.timeout = timeout

    async def available(self) -> bool:
        if not self.model_name:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                models = response.json().get("models", [])
                return self.model_name in {
                    item.get("model") or item.get("name")
                    for item in models
                    if isinstance(item, dict)
                }
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model_name, "input": list(texts)},
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
        self, database: LibraryDatabase, embedding_provider: EmbeddingProvider | None = None
    ):
        self.database = database
        self.embedding_provider = embedding_provider

    def _filters(
        self,
        *,
        language: str | None,
        level: str | None,
        source_id: str | None,
        include_solutions: bool,
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
    ) -> list[SearchResult]:
        match = _fts_query(query)
        filters, parameters = self._filters(
            language=language,
            level=level,
            source_id=source_id,
            include_solutions=include_solutions,
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.*,s.id source_id,s.name source_name,s.current_path source_path,"
                "s.rights,s.review_status source_review,sv.version_number,bm25(chunk_fts) rank "
                "FROM chunk_fts JOIN chunks c ON c.id=chunk_fts.rowid "
                "JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id WHERE chunk_fts MATCH ? AND "
                + filters
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
    ) -> list[SearchResult]:
        provider = self.embedding_provider
        if provider is None or not await provider.available():
            return []
        query_vector = (await provider.embed([query]))[0]
        filters, parameters = self._filters(
            language=language,
            level=level,
            source_id=source_id,
            include_solutions=include_solutions,
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.*,s.id source_id,s.name source_name,s.current_path source_path,"
                "s.rights,s.review_status source_review,sv.version_number,e.vector_json "
                "FROM embeddings e JOIN chunks c ON c.id=e.chunk_id "
                "JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id WHERE e.provider=? AND e.model=? "
                "AND e.model_version=? AND " + filters,
                [provider.provider_name, provider.model_name, provider.model_version, *parameters],
            ).fetchall()
        scored = []
        for row in rows:
            vector = json_load(row["vector_json"], [])
            if isinstance(vector, list):
                score = _cosine(query_vector, [float(value) for value in vector])
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        return [
            self._result(
                row,
                query=query,
                lexical_score=None,
                semantic_score=score,
                combined_score=1 / (60 + index + 1),
            )
            for index, (score, row) in enumerate(scored[:limit])
        ]

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
    ) -> SearchResponse:
        lexical = self.lexical(
            query,
            limit=max(limit, 30),
            language=language,
            level=level,
            source_id=source_id,
            include_solutions=include_solutions,
        )
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
            )
        if not semantic_available:
            return SearchResponse(
                query=query,
                requested_mode=mode,
                effective_mode="lexical",
                semantic_available=False,
                results=lexical[:limit],
                warning="La búsqueda semántica local no está configurada; se usó FTS5.",
            )
        semantic = await self.semantic(
            query,
            limit=max(limit, 30),
            language=language,
            level=level,
            source_id=source_id,
            include_solutions=include_solutions,
        )
        if mode == "semantic":
            return SearchResponse(
                query=query,
                requested_mode="semantic",
                effective_mode="semantic",
                semantic_available=True,
                results=semantic[:limit],
            )
        scores: dict[int, float] = {}
        by_id: dict[int, SearchResult] = {}
        for ranking in (lexical, semantic):
            for rank, result in enumerate(ranking, start=1):
                scores[result.id] = scores.get(result.id, 0) + 1 / (60 + rank)
                by_id[result.id] = result
        ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
        hybrid = [
            by_id[chunk_id].model_copy(update={"combined_score": scores[chunk_id]})
            for chunk_id in ordered
        ]
        return SearchResponse(
            query=query,
            requested_mode="hybrid",
            effective_mode="hybrid",
            semantic_available=True,
            results=hybrid,
        )

    async def index_embeddings(self, *, batch_size: int = 16, limit: int | None = None) -> int:
        provider = self.embedding_provider
        if provider is None or not await provider.available():
            return 0
        processed = 0
        while limit is None or processed < limit:
            current_limit = min(batch_size, limit - processed) if limit is not None else batch_size
            with self.database.connect() as connection:
                rows = connection.execute(
                    "SELECT c.id,c.text FROM chunks c JOIN source_versions sv ON sv.id=c.source_version_id "
                    "JOIN sources s ON s.id=sv.source_id LEFT JOIN embeddings e ON e.chunk_id=c.id "
                    "AND e.provider=? AND e.model=? AND e.model_version=? "
                    "WHERE sv.id=s.current_version_id AND s.status='present' AND e.chunk_id IS NULL "
                    "ORDER BY c.id LIMIT ?",
                    (
                        provider.provider_name,
                        provider.model_name,
                        provider.model_version,
                        current_limit,
                    ),
                ).fetchall()
            if not rows:
                break
            vectors = await provider.embed([row["text"] for row in rows])
            with self.database.transaction(immediate=True) as connection:
                for row, vector in zip(rows, vectors, strict=True):
                    connection.execute(
                        "INSERT OR REPLACE INTO embeddings(chunk_id,provider,model,model_version,"
                        "vector_json,dimension,created_at) VALUES (?,?,?,?,?,?,?)",
                        (
                            row["id"],
                            provider.provider_name,
                            provider.model_name,
                            provider.model_version,
                            json.dumps(vector, separators=(",", ":")),
                            len(vector),
                            utc_text(),
                        ),
                    )
                    connection.execute(
                        "UPDATE chunks SET embedding_status='indexed' WHERE id=?", (row["id"],)
                    )
            processed += len(rows)
        return processed

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
        )
