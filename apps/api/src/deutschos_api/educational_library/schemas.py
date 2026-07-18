from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

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


class PedagogicalRole(StrEnum):
    CORE_THEORY = "core_theory"
    CORE_WORKBOOK = "core_workbook"
    CORE_ANSWER_KEY = "core_answer_key"
    SUPPLEMENTARY = "supplementary"
    REFERENCE = "reference"
    GLOSSARY = "glossary"
    ANSWER_KEY = "answer_key"
    UNKNOWN = "unknown"


class EditorialStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    USER_CONFIRMED = "user_confirmed"
    SYSTEM_SUGGESTED = "system_suggested"
    REJECTED = "rejected"


class MetadataOrigin(StrEnum):
    SCANNER = "scanner"
    DOCUMENT_METADATA = "document_metadata"
    CONTENT_INSPECTION = "content_inspection"
    FILENAME = "filename"
    USER = "user"


class TeacherIntent(StrEnum):
    DEFINITION = "definition"
    DIFFERENCE = "difference"
    GRAMMAR_EXPLANATION = "grammar_explanation"
    USAGE = "usage"
    SENTENCE_EXPLANATION = "sentence_explanation"
    TRANSLATION_IN_CONTEXT = "translation_in_context"
    SOURCE_LOOKUP = "source_lookup"
    OVERVIEW = "overview"
    UNKNOWN = "unknown"


class QueryAmbiguity(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class EvidenceConfidence(StrEnum):
    SOLID = "solid"
    MODERATE = "moderate"
    LIMITED = "limited"
    INSUFFICIENT = "insufficient"


class TeacherQueryStatus(StrEnum):
    COMPLETED = "completed"
    INSUFFICIENT = "insufficient"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class TeacherFailureReason(StrEnum):
    NO_EVIDENCE = "no_evidence"
    WEAK_EVIDENCE = "weak_evidence"
    RETRIEVAL_FAILURE = "retrieval_failure"
    MODEL_UNAVAILABLE = "model_unavailable"
    GENERATION_FAILURE = "generation_failure"
    CITATION_VALIDATION_FAILURE = "citation_validation_failure"
    REPAIR_FAILURE = "repair_failure"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


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
    pdftoppm: bool = False
    tesseract: bool = False
    ocrmypdf: bool = False
    tesseract_languages: list[str] = Field(default_factory=list)
    vision_available: bool = False
    installed_models: list[str] = Field(default_factory=list)


class SemanticIndexSummary(APIModel):
    model: str | None
    model_digest: str | None
    indexed: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)
    stale: int = Field(ge=0)
    excluded: int = Field(ge=0)
    dimension: int | None = Field(default=None, ge=1)
    normalization_version: str


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
    semantic_index: SemanticIndexSummary
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
    canonical_title: str | None
    display_alias: str | None
    author: str | None
    publisher: str | None
    edition: str | None
    cefr_min: str | None
    cefr_max: str | None
    pedagogical_role: PedagogicalRole
    source_priority: int
    editorial_status: EditorialStatus
    user_selected_core: bool
    metadata_origin: MetadataOrigin
    metadata_confidence: float = Field(ge=0, le=1)
    editorial_notes: str | None
    related_source_id: str | None
    semantic_indexed_chunks: int = Field(default=0, ge=0)
    semantic_failed_chunks: int = Field(default=0, ge=0)


class CoreSourceCandidate(APIModel):
    source: SourceRead
    suggested_role: PedagogicalRole | None
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=10)
    unambiguous: bool = False


class CoreSourceAssignmentRequest(APIModel):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    pedagogical_role: Literal["core_theory", "core_workbook", "core_answer_key"]
    display_alias: str | None = Field(default=None, min_length=1, max_length=300)
    canonical_title: str | None = Field(default=None, min_length=1, max_length=500)
    related_source_id: str | None = Field(default=None, min_length=8, max_length=100)
    editorial_notes: str | None = Field(default=None, max_length=2_000)


class CoreSourcePairRead(APIModel):
    theory: SourceRead | None
    workbook: SourceRead | None
    answer_key: SourceRead | None
    candidates: list[CoreSourceCandidate]
    ready: bool


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
    pedagogical_role: PedagogicalRole = PedagogicalRole.UNKNOWN
    source_priority: int = 0
    extraction_quality: float = Field(default=0, ge=0, le=1)
    page_quality: str | None = None
    retrieval_origins: list[str] = Field(default_factory=list)


class SearchTimings(APIModel):
    fts_ms: int = Field(default=0, ge=0)
    query_embedding_ms: int = Field(default=0, ge=0)
    vector_ms: int = Field(default=0, ge=0)
    ranking_ms: int = Field(default=0, ge=0)
    total_ms: int = Field(default=0, ge=0)


