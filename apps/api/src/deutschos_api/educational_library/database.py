from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

LIBRARY_SCHEMA_VERSION = 8

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

_MIGRATION_0003 = """
ALTER TABLE sources ADD COLUMN canonical_title TEXT;
ALTER TABLE sources ADD COLUMN display_alias TEXT;
ALTER TABLE sources ADD COLUMN edition TEXT;
ALTER TABLE sources ADD COLUMN cefr_min TEXT;
ALTER TABLE sources ADD COLUMN cefr_max TEXT;
ALTER TABLE sources ADD COLUMN pedagogical_role TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE sources ADD COLUMN editorial_status TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE sources ADD COLUMN user_selected_core INTEGER NOT NULL DEFAULT 0
    CHECK(user_selected_core IN (0, 1));
ALTER TABLE sources ADD COLUMN metadata_origin TEXT NOT NULL DEFAULT 'scanner';
ALTER TABLE sources ADD COLUMN metadata_confidence REAL NOT NULL DEFAULT 0.5
    CHECK(metadata_confidence BETWEEN 0 AND 1);
ALTER TABLE sources ADD COLUMN editorial_notes TEXT;
ALTER TABLE sources ADD COLUMN related_source_id TEXT REFERENCES sources(id);

ALTER TABLE sections ADD COLUMN cefr_min TEXT;
ALTER TABLE sections ADD COLUMN cefr_max TEXT;
ALTER TABLE sections ADD COLUMN topic TEXT;
ALTER TABLE sections ADD COLUMN extraction_method TEXT NOT NULL DEFAULT 'source_extractor';
ALTER TABLE sections ADD COLUMN provenance_confidence REAL NOT NULL DEFAULT 0.5
    CHECK(provenance_confidence BETWEEN 0 AND 1);
ALTER TABLE sections ADD COLUMN editorial_status TEXT NOT NULL DEFAULT 'system_suggested';

ALTER TABLE embeddings ADD COLUMN source_version_id INTEGER REFERENCES source_versions(id);
ALTER TABLE embeddings ADD COLUMN model_digest TEXT;
ALTER TABLE embeddings ADD COLUMN text_hash TEXT;
ALTER TABLE embeddings ADD COLUMN normalization_version TEXT NOT NULL DEFAULT 'embedding-text.v1';
ALTER TABLE embeddings ADD COLUMN chunk_quality REAL NOT NULL DEFAULT 1
    CHECK(chunk_quality BETWEEN 0 AND 1);
ALTER TABLE embeddings ADD COLUMN status TEXT NOT NULL DEFAULT 'indexed';
ALTER TABLE embeddings ADD COLUMN error_code TEXT;
ALTER TABLE embeddings ADD COLUMN error_detail TEXT;
ALTER TABLE embeddings ADD COLUMN updated_at TEXT;

UPDATE embeddings
SET source_version_id=(SELECT source_version_id FROM chunks WHERE chunks.id=embeddings.chunk_id),
    text_hash=(SELECT content_hash FROM chunks WHERE chunks.id=embeddings.chunk_id),
    updated_at=created_at
WHERE source_version_id IS NULL OR text_hash IS NULL OR updated_at IS NULL;

ALTER TABLE teacher_queries ADD COLUMN failure_reason TEXT;
ALTER TABLE teacher_queries ADD COLUMN models_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE teacher_queries ADD COLUMN cache_hit INTEGER NOT NULL DEFAULT 0
    CHECK(cache_hit IN (0, 1));
ALTER TABLE teacher_queries ADD COLUMN stream_status TEXT NOT NULL DEFAULT 'complete';

CREATE TABLE source_editorial_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, operation_id)
);

CREATE TABLE editorial_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    title TEXT NOT NULL,
    page_start INTEGER NOT NULL CHECK(page_start > 0),
    page_end INTEGER NOT NULL CHECK(page_end >= page_start),
    cefr_min TEXT,
    cefr_max TEXT,
    topic TEXT,
    content_role TEXT NOT NULL DEFAULT 'other',
    derivation_method TEXT NOT NULL,
    provenance_confidence REAL NOT NULL CHECK(provenance_confidence BETWEEN 0 AND 1),
    editorial_status TEXT NOT NULL DEFAULT 'system_suggested',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_version_id, stable_key)
);

CREATE TABLE editorial_section_links (
    source_section_id INTEGER NOT NULL REFERENCES editorial_sections(id) ON DELETE CASCADE,
    target_section_id INTEGER NOT NULL REFERENCES editorial_sections(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    editorial_status TEXT NOT NULL DEFAULT 'system_suggested',
    created_at TEXT NOT NULL,
    PRIMARY KEY(source_section_id, target_section_id, relation),
    CHECK(source_section_id != target_section_id)
);

CREATE TABLE page_quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL CHECK(page_number > 0),
    extraction_method TEXT NOT NULL,
    character_count INTEGER NOT NULL DEFAULT 0 CHECK(character_count >= 0),
    detected_language TEXT,
    text_density REAL NOT NULL DEFAULT 0 CHECK(text_density >= 0),
    replacement_ratio REAL NOT NULL DEFAULT 0 CHECK(replacement_ratio BETWEEN 0 AND 1),
    weird_character_ratio REAL NOT NULL DEFAULT 0 CHECK(weird_character_ratio BETWEEN 0 AND 1),
    repeated_line_ratio REAL NOT NULL DEFAULT 0 CHECK(repeated_line_ratio BETWEEN 0 AND 1),
    ordering_warning INTEGER NOT NULL DEFAULT 0 CHECK(ordering_warning IN (0, 1)),
    columns_warning INTEGER NOT NULL DEFAULT 0 CHECK(columns_warning IN (0, 1)),
    tables_warning INTEGER NOT NULL DEFAULT 0 CHECK(tables_warning IN (0, 1)),
    damaged_german_ratio REAL NOT NULL DEFAULT 0 CHECK(damaged_german_ratio BETWEEN 0 AND 1),
    quality TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reviewed_variant_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_version_id, page_number)
);

CREATE TABLE page_extraction_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL CHECK(page_number > 0),
    method TEXT NOT NULL,
    method_version TEXT NOT NULL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    quality_score REAL NOT NULL CHECK(quality_score BETWEEN 0 AND 1),
    warnings_json TEXT NOT NULL DEFAULT '[]',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(source_version_id, page_number, method, method_version, text_hash)
);

CREATE TABLE library_cache (
    namespace TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    model TEXT,
    model_digest TEXT,
    prompt_version TEXT,
    config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    PRIMARY KEY(namespace, cache_key)
);

CREATE INDEX ix_sources_pedagogical_role
    ON sources(pedagogical_role, editorial_status, user_selected_core);
CREATE INDEX ix_editorial_sections_source
    ON editorial_sections(source_version_id, page_start, page_end);
CREATE INDEX ix_page_quality_source
    ON page_quality(source_version_id, quality, page_number);
CREATE INDEX ix_page_variants_source
    ON page_extraction_variants(source_version_id, page_number);
CREATE INDEX ix_embeddings_validity
    ON embeddings(provider, model, model_version, status, text_hash);
CREATE INDEX ix_library_cache_expiry ON library_cache(namespace, expires_at);
"""

