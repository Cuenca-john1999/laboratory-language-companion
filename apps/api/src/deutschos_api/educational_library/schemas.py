from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from deutschos_api.schemas.base import APIModel


class SourceStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    EXCLUDED = "excluded"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class ProcessingState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    PARTIAL = "partial"
    NEEDS_OCR = "needs_ocr"
    AWAITING_TRANSCRIBER = "awaiting_transcriber"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class KnowledgeStatus(StrEnum):
    CANDIDATE = "candidate"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    STALE = "stale"


class KnowledgeKind(StrEnum):
    GRAMMAR_RULE = "grammar_rule"
    GRAMMAR_CONTRAST = "grammar_contrast"
    VOCABULARY_ITEM = "vocabulary_item"
    USAGE_EXAMPLE = "usage_example"
    COMMON_ERROR = "common_error"
    SPANISH_GERMAN_CONTRAST = "spanish_german_contrast"
    COMMUNICATION_STRATEGY = "communication_strategy"
    EXERCISE_PATTERN = "exercise_pattern"
    CULTURAL_NOTE = "cultural_note"
    SUMMARY = "summary"


class RightsCategory(StrEnum):
    OWNED_OR_CREATED = "owned_or_created"
    OPEN_LICENSE = "open_license"
    OFFICIAL_PUBLIC = "official_public"
    PERSONAL_USE_COMMERCIAL = "personal_use_commercial"
    EXTERNAL_REFERENCE = "external_reference"
    UNKNOWN = "unknown"


class SourceKind(StrEnum):
    DOCUMENT = "document"
    SUBTITLE = "subtitle"
    AUDIO = "audio"
    VIDEO = "video"
    IMAGE = "image"
    ARCHIVE = "archive"
    UNKNOWN = "unknown"


class ExtractedSection(APIModel):
    sequence: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=50)
    title: str | None = Field(default=None, max_length=500)
    hierarchy: list[str] = Field(default_factory=list, max_length=20)
    text: str = Field(default="", max_length=2_000_000)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    content_role: Literal[
        "theory", "example", "exercise", "solution", "glossary", "index", "transcript", "other"
    ] = "other"


class ExtractionResult(APIModel):
    title: str | None = Field(default=None, max_length=1000)
    author: str | None = Field(default=None, max_length=1000)
    language: str | None = Field(default=None, max_length=20)
    sections: list[ExtractedSection] = Field(default_factory=list, max_length=20_000)
    page_count: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    extraction_quality: float = Field(default=0, ge=0, le=1)
    needs_ocr: bool = False
    needs_transcription: bool = False
    processing_state: ProcessingState
    extractor: str
    extractor_version: str


class InventoryExtension(APIModel):
    extension: str
    files: int = Field(ge=0)
    bytes: int = Field(ge=0)


class InventoryReport(APIModel):
    generated_at: datetime
    root: str
    files: int = Field(ge=0)
    directories: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    by_extension: list[InventoryExtension]
    without_extension: int = Field(ge=0)
    hidden_files: int = Field(ge=0)
    empty_files: int = Field(ge=0)
    over_50_mb: int = Field(ge=0)
    over_250_mb: int = Field(ge=0)
    over_1_gb: int = Field(ge=0)
    possible_duplicate_groups: int = Field(ge=0)
    confirmed_duplicate_groups: int = Field(ge=0)
    confirmed_duplicate_files: int = Field(ge=0)
    symlinks: int = Field(ge=0)
    unknown_formats: int = Field(ge=0)
    documents: int = Field(ge=0)
    subtitles: int = Field(ge=0)
    audio: int = Field(ge=0)
    video: int = Field(ge=0)
    images: int = Field(ge=0)
    archives: int = Field(ge=0)
    problematic_names: list[str] = Field(default_factory=list, max_length=500)
    overly_long_paths: list[str] = Field(default_factory=list, max_length=500)
    inaccessible: list[str] = Field(default_factory=list, max_length=500)
    duplicate_groups: list[list[str]] = Field(default_factory=list, max_length=500)


class ScanSummary(APIModel):
    job_id: str
    inventory: InventoryReport
    new: int = 0
    modified: int = 0
    renamed: int = 0
    missing: int = 0
    duplicates: int = 0
    unchanged: int = 0
    processed: int = 0
    errors: int = 0
    unsupported: int = 0
    cancelled: bool = False


class LibraryCapabilities(APIModel):
    fts5: bool
    pdftotext: bool
    ffprobe: bool
    transcription_backend: str | None = None
    transcription_model: str | None = None
    semantic_available: bool
    embedding_provider: str | None = None
    embedding_model: str | None = None
    ollama_available: bool = False


class LibrarySummary(APIModel):
    materials_root: str
    runtime_root: str
    schema_version: int
    total_sources: int
    total_bytes: int
    sources_present: int
    sources_missing: int
    processed: int
    pending: int
    errors: int
    unsupported: int
    needs_ocr: int
    needs_transcription: int
    chunks: int
    embeddings: int
    knowledge_units: int
    knowledge_by_status: dict[str, int]
    jobs_by_status: dict[str, int]
    capabilities: LibraryCapabilities
    latest_inventory: InventoryReport | None = None


