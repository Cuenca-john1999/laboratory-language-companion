from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from .database import LibraryDatabase
from .service import json_dump, json_load, utc_text


def stable_cache_key(*parts: object) -> str:
    payload = json_dump(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class LibraryCache:
    def __init__(self, database: LibraryDatabase, *, ttl_seconds: int = 86_400):
        self.database = database
        self.ttl_seconds = ttl_seconds

    def get(
        self,
        namespace: str,
        cache_key: str,
        *,
        source_fingerprint: str,
        config_hash: str,
    ) -> object | None:
        now = utc_text()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM library_cache WHERE namespace=? AND cache_key=? "
                "AND source_fingerprint=? AND config_hash=? "
                "AND (expires_at IS NULL OR expires_at>?)",
                (namespace, cache_key, source_fingerprint, config_hash, now),
            ).fetchone()
        return json_load(row["value_json"], None) if row else None

    def put(
        self,
        namespace: str,
        cache_key: str,
        value: object,
        *,
        source_fingerprint: str,
        config_hash: str,
        model: str | None = None,
        model_digest: str | None = None,
        prompt_version: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        expires = None
        if ttl > 0:
            expires = utc_text(datetime.now(UTC) + timedelta(seconds=ttl))
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO library_cache(namespace,cache_key,value_json,source_fingerprint,"
                "model,model_digest,prompt_version,config_hash,created_at,expires_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(namespace,cache_key) DO UPDATE SET "
                "value_json=excluded.value_json,source_fingerprint=excluded.source_fingerprint,"
                "model=excluded.model,model_digest=excluded.model_digest,"
                "prompt_version=excluded.prompt_version,config_hash=excluded.config_hash,"
                "created_at=excluded.created_at,expires_at=excluded.expires_at",
                (
                    namespace,
                    cache_key,
                    json_dump(value),
                    source_fingerprint,
                    model,
                    model_digest,
                    prompt_version,
                    config_hash,
                    utc_text(),
                    expires,
                ),
            )

    def invalidate(self, namespace: str | None = None) -> int:
        with self.database.transaction(immediate=True) as connection:
            if namespace is None:
                cursor = connection.execute("DELETE FROM library_cache")
            else:
                cursor = connection.execute(
                    "DELETE FROM library_cache WHERE namespace=?", (namespace,)
                )
        return max(0, cursor.rowcount)