_MIGRATION_0004 = """
CREATE TABLE pedagogical_concepts (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    language TEXT NOT NULL,
    display_name_es TEXT,
    display_name_de TEXT,
    category TEXT NOT NULL,
    description TEXT,
    editorial_status TEXT NOT NULL DEFAULT 'candidate',
    source_version_id INTEGER REFERENCES source_versions(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(normalized_name, language)
);

CREATE TABLE pedagogical_concept_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id TEXT NOT NULL REFERENCES pedagogical_concepts(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    language TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    confidence TEXT NOT NULL DEFAULT 'moderate',
    user_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(user_confirmed IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(concept_id, normalized_text, language)
);

CREATE TABLE pedagogical_concept_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_concept_id TEXT NOT NULL REFERENCES pedagogical_concepts(id) ON DELETE CASCADE,
    target_concept_id TEXT NOT NULL REFERENCES pedagogical_concepts(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_concept_id, target_concept_id, relation_type),
    CHECK(source_concept_id != target_concept_id)
);

CREATE TABLE document_page_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    pdf_page_index INTEGER NOT NULL CHECK(pdf_page_index >= 0),
    pdf_page_number INTEGER NOT NULL CHECK(pdf_page_number > 0),
    scan_layout TEXT NOT NULL DEFAULT 'unknown',
    printed_left_label TEXT,
    printed_right_label TEXT,
    printed_full_label TEXT,
    rotation INTEGER NOT NULL DEFAULT 0 CHECK(rotation IN (0, 90, 180, 270)),
    mapping_status TEXT NOT NULL DEFAULT 'candidate',
    mapping_origin TEXT NOT NULL,
    mapping_version INTEGER NOT NULL DEFAULT 1 CHECK(mapping_version > 0),
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_version_id, pdf_page_number),
    CHECK(pdf_page_index = pdf_page_number - 1)
);

CREATE TABLE pedagogical_evidence_locations (
    id TEXT PRIMARY KEY,
    concept_id TEXT NOT NULL REFERENCES pedagogical_concepts(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
    editorial_section_id INTEGER REFERENCES editorial_sections(id) ON DELETE SET NULL,
    page_mapping_id INTEGER REFERENCES document_page_mappings(id) ON DELETE SET NULL,
    pdf_page_index INTEGER CHECK(pdf_page_index >= 0),
    pdf_page_number INTEGER CHECK(pdf_page_number > 0),
    region_kind TEXT NOT NULL DEFAULT 'full',
    custom_bbox_json TEXT,
    heading TEXT,
    evidence_snippet TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    extraction_quality REAL NOT NULL DEFAULT 0 CHECK(extraction_quality BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'candidate',
    origin TEXT NOT NULL,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(pdf_page_number IS NULL OR pdf_page_index = pdf_page_number - 1)
);

CREATE UNIQUE INDEX ux_pedagogical_location_exact
    ON pedagogical_evidence_locations(
        concept_id, source_version_id, ifnull(chunk_id, -1),
        ifnull(pdf_page_number, -1), region_kind, evidence_hash
    );

CREATE TABLE teacher_query_concepts (
    query_id TEXT NOT NULL REFERENCES teacher_queries(id) ON DELETE CASCADE,
    concept_id TEXT NOT NULL REFERENCES pedagogical_concepts(id) ON DELETE CASCADE,
    origin TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TEXT NOT NULL,
    PRIMARY KEY(query_id, concept_id)
);

CREATE TABLE teacher_query_location_usage (
    query_id TEXT NOT NULL REFERENCES teacher_queries(id) ON DELETE CASCADE,
    location_id TEXT NOT NULL REFERENCES pedagogical_evidence_locations(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    evidence_role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(query_id, location_id),
    UNIQUE(query_id, sequence)
);

CREATE TABLE teacher_response_feedback (
    query_id TEXT PRIMARY KEY REFERENCES teacher_queries(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    comment TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE pedagogical_memory_reviews (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    query_id TEXT REFERENCES teacher_queries(id) ON DELETE SET NULL,
    verdict TEXT NOT NULL,
    actor TEXT NOT NULL,
    comment TEXT,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    reverts_review_id TEXT REFERENCES pedagogical_memory_reviews(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE pedagogical_memory_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    query_id TEXT REFERENCES teacher_queries(id) ON DELETE SET NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    comment TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX ix_pedagogical_concepts_status
    ON pedagogical_concepts(editorial_status, category, normalized_name);
CREATE INDEX ix_pedagogical_alias_lookup
    ON pedagogical_concept_aliases(normalized_text, status, concept_id);
CREATE INDEX ix_pedagogical_relations_source
    ON pedagogical_concept_relations(source_concept_id, status);
CREATE INDEX ix_page_mappings_source
    ON document_page_mappings(source_version_id, pdf_page_number, mapping_status);
CREATE INDEX ix_pedagogical_locations_concept
    ON pedagogical_evidence_locations(concept_id, status, source_version_id);
CREATE INDEX ix_pedagogical_locations_source
    ON pedagogical_evidence_locations(source_id, source_version_id, pdf_page_number);
CREATE INDEX ix_memory_reviews_target
    ON pedagogical_memory_reviews(target_type, target_id, created_at DESC);
CREATE INDEX ix_memory_audit_target
    ON pedagogical_memory_audit(target_type, target_id, created_at DESC);
"""