class SearchResponse(APIModel):
    query: str
    requested_mode: Literal["lexical", "semantic", "hybrid"]
    effective_mode: Literal["lexical", "semantic", "hybrid"]
    semantic_available: bool
    results: list[SearchResult]
    warning: str | None = None
    core_results: int = Field(default=0, ge=0)
    supplementary_results: int = Field(default=0, ge=0)
    query_embedding_cache_hit: bool = False
    timings: SearchTimings = Field(default_factory=SearchTimings)


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


class TeacherQueryPlan(APIModel):
    intent: TeacherIntent
    language: Literal["de", "es", "mixed", "unknown"] = "unknown"
    target_expression: str | None = Field(default=None, max_length=200)
    user_language: Literal["es", "de"] = "es"
    ambiguity: QueryAmbiguity
    possible_interpretations: list[str] = Field(default_factory=list, max_length=5)
    search_queries: list[str] = Field(min_length=1, max_length=6)
    required_evidence: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("target_expression")
    @classmethod
    def safe_target_expression(cls, value: str | None) -> str | None:
        if value is None:
            return None
        folded = value.casefold()
        if (
            not value.strip()
            or any(ord(character) < 32 for character in value)
            or "../" in value
            or "file://" in folded
            or "/volumes/" in folded
        ):
            raise ValueError("target expression is outside safe editorial limits")
        return value

    @field_validator("search_queries")
    @classmethod
    def safe_search_queries(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            if (
                not value.strip()
                or len(value) > 160
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError("search query is outside safe editorial limits")
            folded = value.casefold()
            if "../" in value or "file://" in folded or "/volumes/" in folded:
                raise ValueError("search query must not contain a filesystem path")
            if value not in cleaned:
                cleaned.append(value)
        return cleaned


class TeacherExample(APIModel):
    german: str = Field(min_length=1, max_length=500)
    spanish: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


class TeacherClaim(APIModel):
    text: str = Field(min_length=1, max_length=2_000)
    source_chunk_ids: list[int] = Field(min_length=1, max_length=8)


class TeacherAnswerDraft(APIModel):
    evidence_sufficient: bool
    direct_answer: str = Field(min_length=1, max_length=5_000)
    key_points: list[str] = Field(default_factory=list, max_length=5)
    examples: list[TeacherExample] = Field(default_factory=list, max_length=5)
    important_nuance: str | None = Field(default=None, max_length=2_000)
    ambiguity_note: str | None = Field(default=None, max_length=1_500)
    follow_up_question: str | None = Field(default=None, max_length=500)
    claims: list[TeacherClaim] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def claims_required_for_grounded_answer(self):
        if self.evidence_sufficient and not self.claims:
            raise ValueError("a grounded answer requires at least one supported claim")
        return self


class TeacherPublicAnswer(APIModel):
    direct_answer: str
    key_points: list[str]
    examples: list[TeacherExample]
    important_nuance: str | None
    ambiguity_note: str | None
    follow_up_question: str | None


class TeacherSourceRead(APIModel):
    citation: str
    source_id: str
    source_name: str
    page_start: int | None
    page_end: int | None
    start_seconds: float | None
    end_seconds: float | None
    section: str | None
    snippet: str = Field(max_length=600)
    review_status: str
    rights: RightsCategory
    extraction_quality: float = Field(ge=0, le=1)
    content_role: str
    retrieval_score: float = Field(ge=0)
    pedagogical_role: PedagogicalRole = PedagogicalRole.UNKNOWN
    evidence_origin: Literal["core", "supplementary"] = "supplementary"
    page_quality: str | None = None


class TeacherTimings(APIModel):
    planning_ms: int = Field(ge=0)
    retrieval_ms: int = Field(ge=0)
    generation_ms: int = Field(ge=0)
    validation_ms: int = Field(ge=0)
    total_ms: int = Field(ge=0)
    embedding_ms: int = Field(default=0, ge=0)
    model_selection_ms: int = Field(default=0, ge=0)
    fts_ms: int = Field(default=0, ge=0)
    vector_ms: int = Field(default=0, ge=0)
    ranking_ms: int = Field(default=0, ge=0)
    repair_ms: int = Field(default=0, ge=0)


class TeacherAskRequest(APIModel):
    question: str = Field(min_length=2, max_length=1_000)
    conversation_id: str | None = Field(default=None, max_length=100)
    source_id: str | None = Field(default=None, max_length=100)
    continuation_action: (
        Literal["expand", "more_examples", "rephrase", "use_in_sentence", "follow_up"] | None
    ) = None

    @field_validator("question")
    @classmethod
    def printable_question(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValueError("question contains control characters")
        return value


class TeacherQueryRead(APIModel):
    query_id: str
    conversation_id: str
    parent_query_id: str | None
    question: str
    status: TeacherQueryStatus
    answer: TeacherPublicAnswer
    confidence: EvidenceConfidence
    sources: list[TeacherSourceRead]
    warnings: list[str]
    retrieval_mode: Literal["lexical", "semantic", "hybrid"]
    semantic_search_available: bool
    timings: TeacherTimings
    created_at: datetime
    failure_reason: TeacherFailureReason | None = None
    models: dict[str, str] = Field(default_factory=dict)
    cache_hit: bool = False
    answer_verified: bool = True


class TeacherStreamEvent(APIModel):
    event: Literal[
        "accepted", "planning", "retrieving", "generating", "provisional", "verified", "error"
    ]
    message: str
    query: TeacherQueryRead | None = None
    failure_reason: TeacherFailureReason | None = None


class TeacherConversationSummary(APIModel):
    conversation_id: str
    latest_query_id: str
    question: str
    answer_excerpt: str
    confidence: EvidenceConfidence
    source_count: int = Field(ge=0)
    turn_count: int = Field(ge=1)
    updated_at: datetime


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
    canonical_title: str | None = Field(default=None, max_length=500)
    display_alias: str | None = Field(default=None, max_length=300)
    author: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=500)
    edition: str | None = Field(default=None, max_length=200)
    cefr_min: str | None = Field(default=None, max_length=20)
    cefr_max: str | None = Field(default=None, max_length=20)
    pedagogical_role: PedagogicalRole | None = None
    editorial_status: EditorialStatus | None = None
    metadata_origin: MetadataOrigin | None = None
    metadata_confidence: float | None = Field(default=None, ge=0, le=1)
    editorial_notes: str | None = Field(default=None, max_length=2_000)
    related_source_id: str | None = Field(default=None, max_length=100)

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


class LibraryTeacherError(LibraryError):
    def __init__(self, reason: TeacherFailureReason, message: str):
        super().__init__(message)
        self.reason = reason


class EditorialSectionRead(APIModel):
    id: int
    source_version_id: int
    stable_key: str
    title: str
    page_start: int
    page_end: int
    cefr_min: str | None
    cefr_max: str | None
    topic: str | None
    content_role: str
    derivation_method: str
    provenance_confidence: float = Field(ge=0, le=1)
    editorial_status: EditorialStatus
    notes: str | None
    related_sections: list[int] = Field(default_factory=list)


class EditorialSectionUpdate(APIModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    cefr_min: str | None = Field(default=None, max_length=20)
    cefr_max: str | None = Field(default=None, max_length=20)
    topic: str | None = Field(default=None, max_length=200)
    content_role: str | None = Field(default=None, max_length=50)
    editorial_status: EditorialStatus | None = None
    notes: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def valid_range(self):
        if self.page_start is not None and self.page_end is not None:
            if self.page_end < self.page_start:
                raise ValueError("page_end must be greater than or equal to page_start")
        if not self.model_fields_set:
            raise ValueError("at least one section field must be provided")
        return self


class EditorialSectionLinkRequest(APIModel):
    target_section_id: int = Field(ge=1)
    relation: Literal["theory_to_practice", "practice_to_theory", "continues", "related"]
    editorial_status: EditorialStatus = EditorialStatus.USER_CONFIRMED


class PageQualityRead(APIModel):
    id: int
    source_version_id: int
    page_number: int
    extraction_method: str
    character_count: int
    detected_language: str | None
    text_density: float
    replacement_ratio: float
    weird_character_ratio: float
    repeated_line_ratio: float
    ordering_warning: bool
    columns_warning: bool
    tables_warning: bool
    damaged_german_ratio: float
    quality: Literal["good", "acceptable", "poor", "unusable"]
    warnings: list[str]
    review_status: str
    reviewed_variant_id: int | None


class PageVariantRead(APIModel):
    id: int
    source_version_id: int
    page_number: int
    method: str
    method_version: str
    text_preview: str
    text_hash: str
    quality_score: float = Field(ge=0, le=1)
    warnings: list[str]
    provenance: dict[str, JsonValue]
    created_at: datetime


class PageReprocessRequest(APIModel):
    method: Literal["pdftotext", "ocr", "vision"]
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class PageVariantReviewRequest(APIModel):
    variant_id: int = Field(ge=1)


class SemanticIndexRequest(APIModel):
    batch_size: int = Field(default=16, ge=1, le=64)
    limit: int | None = Field(default=None, ge=1, le=100_000)
    core_first: bool = True


class ModelRoleRead(APIModel):
    role: Literal["planner", "embedding", "teacher", "fallback", "vision", "repair"]
    configured_model: str
    available: bool
    selected_model: str | None
    fallback_models: list[str] = Field(default_factory=list)


class ModelRoutingRead(APIModel):
    ollama_available: bool
    installed_models: list[str]
    roles: list[ModelRoleRead]
    policy_version: str