class SourceRead(APIModel):
    id: str
    current_path: str
    name: str
    kind: SourceKind
    format: str
    size_bytes: int
    content_hash: str | None
    language: str | None
    cefr_level: str | None
    topics: list[str]
    provenance: str | None
    rights: RightsCategory
    priority: int
    editorial_confidence: float
    review_status: str
    status: SourceStatus
    processing_state: ProcessingState
    duplicate_of_source_id: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    current_version: int


class SourceVersionRead(APIModel):
    id: int
    source_id: str
    version_number: int
    content_hash: str
    size_bytes: int
    extractor: str | None
    extractor_version: str | None
    processing_state: ProcessingState
    error_code: str | None
    statistics: dict[str, JsonValue]
    created_at: datetime


class ChunkRead(APIModel):
    id: int
    source_id: str
    source_version_id: int
    source_name: str
    source_path: str
    text: str
    title: str | None
    page_start: int | None
    page_end: int | None
    start_seconds: float | None
    end_seconds: float | None
    level: str | None
    topics: list[str]
    review_status: str
    rights: RightsCategory
    source_version: int
    content_role: str


class SearchResult(ChunkRead):
    text: str = Field(exclude=True)
    lexical_score: float | None = None
    semantic_score: float | None = None
    combined_score: float
    snippet: str


class SearchResponse(APIModel):
    query: str
    requested_mode: Literal["lexical", "semantic", "hybrid"]
    effective_mode: Literal["lexical", "semantic", "hybrid"]
    semantic_available: bool
    results: list[SearchResult]
    warning: str | None = None


class KnowledgeCitation(APIModel):
    chunk_id: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=600)


class KnowledgeDraft(APIModel):
    evidence_sufficient: bool
    kind: KnowledgeKind
    title: str = Field(min_length=1, max_length=500)
    content_es: str = Field(min_length=1, max_length=8_000)
    german_examples: list[str] = Field(default_factory=list, max_length=20)
    translations: list[str] = Field(default_factory=list, max_length=20)
    cefr_level: str = Field(pattern=r"^(pre-A1|A1|A2|B1|B2|C1|C2|unknown)$")
    topics: list[str] = Field(default_factory=list, max_length=30)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    citations: list[KnowledgeCitation] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)


class KnowledgeUnitRead(KnowledgeDraft):
    id: str
    status: KnowledgeStatus
    model: str
    prompt_version: str
    stale: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeReviewRequest(APIModel):
    action: Literal["approve", "reject", "conflict", "needs_review", "useful", "incorrect"]
    comment: str | None = Field(default=None, max_length=2_000)


class GroundedGenerationRequest(APIModel):
    query: str = Field(min_length=2, max_length=1_000)
    level: str = Field(default="A1", pattern=r"^(pre-A1|A1|A2|B1|B2|C1|C2)$")
    objective: Literal["explanation", "micro_lesson", "exercises", "answer", "error_explanation"]
    explanation_language: Literal["es", "de"] = "es"
    max_sources: int = Field(default=5, ge=1, le=8)
    model: str | None = Field(default=None, max_length=200)


class GroundedClaim(APIModel):
    text: str = Field(min_length=1, max_length=2_000)
    source_chunk_ids: list[int] = Field(min_length=1, max_length=8)


class GroundedDraftPayload(APIModel):
    title: str = Field(min_length=1, max_length=500)
    explanation: str = Field(min_length=1, max_length=8_000)
    examples: list[str] = Field(default_factory=list, max_length=20)
    exercises: list[str] = Field(default_factory=list, max_length=20)
    claims: list[GroundedClaim] = Field(min_length=1, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)


class GroundedSource(APIModel):
    chunk_id: int
    source_id: str
    source_name: str
    page_start: int | None
    page_end: int | None
    start_seconds: float | None
    end_seconds: float | None
    source_version: int
    review_status: str


class GroundedGenerationRead(APIModel):
    id: str
    query: str
    objective: str
    payload: GroundedDraftPayload
    sources: list[GroundedSource]
    model: str
    prompt_version: str
    review_status: Literal["draft"]
    evidence_sufficient: bool
    created_at: datetime


class JobRead(APIModel):
    id: str
    kind: str
    state: JobState
    priority: int
    progress_current: int
    progress_total: int
    attempts: int
    cursor: str | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancel_requested: bool


class ScanRequest(APIModel):
    process_documents: bool = True


class SourceUpdateRequest(APIModel):
    rights: RightsCategory | None = None
    priority: int | None = Field(default=None, ge=-100, le=100)
    editorial_confidence: float | None = Field(default=None, ge=0, le=1)
    review_status: str | None = Field(default=None, max_length=50)
    language: str | None = Field(default=None, max_length=20)
    cefr_level: str | None = Field(default=None, max_length=20)
    topics: list[str] | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def at_least_one_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one source field must be provided")
        return self


class KnowledgeGenerationRequest(APIModel):
    query: str = Field(min_length=2, max_length=1_000)
    model: str | None = Field(default=None, max_length=200)
    max_chunks: int = Field(default=4, ge=1, le=8)


class LibraryError(RuntimeError):
    pass


class LibraryBusyError(LibraryError):
    pass


class LibraryNotFoundError(LibraryError):
    pass


class LibraryContractError(LibraryError):
    pass


class LibraryProviderUnavailableError(LibraryError):
    pass