_MIGRATION_0005 = """
CREATE TABLE canonical_route_imports (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    route_version INTEGER NOT NULL CHECK(route_version > 0),
    reference_name TEXT NOT NULL,
    reference_sha256 TEXT NOT NULL,
    reference_size_bytes INTEGER NOT NULL CHECK(reference_size_bytes > 0),
    reference_page_count INTEGER NOT NULL CHECK(reference_page_count > 0),
    model TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    status TEXT NOT NULL,
    editorial_status TEXT NOT NULL,
    origin TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1)),
    validation_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    statistics_json TEXT NOT NULL DEFAULT '{}',
    imported_at TEXT NOT NULL,
    validated_at TEXT,
    superseded_at TEXT,
    UNIQUE(source_version_id, reference_sha256),
    UNIQUE(source_version_id, route_version)
);

CREATE UNIQUE INDEX ux_canonical_route_active_source
    ON canonical_route_imports(source_version_id) WHERE active=1;

CREATE TABLE canonical_route_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id TEXT NOT NULL REFERENCES canonical_route_imports(id) ON DELETE CASCADE,
    reference_pdf_page INTEGER NOT NULL CHECK(reference_pdf_page > 0),
    physical_index_page INTEGER,
    extraction_json TEXT NOT NULL,
    extraction_hash TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(import_id, reference_pdf_page)
);

CREATE TABLE canonical_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id TEXT NOT NULL REFERENCES canonical_route_imports(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    theme_number INTEGER NOT NULL CHECK(theme_number BETWEEN 1 AND 51),
    title_es TEXT NOT NULL,
    title_de TEXT,
    printed_start INTEGER NOT NULL CHECK(printed_start > 0),
    printed_end INTEGER CHECK(printed_end >= printed_start),
    printed_end_origin TEXT NOT NULL DEFAULT 'unknown',
    reference_pdf_page INTEGER NOT NULL CHECK(reference_pdf_page > 0),
    reference_visual_region TEXT NOT NULL DEFAULT 'unknown',
    manual_pdf_start INTEGER CHECK(manual_pdf_start > 0),
    manual_pdf_end INTEGER CHECK(manual_pdf_end >= manual_pdf_start),
    manual_scan_layout TEXT,
    manual_region TEXT,
    editorial_status TEXT NOT NULL,
    origin TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(import_id, stable_key),
    UNIQUE(import_id, theme_number)
);

CREATE TABLE canonical_outline_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_topic_id INTEGER NOT NULL REFERENCES canonical_topics(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES canonical_outline_nodes(id) ON DELETE CASCADE,
    sort_key INTEGER NOT NULL CHECK(sort_key >= 0),
    hierarchy_level TEXT NOT NULL,
    local_number TEXT,
    title_es TEXT,
    title_de TEXT,
    printed_page INTEGER CHECK(printed_page > 0),
    reference_pdf_page INTEGER NOT NULL CHECK(reference_pdf_page > 0),
    visual_region TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    manual_pdf_page INTEGER CHECK(manual_pdf_page > 0),
    manual_scan_layout TEXT,
    manual_region TEXT,
    editorial_status TEXT NOT NULL,
    origin TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    raw_visible_text TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(canonical_topic_id, sort_key)
);

CREATE TABLE canonical_legacy_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id TEXT NOT NULL REFERENCES canonical_route_imports(id) ON DELETE CASCADE,
    legacy_section_id INTEGER NOT NULL REFERENCES editorial_sections(id) ON DELETE CASCADE,
    canonical_topic_id INTEGER REFERENCES canonical_topics(id) ON DELETE SET NULL,
    outline_node_id INTEGER REFERENCES canonical_outline_nodes(id) ON DELETE SET NULL,
    mapping_status TEXT NOT NULL,
    score REAL NOT NULL CHECK(score BETWEEN 0 AND 1),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(import_id, legacy_section_id)
);

CREATE TABLE canonical_route_visual_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id TEXT NOT NULL REFERENCES canonical_route_imports(id) ON DELETE CASCADE,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    reference_pdf_page INTEGER NOT NULL CHECK(reference_pdf_page > 0),
    region TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    output_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    activated INTEGER NOT NULL DEFAULT 0 CHECK(activated IN (0, 1)),
    review_state TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE canonical_route_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id TEXT NOT NULL REFERENCES canonical_route_imports(id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    comment TEXT,
    reverts_audit_id INTEGER REFERENCES canonical_route_audits(id),
    created_at TEXT NOT NULL
);

CREATE INDEX ix_canonical_topics_route
    ON canonical_topics(import_id, theme_number);
CREATE INDEX ix_canonical_outline_topic
    ON canonical_outline_nodes(canonical_topic_id, sort_key);
CREATE INDEX ix_canonical_legacy_route
    ON canonical_legacy_mappings(import_id, mapping_status, legacy_section_id);
CREATE INDEX ix_canonical_audits_target
    ON canonical_route_audits(import_id, target_type, target_id, created_at);
"""

