from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LIBRARY_SCHEMA_VERSION = 2

_MIGRATION_0001 = """
CREATE TABLE library_schema (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    current_path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    format TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    mtime_ns INTEGER NOT NULL CHECK(mtime_ns >= 0),
    current_hash TEXT,
    language TEXT,
    secondary_languages_json TEXT NOT NULL DEFAULT '[]',
    cefr_level TEXT,
    topics_json TEXT NOT NULL DEFAULT '[]',
    provenance TEXT,
    author TEXT,
    publisher TEXT,
    publication_year INTEGER,
    rights TEXT NOT NULL DEFAULT 'unknown',
    priority INTEGER NOT NULL DEFAULT 0,
    editorial_confidence REAL NOT NULL DEFAULT 0.5 CHECK(editorial_confidence BETWEEN 0 AND 1),
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    status TEXT NOT NULL,
    processing_state TEXT NOT NULL DEFAULT 'pending',
    duplicate_of_source_id TEXT REFERENCES sources(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    missing_since TEXT,
    excluded INTEGER NOT NULL DEFAULT 0 CHECK(excluded IN (0, 1)),
    current_version_id INTEGER,
    FOREIGN KEY(current_version_id) REFERENCES source_versions(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE source_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id),
    version_number INTEGER NOT NULL CHECK(version_number > 0),
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    mtime_ns INTEGER NOT NULL CHECK(mtime_ns >= 0),
    extractor TEXT,
    extractor_version TEXT,
    processing_state TEXT NOT NULL DEFAULT 'pending',
    error_code TEXT,
    error_detail TEXT,
    statistics_json TEXT NOT NULL DEFAULT '{}',
    previous_version_id INTEGER REFERENCES source_versions(id),
    created_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE(source_id, version_number),
    UNIQUE(source_id, content_hash)
);

CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version_id INTEGER NOT NULL UNIQUE REFERENCES source_versions(id),
    title TEXT,
    author TEXT,
    language TEXT,
    structure_json TEXT NOT NULL DEFAULT '{}',
    extracted_text TEXT NOT NULL DEFAULT '',
    page_count INTEGER,
    duration_seconds REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    extraction_quality REAL NOT NULL DEFAULT 0 CHECK(extraction_quality BETWEEN 0 AND 1),
    needs_ocr INTEGER NOT NULL DEFAULT 0 CHECK(needs_ocr IN (0, 1)),
    needs_transcription INTEGER NOT NULL DEFAULT 0 CHECK(needs_transcription IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT,
    hierarchy_json TEXT NOT NULL DEFAULT '[]',
    text TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    start_seconds REAL,
    end_seconds REAL,
    content_role TEXT NOT NULL DEFAULT 'other',
    UNIQUE(document_id, sequence)
);

CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    section_id INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    sequence INTEGER NOT NULL,
    title TEXT,
    hierarchy_json TEXT NOT NULL DEFAULT '[]',
    text TEXT NOT NULL,
    character_start INTEGER NOT NULL DEFAULT 0,
    character_end INTEGER NOT NULL DEFAULT 0,
    page_start INTEGER,
    page_end INTEGER,
    start_seconds REAL,
    end_seconds REAL,
    token_count INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    language TEXT,
    cefr_level TEXT,
    topics_json TEXT NOT NULL DEFAULT '[]',
    embedding_status TEXT NOT NULL DEFAULT 'pending',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    content_role TEXT NOT NULL DEFAULT 'other',
    created_at TEXT NOT NULL,
    UNIQUE(source_version_id, sequence),
    UNIQUE(source_version_id, content_hash)
);

CREATE VIRTUAL TABLE chunk_fts USING fts5(
    title,
    text,
    content='chunks',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunk_fts(rowid, title, text) VALUES (new.id, coalesce(new.title, ''), new.text);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, title, text)
  VALUES('delete', old.id, coalesce(old.title, ''), old.text);
END;
CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunk_fts(chunk_fts, rowid, title, text)
  VALUES('delete', old.id, coalesce(old.title, ''), old.text);
  INSERT INTO chunk_fts(rowid, title, text) VALUES (new.id, coalesce(new.title, ''), new.text);
END;

CREATE TABLE processing_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    cursor TEXT,
    error_code TEXT,
    error_detail TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1))
);

CREATE TABLE inventory_reports (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES processing_jobs(id),
    generated_at TEXT NOT NULL,
    report_json TEXT NOT NULL
);

CREATE TABLE embeddings (
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    model_version TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK(dimension > 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY(chunk_id, provider, model, model_version)
);

CREATE TABLE knowledge_units (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content_es TEXT NOT NULL,
    german_examples_json TEXT NOT NULL DEFAULT '[]',
    translations_json TEXT NOT NULL DEFAULT '[]',
    cefr_level TEXT NOT NULL,
    topics_json TEXT NOT NULL DEFAULT '[]',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    stale INTEGER NOT NULL DEFAULT 0 CHECK(stale IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(content_hash, prompt_version)
);

CREATE TABLE knowledge_unit_sources (
    knowledge_unit_id TEXT NOT NULL REFERENCES knowledge_units(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id),
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id),
    quote TEXT NOT NULL,
    PRIMARY KEY(knowledge_unit_id, chunk_id)
);

CREATE TABLE knowledge_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_unit_id TEXT NOT NULL REFERENCES knowledge_units(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE grounded_drafts (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    objective TEXT NOT NULL,
    level TEXT NOT NULL,
    explanation_language TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_chunk_ids_json TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'draft',
    evidence_sufficient INTEGER NOT NULL CHECK(evidence_sufficient IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX ix_sources_hash ON sources(current_hash);
CREATE INDEX ix_sources_status ON sources(status, processing_state);
CREATE INDEX ix_source_versions_hash ON source_versions(content_hash);
CREATE INDEX ix_chunks_source_version ON chunks(source_version_id);
CREATE INDEX ix_chunks_role ON chunks(content_role);
CREATE INDEX ix_jobs_state ON processing_jobs(state, priority, created_at);
CREATE INDEX ix_knowledge_status ON knowledge_units(status, stale, confidence);
"""

