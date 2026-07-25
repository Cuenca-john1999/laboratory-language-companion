from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

LIBRARY_SCHEMA_VERSION = 5

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