_MIGRATION_0006 = """
ALTER TABLE sources ADD COLUMN latest_version_id INTEGER REFERENCES source_versions(id);
ALTER TABLE sources ADD COLUMN document_state TEXT NOT NULL DEFAULT 'detected';
ALTER TABLE sources ADD COLUMN needs_manual_review INTEGER NOT NULL DEFAULT 0
    CHECK(needs_manual_review IN (0, 1));
ALTER TABLE sources ADD COLUMN last_inventory_at TEXT;

ALTER TABLE source_versions ADD COLUMN observed_path TEXT;
ALTER TABLE source_versions ADD COLUMN observed_name TEXT;
ALTER TABLE source_versions ADD COLUMN detected_at TEXT;
ALTER TABLE source_versions ADD COLUMN page_count INTEGER CHECK(page_count IS NULL OR page_count >= 0);
ALTER TABLE source_versions ADD COLUMN document_state TEXT NOT NULL DEFAULT 'detected';
ALTER TABLE source_versions ADD COLUMN availability_state TEXT NOT NULL DEFAULT 'present';
ALTER TABLE source_versions ADD COLUMN extraction_state TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE source_versions ADD COLUMN chunk_state TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE source_versions ADD COLUMN embedding_state TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE source_versions ADD COLUMN activation_state TEXT NOT NULL DEFAULT 'candidate';
ALTER TABLE source_versions ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0
    CHECK(is_active IN (0, 1));
ALTER TABLE source_versions ADD COLUMN extraction_tool TEXT;
ALTER TABLE source_versions ADD COLUMN extraction_tool_version TEXT;
ALTER TABLE source_versions ADD COLUMN ocr_tool TEXT;
ALTER TABLE source_versions ADD COLUMN ocr_tool_version TEXT;
ALTER TABLE source_versions ADD COLUMN ocr_languages_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE source_versions ADD COLUMN technical_metadata_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE source_versions ADD COLUMN version_provenance TEXT NOT NULL DEFAULT 'inventory';
ALTER TABLE source_versions ADD COLUMN change_reason TEXT;
ALTER TABLE source_versions ADD COLUMN superseded_at TEXT;

UPDATE sources
SET latest_version_id=(
        SELECT sv.id FROM source_versions sv
        WHERE sv.source_id=sources.id
        ORDER BY sv.version_number DESC LIMIT 1
    ),
    document_state=CASE
        WHEN status='missing' THEN 'missing'
        WHEN status='error' OR processing_state='error' THEN 'failed'
        WHEN processing_state='needs_ocr' THEN 'needs_ocr'
        WHEN current_version_id IS NOT NULL THEN 'active'
        ELSE 'detected'
    END;

UPDATE source_versions
SET observed_path=(SELECT current_path FROM sources WHERE sources.id=source_versions.source_id),
    observed_name=(SELECT name FROM sources WHERE sources.id=source_versions.source_id),
    detected_at=created_at,
    page_count=(SELECT page_count FROM documents WHERE documents.source_version_id=source_versions.id),
    document_state=CASE
        WHEN id=(SELECT current_version_id FROM sources WHERE sources.id=source_versions.source_id)
            THEN 'active'
        ELSE 'historical'
    END,
    availability_state=CASE
        WHEN (SELECT status FROM sources WHERE sources.id=source_versions.source_id)='missing'
            AND id=(SELECT latest_version_id FROM sources WHERE sources.id=source_versions.source_id)
            THEN 'missing'
        ELSE 'present'
    END,
    extraction_state=CASE
        WHEN processing_state='processed' OR processing_state='partial' THEN 'extracted'
        WHEN processing_state='needs_ocr' THEN 'needs_ocr'
        WHEN processing_state='processing' THEN 'processing'
        WHEN processing_state='error' THEN 'failed'
        WHEN processing_state='unsupported' THEN 'unsupported'
        ELSE 'pending'
    END,
    chunk_state=CASE
        WHEN EXISTS(SELECT 1 FROM chunks WHERE chunks.source_version_id=source_versions.id)
            THEN 'available'
        ELSE 'pending'
    END,
    embedding_state=CASE
        WHEN EXISTS(SELECT 1 FROM embeddings WHERE embeddings.source_version_id=source_versions.id)
            THEN 'available'
        ELSE 'pending'
    END,
    activation_state=CASE
        WHEN id=(SELECT current_version_id FROM sources WHERE sources.id=source_versions.source_id)
            THEN 'active'
        ELSE 'historical'
    END,
    is_active=CASE
        WHEN id=(SELECT current_version_id FROM sources WHERE sources.id=source_versions.source_id)
            THEN 1
        ELSE 0
    END,
    extraction_tool=extractor,
    extraction_tool_version=extractor_version,
    version_provenance='legacy_backfill';

CREATE UNIQUE INDEX ux_source_versions_one_active
    ON source_versions(source_id) WHERE is_active=1;
CREATE INDEX ix_sources_latest_version ON sources(latest_version_id);
CREATE INDEX ix_sources_document_state
    ON sources(document_state, needs_manual_review, status);
CREATE INDEX ix_source_versions_lifecycle
    ON source_versions(source_id, activation_state, document_state);

CREATE TABLE document_inventory_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES processing_jobs(id),
    generated_at TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX ix_document_inventory_runs_generated
    ON document_inventory_runs(generated_at DESC);
"""