_MIGRATION_0002 = """
CREATE TABLE teacher_conversations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE teacher_queries (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES teacher_conversations(id) ON DELETE CASCADE,
    parent_query_id TEXT REFERENCES teacher_queries(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    answer_json TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    model TEXT NOT NULL,
    plan_prompt_version TEXT NOT NULL,
    answer_prompt_version TEXT NOT NULL,
    repair_prompt_version TEXT NOT NULL,
    retrieval_mode TEXT NOT NULL,
    semantic_available INTEGER NOT NULL CHECK(semantic_available IN (0, 1)),
    warnings_json TEXT NOT NULL DEFAULT '[]',
    timings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE teacher_query_sources (
    query_id TEXT NOT NULL REFERENCES teacher_queries(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    chunk_id INTEGER NOT NULL REFERENCES chunks(id),
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id),
    retrieval_score REAL NOT NULL,
    matched_queries_json TEXT NOT NULL DEFAULT '[]',
    snippet TEXT NOT NULL,
    PRIMARY KEY(query_id, sequence),
    UNIQUE(query_id, chunk_id)
);

CREATE INDEX ix_teacher_queries_conversation
    ON teacher_queries(conversation_id, created_at);
CREATE INDEX ix_teacher_queries_created ON teacher_queries(created_at DESC);
CREATE INDEX ix_teacher_query_sources_chunk ON teacher_query_sources(chunk_id);
"""


class LibraryDatabase:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def migrate(self) -> int:
        with self.connect() as connection:
            current = self._current_version(connection)
            if current > LIBRARY_SCHEMA_VERSION:
                raise RuntimeError(
                    f"library schema {current} is newer than supported {LIBRARY_SCHEMA_VERSION}"
                )
            if current == 0:
                self._apply_migration(connection, 1, _MIGRATION_0001)
                current = 1
            if current < 2:
                self._apply_migration(connection, 2, _MIGRATION_0002)
            return self._current_version(connection)

    @staticmethod
    def _apply_migration(connection: sqlite3.Connection, version: int, sql: str) -> None:
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + sql)
            connection.execute(
                "INSERT INTO library_schema(version, applied_at) VALUES (?, datetime('now'))",
                (version,),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _current_version(connection: sqlite3.Connection) -> int:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_schema'"
        ).fetchone()
        if not exists:
            return 0
        row = connection.execute("SELECT max(version) AS version FROM library_schema").fetchone()
        return int(row["version"] or 0)

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def integrity(self) -> tuple[str, list[tuple[object, ...]]]:
        with self.connect() as connection:
            quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            foreign = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        return quick, foreign