_ROLLBACK_0006 = """
DROP INDEX IF EXISTS ix_source_versions_lifecycle;
DROP INDEX IF EXISTS ix_sources_document_state;
DROP INDEX IF EXISTS ix_sources_latest_version;
DROP INDEX IF EXISTS ux_source_versions_one_active;
DROP TABLE IF EXISTS document_inventory_runs;

ALTER TABLE source_versions DROP COLUMN superseded_at;
ALTER TABLE source_versions DROP COLUMN change_reason;
ALTER TABLE source_versions DROP COLUMN version_provenance;
ALTER TABLE source_versions DROP COLUMN technical_metadata_json;
ALTER TABLE source_versions DROP COLUMN ocr_languages_json;
ALTER TABLE source_versions DROP COLUMN ocr_tool_version;
ALTER TABLE source_versions DROP COLUMN ocr_tool;
ALTER TABLE source_versions DROP COLUMN extraction_tool_version;
ALTER TABLE source_versions DROP COLUMN extraction_tool;
ALTER TABLE source_versions DROP COLUMN is_active;
ALTER TABLE source_versions DROP COLUMN activation_state;
ALTER TABLE source_versions DROP COLUMN embedding_state;
ALTER TABLE source_versions DROP COLUMN chunk_state;
ALTER TABLE source_versions DROP COLUMN extraction_state;
ALTER TABLE source_versions DROP COLUMN availability_state;
ALTER TABLE source_versions DROP COLUMN document_state;
ALTER TABLE source_versions DROP COLUMN page_count;
ALTER TABLE source_versions DROP COLUMN detected_at;
ALTER TABLE source_versions DROP COLUMN observed_name;
ALTER TABLE source_versions DROP COLUMN observed_path;

ALTER TABLE sources DROP COLUMN last_inventory_at;
ALTER TABLE sources DROP COLUMN needs_manual_review;
ALTER TABLE sources DROP COLUMN document_state;
ALTER TABLE sources DROP COLUMN latest_version_id;
DELETE FROM library_schema WHERE version=6;
"""

_MIGRATION_0007 = """
CREATE TABLE document_processing_runs (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE RESTRICT,
    target_hash TEXT NOT NULL,
    run_type TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    selection_strategy TEXT NOT NULL,
    selected_pages_json TEXT NOT NULL DEFAULT '[]',
    reused_results_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL CHECK(state IN (
        'planned','queued','running','paused','completed','completed_with_issues',
        'failed','cancelled','stale','superseded'
    )),
    parent_run_id TEXT REFERENCES document_processing_runs(id) ON DELETE SET NULL,
    base_run_id TEXT REFERENCES document_processing_runs(id) ON DELETE SET NULL,
    initiated_by TEXT,
    reason TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_detail TEXT,
    resumable INTEGER NOT NULL DEFAULT 1 CHECK(resumable IN (0, 1)),
    exclusive INTEGER NOT NULL DEFAULT 1 CHECK(exclusive IN (0, 1)),
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE document_run_stages (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES document_processing_runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'not_scheduled','pending','queued','running','paused','completed',
        'completed_with_issues','failed','skipped','cancelled'
    )),
    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt > 0),
    configuration_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    error_code TEXT,
    error_detail TEXT,
    resumable INTEGER NOT NULL DEFAULT 1 CHECK(resumable IN (0, 1)),
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, name, attempt)
);

CREATE TABLE document_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    pdf_page_index INTEGER NOT NULL CHECK(pdf_page_index >= 0),
    printed_page_number TEXT,
    fingerprint TEXT,
    width_points REAL CHECK(width_points IS NULL OR width_points > 0),
    height_points REAL CHECK(height_points IS NULL OR height_points > 0),
    rotation_degrees INTEGER,
    has_text INTEGER CHECK(has_text IS NULL OR has_text IN (0, 1)),
    character_count INTEGER CHECK(character_count IS NULL OR character_count >= 0),
    text_quality TEXT,
    layout_state TEXT,
    structure_state TEXT,
    review_state TEXT,
    issue_count INTEGER NOT NULL DEFAULT 0 CHECK(issue_count >= 0),
    last_run_id TEXT REFERENCES document_processing_runs(id) ON DELETE SET NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_version_id, pdf_page_index)
);

CREATE TABLE document_page_stage_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES document_processing_runs(id) ON DELETE CASCADE,
    page_id INTEGER NOT NULL REFERENCES document_pages(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt > 0),
    state TEXT NOT NULL CHECK(state IN (
        'not_scheduled','pending','running','completed','completed_with_issues',
        'failed','skipped','needs_review','superseded'
    )),
    metrics_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    reused_from_run_id TEXT REFERENCES document_processing_runs(id) ON DELETE SET NULL,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, page_id, stage_name, attempt)
);

CREATE TABLE document_coverage_snapshots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES document_processing_runs(id) ON DELETE CASCADE,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    dimension TEXT NOT NULL,
    execution_status TEXT NOT NULL CHECK(execution_status IN (
        'executed','not_executed','no_data'
    )),
    denominator INTEGER CHECK(denominator IS NULL OR denominator >= 0),
    completed INTEGER CHECK(completed IS NULL OR completed >= 0),
    with_issues INTEGER CHECK(with_issues IS NULL OR with_issues >= 0),
    failed INTEGER CHECK(failed IS NULL OR failed >= 0),
    pending INTEGER CHECK(pending IS NULL OR pending >= 0),
    not_applicable INTEGER CHECK(not_applicable IS NULL OR not_applicable >= 0),
    unknown INTEGER CHECK(unknown IS NULL OR unknown >= 0),
    breakdown_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    captured_at TEXT NOT NULL
);

CREATE TABLE document_run_issues (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES document_processing_runs(id) ON DELETE CASCADE,
    page_id INTEGER REFERENCES document_pages(id) ON DELETE CASCADE,
    stage_name TEXT,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    resolved_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE document_run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES document_processing_runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor TEXT,
    from_state TEXT,
    to_state TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

ALTER TABLE processing_jobs
    ADD COLUMN document_run_id TEXT REFERENCES document_processing_runs(id) ON DELETE SET NULL;
ALTER TABLE processing_jobs
    ADD COLUMN document_run_stage_id TEXT REFERENCES document_run_stages(id) ON DELETE SET NULL;
ALTER TABLE processing_jobs ADD COLUMN legacy INTEGER NOT NULL DEFAULT 1
    CHECK(legacy IN (0, 1));

CREATE INDEX ix_document_runs_target
    ON document_processing_runs(source_id, source_version_id, created_at DESC);
CREATE INDEX ix_document_runs_state
    ON document_processing_runs(state, updated_at DESC);
CREATE UNIQUE INDEX ux_document_runs_exclusive_active
    ON document_processing_runs(source_version_id)
    WHERE exclusive=1 AND state IN ('queued','running','paused');
CREATE INDEX ix_document_stages_run
    ON document_run_stages(run_id, name, attempt DESC);
CREATE INDEX ix_document_pages_version
    ON document_pages(source_version_id, pdf_page_index);
CREATE INDEX ix_document_page_results_run
    ON document_page_stage_results(run_id, stage_name, state, page_id);
CREATE INDEX ix_document_coverage_run
    ON document_coverage_snapshots(run_id, captured_at DESC, dimension);
CREATE INDEX ix_document_issues_run
    ON document_run_issues(run_id, resolved_at, severity);
CREATE INDEX ix_document_events_run
    ON document_run_events(run_id, created_at);
CREATE INDEX ix_jobs_document_run
    ON processing_jobs(document_run_id, document_run_stage_id);
"""

_ROLLBACK_0007 = """
DROP INDEX IF EXISTS ix_jobs_document_run;
DROP INDEX IF EXISTS ix_document_events_run;
DROP INDEX IF EXISTS ix_document_issues_run;
DROP INDEX IF EXISTS ix_document_coverage_run;
DROP INDEX IF EXISTS ix_document_page_results_run;
DROP INDEX IF EXISTS ix_document_pages_version;
DROP INDEX IF EXISTS ix_document_stages_run;
DROP INDEX IF EXISTS ux_document_runs_exclusive_active;
DROP INDEX IF EXISTS ix_document_runs_state;
DROP INDEX IF EXISTS ix_document_runs_target;

ALTER TABLE processing_jobs DROP COLUMN legacy;
ALTER TABLE processing_jobs DROP COLUMN document_run_stage_id;
ALTER TABLE processing_jobs DROP COLUMN document_run_id;

DROP TABLE IF EXISTS document_run_events;
DROP TABLE IF EXISTS document_run_issues;
DROP TABLE IF EXISTS document_coverage_snapshots;
DROP TABLE IF EXISTS document_page_stage_results;
DROP TABLE IF EXISTS document_pages;
DROP TABLE IF EXISTS document_run_stages;
DROP TABLE IF EXISTS document_processing_runs;
DELETE FROM library_schema WHERE version=7;
"""

_MIGRATION_0008 = """
CREATE TABLE document_page_artifacts (
    page_id INTEGER PRIMARY KEY REFERENCES document_pages(id) ON DELETE CASCADE,
    artifact_version TEXT NOT NULL,
    text_content TEXT,
    normalized_text TEXT,
    text_hash TEXT,
    text_metrics_json TEXT NOT NULL DEFAULT '{}',
    visual_hash TEXT,
    perceptual_hash TEXT,
    region_hashes_json TEXT NOT NULL DEFAULT '{}',
    visual_metrics_json TEXT NOT NULL DEFAULT '{}',
    geometry_hash TEXT,
    cache_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE document_version_comparisons (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    base_source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE RESTRICT,
    target_source_version_id INTEGER NOT NULL REFERENCES source_versions(id) ON DELETE RESTRICT,
    base_hash TEXT NOT NULL,
    target_hash TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    supersedes_comparison_id TEXT REFERENCES document_version_comparisons(id)
        ON DELETE SET NULL,
    state TEXT NOT NULL CHECK(state IN (
        'planned','running','completed','completed_with_issues','failed',
        'cancelled','stale','superseded'
    )),
    initiated_by TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_detail TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0 CHECK(needs_review IN (0, 1)),
    transfer_plan_revision INTEGER NOT NULL DEFAULT 0 CHECK(transfer_plan_revision >= 0),
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK(base_source_version_id <> target_source_version_id),
    UNIQUE(id, source_id)
);

CREATE TABLE document_page_correspondences (
    id TEXT PRIMARY KEY,
    comparison_id TEXT NOT NULL REFERENCES document_version_comparisons(id)
        ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK(relation_type IN (
        'one_to_one','one_to_many','many_to_one','many_to_many',
        'inserted','deleted','blank','duplicate','unresolved','ambiguous'
    )),
    review_state TEXT NOT NULL CHECK(review_state IN (
        'proposed','auto_supported','needs_review','confirmed','rejected',
        'manually_adjusted','superseded'
    )),
    confidence TEXT NOT NULL CHECK(confidence IN (
        'very_high','high','medium','low','ambiguous'
    )),
    text_similarity REAL CHECK(text_similarity IS NULL OR text_similarity BETWEEN 0 AND 1),
    visual_similarity REAL CHECK(visual_similarity IS NULL OR visual_similarity BETWEEN 0 AND 1),
    geometry_similarity REAL CHECK(geometry_similarity IS NULL OR geometry_similarity BETWEEN 0 AND 1),
    ordinal_similarity REAL CHECK(ordinal_similarity IS NULL OR ordinal_similarity BETWEEN 0 AND 1),
    printed_page_similarity REAL CHECK(
        printed_page_similarity IS NULL OR printed_page_similarity BETWEEN 0 AND 1
    ),
    split_similarity REAL CHECK(split_similarity IS NULL OR split_similarity BETWEEN 0 AND 1),
    aggregate_score REAL NOT NULL CHECK(aggregate_score BETWEEN 0 AND 1),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    text_difference_json TEXT NOT NULL DEFAULT '{}',
    recommendation TEXT NOT NULL CHECK(recommendation IN (
        'target_preferred','base_preferred','mixed','manual_review','insufficient_evidence'
    )),
    review_note TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE document_correspondence_pages (
    correspondence_id TEXT NOT NULL REFERENCES document_page_correspondences(id)
        ON DELETE CASCADE,
    side TEXT NOT NULL CHECK(side IN ('base','target')),
    page_id INTEGER NOT NULL REFERENCES document_pages(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK(position >= 0),
    PRIMARY KEY(correspondence_id, side, page_id),
    UNIQUE(correspondence_id, side, position)
);

CREATE TABLE document_comparison_events (
    id TEXT PRIMARY KEY,
    comparison_id TEXT NOT NULL REFERENCES document_version_comparisons(id)
        ON DELETE CASCADE,
    correspondence_id TEXT REFERENCES document_page_correspondences(id)
        ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    actor TEXT,
    previous_state TEXT,
    new_state TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE document_transfer_plans (
    id TEXT PRIMARY KEY,
    comparison_id TEXT NOT NULL REFERENCES document_version_comparisons(id)
        ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK(revision > 0),
    state TEXT NOT NULL DEFAULT 'proposal' CHECK(state IN ('proposal','invalidated')),
    plan_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    invalidated_at TEXT,
    UNIQUE(comparison_id, revision)
);

CREATE INDEX ix_page_artifacts_text_hash ON document_page_artifacts(text_hash);
CREATE INDEX ix_page_artifacts_visual_hash ON document_page_artifacts(visual_hash);
CREATE INDEX ix_comparisons_versions ON document_version_comparisons(
    source_id, base_source_version_id, target_source_version_id, created_at DESC
);
CREATE INDEX ix_comparisons_state ON document_version_comparisons(state, updated_at DESC);
CREATE UNIQUE INDEX ux_comparisons_active_configuration
    ON document_version_comparisons(base_source_version_id, target_source_version_id)
    WHERE state IN ('planned','running');
CREATE INDEX ix_correspondences_comparison ON document_page_correspondences(
    comparison_id, review_state, confidence, relation_type, created_at
);
CREATE INDEX ix_correspondence_pages_page ON document_correspondence_pages(page_id, side);
CREATE INDEX ix_comparison_events ON document_comparison_events(
    comparison_id, created_at, id
);
"""

_ROLLBACK_0008 = """
DROP INDEX IF EXISTS ix_comparison_events;
DROP INDEX IF EXISTS ix_correspondence_pages_page;
DROP INDEX IF EXISTS ix_correspondences_comparison;
DROP INDEX IF EXISTS ux_comparisons_active_configuration;
DROP INDEX IF EXISTS ix_comparisons_state;
DROP INDEX IF EXISTS ix_comparisons_versions;
DROP INDEX IF EXISTS ix_page_artifacts_visual_hash;
DROP INDEX IF EXISTS ix_page_artifacts_text_hash;
DROP TABLE IF EXISTS document_transfer_plans;
DROP TABLE IF EXISTS document_comparison_events;
DROP TABLE IF EXISTS document_correspondence_pages;
DROP TABLE IF EXISTS document_page_correspondences;
DROP TABLE IF EXISTS document_version_comparisons;
DROP TABLE IF EXISTS document_page_artifacts;
DELETE FROM library_schema WHERE version=8;
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
                current = 2
            if current < 3:
                self._apply_migration(connection, 3, _MIGRATION_0003)
                current = 3
            if current < 4:
                if current == 3:
                    self._backup_before_v4(connection)
                self._apply_migration(connection, 4, _MIGRATION_0004)
                current = 4
            if current < 5:
                if current == 4:
                    self._backup_before_v5(connection)
                self._apply_migration(connection, 5, _MIGRATION_0005)
                current = 5
            if current < 6:
                self._apply_migration(connection, 6, _MIGRATION_0006)
                current = 6
            if current < 7:
                self._apply_migration(connection, 7, _MIGRATION_0007)
                current = 7
            if current < 8:
                self._apply_migration(connection, 8, _MIGRATION_0008)
            return self._current_version(connection)

    def rollback_version_6(self) -> int:
        with self.connect() as connection:
            if self._current_version(connection) != 6:
                raise RuntimeError("library schema rollback requires version 6")
            try:
                connection.executescript("BEGIN IMMEDIATE;\n" + _ROLLBACK_0006)
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            return self._current_version(connection)

    def rollback_version_7(self) -> int:
        with self.connect() as connection:
            if self._current_version(connection) != 7:
                raise RuntimeError("library schema rollback requires version 7")
            try:
                connection.executescript("BEGIN IMMEDIATE;\n" + _ROLLBACK_0007)
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            return self._current_version(connection)

    def rollback_version_8(self) -> int:
        with self.connect() as connection:
            if self._current_version(connection) != 8:
                raise RuntimeError("library schema rollback requires version 8")
            try:
                connection.executescript("BEGIN IMMEDIATE;\n" + _ROLLBACK_0008)
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            return self._current_version(connection)

    def _backup_before_v4(self, connection: sqlite3.Connection) -> Path | None:
        return self._backup_before_schema(connection, 3)

    def _backup_before_v5(self, connection: sqlite3.Connection) -> Path | None:
        return self._backup_before_schema(connection, 4)

    def _backup_before_schema(
        self, connection: sqlite3.Connection, schema_version: int
    ) -> Path | None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        required = self.path.stat().st_size * 2 + 10 * 1024 * 1024
        if shutil.disk_usage(self.path.parent).free < required:
            raise RuntimeError(f"insufficient space for library schema {schema_version} backup")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_directory = self.path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        target = backup_directory / f"{self.path.name}.schema{schema_version}-{stamp}.bak"
        partial = target.with_suffix(f"{target.suffix}.partial")
        try:
            with sqlite3.connect(partial) as backup:
                connection.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError(
                        f"library schema {schema_version} backup failed SQLite quick_check"
                    )
                backup.execute("PRAGMA journal_mode=DELETE")
            partial.with_name(f"{partial.name}-wal").unlink(missing_ok=True)
            partial.with_name(f"{partial.name}-shm").unlink(missing_ok=True)
            partial.replace(target)
        except Exception:
            partial.unlink(missing_ok=True)
            partial.with_name(f"{partial.name}-wal").unlink(missing_ok=True)
            partial.with_name(f"{partial.name}-shm").unlink(missing_ok=True)
            raise
        return target

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
