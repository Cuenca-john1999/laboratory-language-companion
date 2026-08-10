from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from llc_api.schemas.base import APIModel


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


class DocumentState(StrEnum):
    UNCHANGED = "unchanged"
    DETECTED = "detected"
    CANDIDATE = "candidate"
    PENDING_EXTRACTION = "pending_extraction"
    NEEDS_OCR = "needs_ocr"
    PROCESSING = "processing"
    PENDING_VALIDATION = "pending_validation"
    READY = "ready"
    ACTIVE = "active"
    HISTORICAL = "historical"
    FAILED = "failed"
    MISSING = "missing"
    MANUAL_REVIEW = "manual_review"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class DocumentRunState(StrEnum):
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"
    SUPERSEDED = "superseded"


class ComparisonState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"
    SUPERSEDED = "superseded"


class DocumentStageState(StrEnum):
    NOT_SCHEDULED = "not_scheduled"
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class PageStageState(StrEnum):
    NOT_SCHEDULED = "not_scheduled"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"
    SUPERSEDED = "superseded"


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


class SourceLookupMode(StrEnum):
    NONE = "none"
    PURE = "pure"
    MIXED = "mixed"
    AMBIGUOUS = "ambiguous"


class SourceLookupStatus(StrEnum):
    VERIFIED_LOCATION = "verified_location"
    CANDIDATE_LOCATIONS = "candidate_locations"
    MULTIPLE_LOCATIONS = "multiple_locations"
    CONFLICT = "conflict"
    STALE = "stale"
    NO_LOCATION = "no_location"
    RETRIEVAL_ERROR = "retrieval_error"


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


class PedagogicalMemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    SYSTEM_VERIFIED = "system_verified"
    USER_CONFIRMED = "user_confirmed"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    STALE = "stale"


class MemoryFeedbackVerdict(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNKNOWN = "unknown"


class MemoryReviewAction(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    UNKNOWN = "unknown"
    POSTPONE = "postpone"


class ScanLayout(StrEnum):
    SINGLE_PAGE = "single_page"
    DOUBLE_PAGE = "double_page"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class EvidenceRegion(StrEnum):
    FULL = "full"
    LEFT = "left"
    RIGHT = "right"
    BOTH = "both"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class ConceptRelationType(StrEnum):
    BROADER_THAN = "broader_than"
    NARROWER_THAN = "narrower_than"
    RELATED_TO = "related_to"
    CONTRASTS_WITH = "contrasts_with"
    PREREQUISITE_OF = "prerequisite_of"
    EXAMPLE_OF = "example_of"
    USED_IN = "used_in"
    COMMONLY_CONFUSED_WITH = "commonly_confused_with"


class ConceptAliasOrigin(StrEnum):
    IMPORTED_SECTION = "imported_section"
    SYSTEM_SUGGESTED = "system_suggested"
    QUERY_DETECTED = "query_detected"
    USER_CREATED = "user_created"
    USER_CONFIRMED = "user_confirmed"


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
    lm_studio_available: bool = False
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


class LaboratorySummary(APIModel):
    catalogued_sources: int = Field(ge=0)
    recoverable_sources: int = Field(ge=0)
    sources_without_content: int = Field(ge=0)
    candidate_versions: int = Field(ge=0)
    needs_ocr_sources: int = Field(ge=0)
    error_sources: int = Field(ge=0)
    missing_files: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)


class DocumentVersionRead(APIModel):
    id: int
    source_id: str
    version_number: int
    content_hash: str
    size_bytes: int
    mtime_ns: int
    observed_path: str | None
    observed_name: str | None
    detected_at: datetime
    page_count: int | None
    document_state: DocumentState
    availability_state: str
    extraction_state: str
    chunk_state: str
    embedding_state: str
    activation_state: str
    is_active: bool
    extractor: str | None
    extractor_version: str | None
    extraction_tool: str | None
    extraction_tool_version: str | None
    ocr_tool: str | None
    ocr_tool_version: str | None
    ocr_languages: list[str]
    technical_metadata: dict[str, JsonValue]
    provenance: str
    change_reason: str | None
    previous_version_id: int | None
    error_code: str | None
    error_detail: str | None
    statistics: dict[str, JsonValue]
    processed_at: datetime | None
    chunks: int = Field(ge=0)
    embeddings: int = Field(ge=0)


class LaboratorySourceRead(APIModel):
    id: str
    title: str
    collection: str | None
    format: str
    current_path: str
    source_status: SourceStatus
    document_state: DocumentState
    needs_manual_review: bool
    active_version_id: int | None
    active_version_number: int | None
    latest_version_id: int | None
    latest_version_number: int | None
    page_count: int | None
    active_chunks: int = Field(ge=0)
    active_embeddings: int = Field(ge=0)
    needs_ocr: bool
    error_code: str | None
    change_pending: bool


class LaboratorySourceDetail(LaboratorySourceRead):
    canonical_title: str | None
    display_alias: str | None
    author: str | None
    publisher: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    versions: list[DocumentVersionRead]


class DocumentRunCreate(APIModel):
    source_version_id: int = Field(gt=0)
    run_type: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_.-]+$")
    pipeline_version: str = Field(min_length=1, max_length=100)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    selection_strategy: Literal[
        "all_pages", "pending_only", "failed_only", "issues_only", "explicit_pages"
    ]
    selected_pages: list[int] = Field(default_factory=list, max_length=5_000)
    initiated_by: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=1_000)
    exclusive: bool = True

    @field_validator("selected_pages")
    @classmethod
    def valid_selected_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("selected pages use one-based PDF numbers")
        return sorted(set(value))

    @model_validator(mode="after")
    def explicit_pages_require_selection(self):
        if self.selection_strategy == "explicit_pages" and not self.selected_pages:
            raise ValueError("explicit_pages requires at least one selected page")
        if self.selection_strategy != "explicit_pages" and self.selected_pages:
            raise ValueError("selected_pages is only valid with explicit_pages")
        return self


class DocumentRunRead(APIModel):
    id: str
    source_id: str
    source_title: str
    source_version_id: int
    source_version_number: int
    target_hash: str
    run_type: str
    pipeline_version: str
    configuration: dict[str, JsonValue]
    configuration_hash: str
    selection_strategy: str
    selected_pages: list[int]
    reused_results: dict[str, JsonValue]
    state: DocumentRunState
    parent_run_id: str | None
    base_run_id: str | None
    initiated_by: str | None
    reason: str | None
    summary: dict[str, JsonValue]
    error_code: str | None
    error_detail: str | None
    resumable: bool
    exclusive: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    current_stage: str | None = None
    issue_count: int = Field(default=0, ge=0)


class DocumentRunStageRead(APIModel):
    id: str
    run_id: str
    name: str
    version: str
    state: DocumentStageState
    attempt: int = Field(gt=0)
    configuration: dict[str, JsonValue]
    metrics: dict[str, JsonValue]
    dependencies: list[str]
    error_code: str | None
    error_detail: str | None
    resumable: bool
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CoverageSnapshotRead(APIModel):
    id: str
    run_id: str
    source_version_id: int
    stage_name: str
    dimension: str
    execution_status: Literal["executed", "not_executed", "no_data"]
    denominator: int | None = Field(default=None, ge=0)
    completed: int | None = Field(default=None, ge=0)
    with_issues: int | None = Field(default=None, ge=0)
    failed: int | None = Field(default=None, ge=0)
    pending: int | None = Field(default=None, ge=0)
    not_applicable: int | None = Field(default=None, ge=0)
    unknown: int | None = Field(default=None, ge=0)
    breakdown: dict[str, JsonValue]
    provenance: dict[str, JsonValue]
    captured_at: datetime


class DocumentPageRead(APIModel):
    id: int
    source_version_id: int
    pdf_page_number: int = Field(gt=0)
    printed_page_number: str | None
    fingerprint: str | None
    width_points: float | None
    height_points: float | None
    rotation_degrees: int | None
    has_text: bool | None
    character_count: int | None = Field(default=None, ge=0)
    text_quality: str | None
    layout_state: str | None
    structure_state: str | None
    review_state: str | None
    issue_count: int = Field(ge=0)
    stage_states: dict[str, PageStageState]


class DocumentPageList(APIModel):
    items: list[DocumentPageRead]
    page: int = Field(gt=0)
    page_size: int = Field(gt=0)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class DocumentRunIssueRead(APIModel):
    id: str
    run_id: str
    pdf_page_number: int | None
    stage_name: str | None
    code: str
    severity: str
    message: str
    evidence: dict[str, JsonValue]
    resolved_at: datetime | None
    created_at: datetime


class DocumentRunEventRead(APIModel):
    id: str
    run_id: str
    event_type: str
    actor: str | None
    from_state: str | None
    to_state: str | None
    detail: dict[str, JsonValue]
    created_at: datetime


class DocumentRunDetail(DocumentRunRead):
    stages: list[DocumentRunStageRead]
    coverage: list[CoverageSnapshotRead]
    events: list[DocumentRunEventRead]


class DocumentRunPassCreate(APIModel):
    reason: str | None = Field(default=None, max_length=1_000)
    initiated_by: str | None = Field(default=None, max_length=120)


class DocumentStageRetryRequest(APIModel):
    failed_pages_only: bool = True


class DocumentPageRepeatRequest(APIModel):
    pages: list[int] = Field(min_length=1, max_length=500)
    reason: str | None = Field(default=None, max_length=1_000)
    initiated_by: str | None = Field(default=None, max_length=120)

    @field_validator("pages")
    @classmethod
    def valid_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("pages use one-based PDF numbers")
        return sorted(set(value))


class DocumentPageBlockRead(APIModel):
    id: str
    page_id: int
    run_id: str
    stage: str
    block_index: int = Field(ge=0)
    block_type: str
    subtype: str | None
    raw_text: str | None
    normalized_layout_text: str | None
    bbox: list[float] | None
    reading_order: int = Field(ge=0)
    column_index: int | None
    confidence: float = Field(ge=0, le=1)
    evidence: dict[str, JsonValue]
    issues: list[JsonValue]
    extractor: str
    extractor_version: str


class StructureCandidateRead(APIModel):
    id: str
    source_id: str
    source_version_id: int
    page_id: int
    pdf_page_number: int = Field(gt=0)
    run_id: str
    block_id: str | None
    stage: str
    candidate_type: str
    subtype: str | None
    raw_text: str | None
    normalized_layout_text: str | None
    correction_candidate: str | None
    correction_reason: str | None
    position: dict[str, JsonValue]
    bbox: list[float] | None
    reading_order: int = Field(ge=0)
    parent_candidate_id: str | None
    hierarchy_level: int | None
    observed_pedagogical_level: str | None
    observed_topic: str | None
    canonical_match_state: str | None
    canonical_topic_number: int | None
    proposed_printed_page: str | None
    confidence: float = Field(ge=0, le=1)
    status: str
    evidence: dict[str, JsonValue]
    extractor: str
    extractor_version: str
    configuration: dict[str, JsonValue]
    issues: list[JsonValue]
    needs_review: bool
    created_at: datetime
    updated_at: datetime


class StructureCandidateList(APIModel):
    items: list[StructureCandidateRead]
    page: int = Field(gt=0)
    page_size: int = Field(gt=0)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class ProposedHierarchyNode(APIModel):
    candidate: StructureCandidateRead
    children: list[ProposedHierarchyNode] = Field(default_factory=list)


class DocumentReviewRunRead(APIModel):
    id: str
    source_id: str
    source_version_id: int
    source_hash: str
    rule_version: str
    configuration_hash: str
    state: str
    configuration: dict[str, JsonValue]
    before_metrics: dict[str, JsonValue]
    after_metrics: dict[str, JsonValue]
    error_code: str | None
    error_detail: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentReviewSummaryItem(APIModel):
    source_id: str
    source_version_id: int
    source_title: str
    version_number: int
    hash: str
    activation_state: str
    run_id: str | None
    run_state: str
    readiness: str
    metrics: dict[str, JsonValue]
    queue: dict[str, int]
    blockers: list[JsonValue]


class DocumentReviewSummary(APIModel):
    items: list[DocumentReviewSummaryItem]


class DocumentReviewQueueItem(APIModel):
    id: str
    source_id: str
    source_version_id: int
    candidate_type: str
    raw_text: str | None
    canonical_topic_number: int | None
    observed_pedagogical_level: str | None
    confidence: float
    pdf_page_number: int
    editorial_state: str
    effective_confidence: float
    priority: Literal["P0", "P1", "P2"]
    reason: str | None
    primary_candidate_id: str | None
    has_issue: bool


class DocumentReviewQueue(APIModel):
    items: list[DocumentReviewQueueItem]
    page: int = Field(gt=0)
    page_size: int = Field(gt=0)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class CandidateDecisionRequest(APIModel):
    action: Literal["support", "confirm", "reject", "conflict", "supersede"]
    actor: str = Field(default="local_user", min_length=1, max_length=120)
    method: Literal["visual_review", "manual_review", "batch_manual", "external_audit"] = (
        "manual_review"
    )
    comment: str | None = Field(default=None, max_length=2_000)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)


class CandidateDecisionRead(APIModel):
    id: str
    candidate_id: str
    review_run_id: str | None
    actor: str
    method: str
    rule: str
    previous_state: str
    new_state: str
    confidence_before: float
    confidence_after: float
    batch_id: str | None
    evidence: dict[str, JsonValue]
    comment: str | None
    reverts_decision_id: str | None
    created_at: datetime


class CandidateDecisionRevertRequest(APIModel):
    actor: str = Field(default="local_user", min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=2_000)


class DocumentReviewBatchCreate(APIModel):
    candidate_ids: list[str] = Field(min_length=2, max_length=500)
    action: Literal["support", "confirm", "reject", "conflict", "supersede"]
    minimum_confidence: float = Field(ge=0, le=1)

    @field_validator("candidate_ids")
    @classmethod
    def unique_candidates(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("candidate_ids must be unique")
        return value


class DocumentReviewBatchAction(APIModel):
    actor: str = Field(default="local_user", min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=2_000)


class DocumentReviewBatchRead(APIModel):
    id: str
    review_run_id: str | None
    source_version_id: int
    rule: str
    candidate_type: str
    proposed_action: str
    minimum_confidence: float
    member_count: int
    pages: list[int]
    sample: list[JsonValue]
    effect: dict[str, JsonValue]
    state: str
    created_at: datetime
    applied_at: datetime | None
    reverted_at: datetime | None


class ConsolidatedTopicRead(APIModel):
    id: str
    review_run_id: str
    source_version_id: int
    theme_number: int = Field(ge=1, le=51)
    state: str
    identity_resolution: Literal["unresolved", "body_practice", "index_only_no_direct_practice"]
    identity_evidence_candidate_id: str | None
    resolution_method: str | None
    primary_candidate_id: str | None
    alternative_candidate_ids: list[str]
    pdf_page_number: int | None
    printed_page: str | None
    title_es_observed: str | None
    title_de_observed: str | None
    observed_level: str | None
    confidence: float
    evidence: dict[str, JsonValue]
    issues: list[JsonValue]
    created_at: datetime


class ConsolidatedNodeRead(APIModel):
    id: str
    review_run_id: str
    source_version_id: int
    source_candidate_id: str | None
    parent_node_id: str | None
    node_type: str
    theme_number: int | None
    observed_level: str | None
    pdf_page_number: int
    reading_order: int
    state: str
    raw_text: str | None
    evidence: dict[str, JsonValue]
    issues: list[JsonValue]
    created_at: datetime
    children: list[ConsolidatedNodeRead] = Field(default_factory=list)


class ExerciseSolutionRelationRead(APIModel):
    id: str
    review_run_id: str
    source_version_id: int
    exercise_candidate_ids: list[str]
    solution_candidate_ids: list[str]
    relation_type: str
    state: str
    confidence: float
    evidence: dict[str, JsonValue]
    issues: list[JsonValue]
    created_at: datetime


class DocumentReadinessRead(APIModel):
    id: str | None = None
    review_run_id: str | None = None
    source_version_id: int
    state: str
    metrics: dict[str, JsonValue]
    blockers: list[JsonValue]
    rationale: dict[str, JsonValue]
    created_at: datetime | None = None


class AuditExportRequest(APIModel):
    mode: Literal["full", "review_only", "targeted"]
    selection: dict[str, JsonValue] = Field(default_factory=dict)
    include_visuals: bool = False


class AuditExportPreview(APIModel):
    source_version_id: int
    mode: str
    selection: dict[str, JsonValue]
    include_visuals: bool
    counts: dict[str, int]
    estimated_size_bytes: int
    visual_asset_count: int
    affected_pages: list[int]
    readiness: str
    ai_readiness: str


class AuditExportRead(APIModel):
    id: str
    source_id: str
    source_version_id: int
    document_hash: str
    review_run_id: str | None
    package_schema: str
    export_mode: str
    selection: dict[str, JsonValue]
    include_visuals: bool
    state: str
    relative_path: str | None
    manifest: dict[str, JsonValue]
    logical_hash: str | None
    archive_sha256: str | None
    size_bytes: int | None
    counts: dict[str, int]
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class AuditDecision(APIModel):
    target_type: Literal["candidate", "topic", "relation", "visual"]
    target_id: str = Field(min_length=1)
    expected_previous_state: str = Field(min_length=1)
    action: Literal[
        "confirm",
        "reject",
        "support",
        "mark_conflicted",
        "choose_primary",
        "change_type",
        "change_parent",
        "adjust_printed_page",
        "confirm_relation",
        "reject_relation",
        "classify_visual",
        "resolve_topic_identity",
    ]
    replacement: dict[str, JsonValue] | None = None
    evidence_references: list[str]
    rationale: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(ge=0, le=1)
    note: str | None = Field(default=None, max_length=2_000)


class AuditDecisionSet(APIModel):
    decision_schema: Literal["audit-decisions.v1"]
    package_logical_hash: str = Field(min_length=64, max_length=64)
    source_id: str
    source_version_id: int
    document_hash: str = Field(min_length=64, max_length=64)
    reviewer: str = Field(min_length=1, max_length=200)
    reviewed_at: datetime
    decisions: list[AuditDecision] = Field(min_length=1, max_length=1_000)


class AuditValidationRead(APIModel):
    valid: bool
    decision_set_hash: str
    applicable: list[JsonValue]
    stale: list[JsonValue]
    conflicts: list[JsonValue]
    invalid: list[JsonValue]
    no_effect: list[JsonValue]
    estimated_impact: dict[str, JsonValue]


class AuditImportRead(APIModel):
    id: str
    export_id: str | None
    source_id: str
    source_version_id: int
    document_hash: str
    package_logical_hash: str
    decision_schema: str
    decision_set_hash: str
    reviewer: str
    reviewed_at: datetime
    mode: str
    state: str
    decision_count: int
    applied_count: int
    validation: dict[str, JsonValue]
    impact: dict[str, JsonValue]
    created_at: datetime
    applied_at: datetime | None
    decisions: list[dict[str, JsonValue]] = Field(default_factory=list)


class ClosureStatusRead(APIModel):
    source_id: str
    source_version_id: int
    source_title: str
    document_hash: str
    activation_state: str
    structural_readiness: str
    ai_readiness: str
    blockers: list[JsonValue]
    queue: dict[str, int]
    unresolved_topics: int
    visual_pending: int
    latest_snapshot: dict[str, JsonValue] | None


class ClosureSnapshotRead(APIModel):
    id: str
    source_id: str
    source_version_id: int
    document_hash: str
    review_run_id: str | None
    structural_readiness: str
    ai_readiness: str
    state: str
    topic_revision: str
    hierarchy_revision: str
    review_revision: str
    relation_revision: str
    coverage: dict[str, JsonValue]
    unresolved: list[JsonValue]
    blocking: list[JsonValue]
    pipeline_versions: dict[str, JsonValue]
    snapshot_payload: dict[str, JsonValue]
    snapshot_hash: str
    created_at: datetime


class VersionComparisonCreate(APIModel):
    base_source_version_id: int = Field(gt=0)
    target_source_version_id: int = Field(gt=0)
    algorithm_version: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_.-]+$")
    configuration: dict[str, JsonValue]
    initiated_by: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def versions_must_differ(self):
        if self.base_source_version_id == self.target_source_version_id:
            raise ValueError("base and target versions must differ")
        if self.configuration.get("ocr") or self.configuration.get("use_ocr"):
            raise ValueError("comparison never permits OCR")
        return self


class VersionComparisonRead(APIModel):
    id: str
    source_id: str
    source_title: str
    base_source_version_id: int
    base_version_number: int
    target_source_version_id: int
    target_version_number: int
    base_hash: str
    target_hash: str
    algorithm_version: str
    configuration: dict[str, JsonValue]
    configuration_hash: str
    revision: int = Field(gt=0)
    supersedes_comparison_id: str | None
    state: ComparisonState
    initiated_by: str | None
    summary: dict[str, JsonValue]
    error_code: str | None
    error_detail: str | None
    needs_review: bool
    transfer_plan_revision: int = Field(ge=0)
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class CorrespondenceRead(APIModel):
    id: str
    comparison_id: str
    relation_type: str
    review_state: str
    confidence: str
    base_pages: list[int]
    target_pages: list[int]
    text_similarity: float | None
    visual_similarity: float | None
    geometry_similarity: float | None
    ordinal_similarity: float | None
    printed_page_similarity: float | None
    split_similarity: float | None
    aggregate_score: float
    evidence: dict[str, JsonValue]
    text_difference: dict[str, JsonValue]
    recommendation: str
    review_note: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class CorrespondenceList(APIModel):
    items: list[CorrespondenceRead]
    page: int = Field(gt=0)
    page_size: int = Field(gt=0)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class CorrespondenceDecision(APIModel):
    actor: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1_000)


class CorrespondenceAdjust(CorrespondenceDecision):
    base_pages: list[int] = Field(default_factory=list, max_length=100)
    target_pages: list[int] = Field(default_factory=list, max_length=100)
    relation_type: Literal[
        "one_to_one",
        "one_to_many",
        "many_to_one",
        "many_to_many",
        "inserted",
        "deleted",
        "blank",
        "duplicate",
        "unresolved",
        "ambiguous",
    ]

    @field_validator("base_pages", "target_pages")
    @classmethod
    def valid_page_numbers(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value) or len(value) != len(set(value)):
            raise ValueError("page numbers must be unique and one-based")
        return value


class NoEquivalentRequest(APIModel):
    side: Literal["base", "target"]
    page_number: int = Field(gt=0)
    reason: Literal["inserted", "deleted", "blank", "duplicate", "unresolved"]
    actor: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1_000)


class ComparisonEventRead(APIModel):
    id: str
    comparison_id: str
    correspondence_id: str | None
    event_type: str
    actor: str | None
    previous_state: str | None
    new_state: str | None
    detail: dict[str, JsonValue]
    created_at: datetime


class TransferPlanRead(APIModel):
    id: str
    comparison_id: str
    revision: int
    state: str
    plan: dict[str, JsonValue]
    created_at: datetime
    invalidated_at: datetime | None


class InventoryChangeRead(APIModel):
    outcome: Literal[
        "unchanged",
        "modified",
        "new",
        "renamed",
        "missing",
        "duplicate",
        "manual_review",
    ]
    source_id: str
    relative_path: str
    title: str
    previous_hash: str | None
    current_hash: str | None
    active_version_id: int | None
    candidate_version_id: int | None
    message: str


class DocumentInventoryResult(APIModel):
    job_id: str
    generated_at: datetime
    files_scanned: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    modified: int = Field(ge=0)
    new: int = Field(ge=0)
    renamed: int = Field(ge=0)
    missing: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    manual_review: int = Field(ge=0)
    candidate_versions_created: int = Field(ge=0)
    changes: list[InventoryChangeRead]


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


class SourceLookupConceptRead(APIModel):
    concept_id: str | None = None
    canonical_name: str
    display_name_es: str | None = None
    display_name_de: str | None = None


class SourceLookupLocationRead(APIModel):
    location_id: str
    source_id: str
    source_name: str
    source_role: PedagogicalRole
    source_version: int = Field(ge=1)
    pdf_page: int | None = Field(default=None, ge=1)
    printed_page: str | None = None
    scan_layout: ScanLayout
    region: EvidenceRegion
    heading: str | None = None
    review_status: PedagogicalMemoryStatus
    citation: str
    snippet: str = Field(max_length=600)
    provenance: Literal["memory", "hybrid"]


class SourceLookupRead(APIModel):
    status: SourceLookupStatus
    concept: SourceLookupConceptRead | None = None
    summary: str = Field(min_length=1, max_length=5_000)
    locations: list[SourceLookupLocationRead] = Field(default_factory=list, max_length=10)
    available_location_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=10)
    memory_hit: bool = False
    lookup_cache_hit: bool = False
    hybrid_fallback: bool = False
    used_generation: bool = False


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
    answer_kind: Literal["teacher_answer", "source_lookup", "teacher_answer_with_source_lookup"] = (
        "teacher_answer"
    )
    source_lookup: SourceLookupRead | None = None
    used_generation: bool = True

    @model_validator(mode="after")
    def claims_required_for_grounded_answer(self):
        if self.evidence_sufficient and self.source_lookup is None and not self.claims:
            raise ValueError("a grounded answer requires at least one supported claim")
        if self.answer_kind != "teacher_answer" and self.source_lookup is None:
            raise ValueError("source lookup answers require a structured lookup result")
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
    location_id: str | None = None
    public_location: str | None = None
    memory_status: PedagogicalMemoryStatus | None = None
    printed_page_label: str | None = None
    scan_layout: ScanLayout = ScanLayout.UNKNOWN
    region: EvidenceRegion = EvidenceRegion.UNKNOWN


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
    intent_detection_ms: int = Field(default=0, ge=0)
    memory_lookup_ms: int = Field(default=0, ge=0)
    hybrid_fallback_ms: int = Field(default=0, ge=0)


class StudyTeacherContext(APIModel):
    session_id: str = Field(min_length=1, max_length=36)
    section_title: str = Field(min_length=1, max_length=500)
    concept_name: str | None = Field(default=None, max_length=300)
    source_name: str | None = Field(default=None, max_length=500)
    pdf_page_start: int | None = Field(default=None, ge=1)
    pdf_page_end: int | None = Field(default=None, ge=1)
    current_pdf_page: int | None = Field(default=None, ge=1)
    printed_page_label: str | None = Field(default=None, max_length=100)
    mission_type: str | None = Field(default=None, max_length=40)
    preferred_language: Literal["es-ES"] = "es-ES"
    selected_notes: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("selected_notes")
    @classmethod
    def bounded_selected_notes(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 2_000 for value in values):
            raise ValueError("selected study notes must contain 1 to 2000 characters")
        return values


class TeacherAskRequest(APIModel):
    question: str = Field(min_length=2, max_length=1_000)
    conversation_id: str | None = Field(default=None, max_length=100)
    source_id: str | None = Field(default=None, max_length=100)
    continuation_action: (
        Literal["expand", "more_examples", "rephrase", "use_in_sentence", "follow_up"] | None
    ) = None
    study_context: StudyTeacherContext | None = None

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
    memory_used: bool = False
    response_feedback: MemoryFeedbackVerdict | None = None
    answer_kind: Literal["teacher_answer", "source_lookup", "teacher_answer_with_source_lookup"] = (
        "teacher_answer"
    )
    source_lookup: SourceLookupRead | None = None
    used_generation: bool = True
    memory_hit: bool = False
    lookup_cache_hit: bool = False
    hybrid_fallback: bool = False
    answer_cache_hit: bool = False


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
    role: Literal["planner", "embedding", "teacher", "deep", "vision", "repair"]
    configured_model: str
    available: bool
    selected_model: str | None
    fallback_models: list[str] = Field(default_factory=list)


class ModelRoutingRead(APIModel):
    lm_studio_available: bool
    installed_models: list[str]
    roles: list[ModelRoleRead]
    policy_version: str


class NormalizedBBox(APIModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside_page(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("custom region must fit inside the page")
        return self


class ConceptAliasRead(APIModel):
    id: int
    text: str
    language: str
    normalized_text: str
    origin: ConceptAliasOrigin
    status: PedagogicalMemoryStatus
    confidence: Literal["low", "moderate", "high"]
    user_confirmed: bool


class ConceptRelationRead(APIModel):
    id: int
    source_concept_id: str
    target_concept_id: str
    target_name: str
    relation_type: ConceptRelationType
    status: PedagogicalMemoryStatus
    origin: str


class PageMappingRead(APIModel):
    id: int
    source_version_id: int
    pdf_page_index: int = Field(ge=0)
    pdf_page_number: int = Field(ge=1)
    scan_layout: ScanLayout
    printed_left_label: str | None
    printed_right_label: str | None
    printed_full_label: str | None
    rotation: Literal[0, 90, 180, 270]
    status: PedagogicalMemoryStatus
    origin: str
    mapping_version: int = Field(ge=1)
    reviewed_at: datetime | None


class EvidenceLocationRead(APIModel):
    id: str
    concept_id: str
    concept_name: str
    source_id: str
    source_name: str
    source_role: PedagogicalRole = PedagogicalRole.UNKNOWN
    source_version: int = Field(ge=1)
    source_current: bool = True
    source_present: bool = True
    chunk_id: int | None
    pdf_page_number: int | None = Field(default=None, ge=1)
    printed_page_label: str | None
    scan_layout: ScanLayout
    region: EvidenceRegion
    custom_bbox: NormalizedBBox | None = None
    heading: str | None
    evidence_snippet: str = Field(max_length=600)
    extraction_quality: float = Field(ge=0, le=1)
    status: PedagogicalMemoryStatus
    origin: str
    public_citation: str
    reviewed_at: datetime | None


class PedagogicalConceptSummary(APIModel):
    id: str
    canonical_name: str
    language: str
    display_name_es: str | None
    display_name_de: str | None
    category: str
    description: str | None
    status: PedagogicalMemoryStatus
    aliases: list[ConceptAliasRead] = Field(default_factory=list)
    location_counts: dict[str, int] = Field(default_factory=dict)
    updated_at: datetime


class PedagogicalConceptRead(PedagogicalConceptSummary):
    relations: list[ConceptRelationRead] = Field(default_factory=list)
    locations: list[EvidenceLocationRead] = Field(default_factory=list)
    query_ids: list[str] = Field(default_factory=list)


class PedagogicalMemorySummary(APIModel):
    concepts: int = Field(ge=0)
    aliases: int = Field(ge=0)
    locations: int = Field(ge=0)
    relations: int = Field(ge=0)
    by_status: dict[str, int]
    pending_review: int = Field(ge=0)


class PedagogicalConceptCreate(APIModel):
    canonical_name: str = Field(min_length=1, max_length=200)
    language: str = Field(default="de", min_length=2, max_length=20)
    display_name_es: str | None = Field(default=None, max_length=200)
    display_name_de: str | None = Field(default=None, max_length=200)
    category: str = Field(default="other", min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=1_000)
    status: PedagogicalMemoryStatus = PedagogicalMemoryStatus.CANDIDATE
    origin: ConceptAliasOrigin = ConceptAliasOrigin.USER_CREATED


class ConceptAliasCreate(APIModel):
    text: str = Field(min_length=1, max_length=200)
    language: str = Field(min_length=2, max_length=20)
    origin: ConceptAliasOrigin = ConceptAliasOrigin.USER_CREATED
    status: PedagogicalMemoryStatus = PedagogicalMemoryStatus.CANDIDATE
    confidence: Literal["low", "moderate", "high"] = "moderate"


class ConceptRelationCreate(APIModel):
    target_concept_id: str = Field(min_length=8, max_length=100)
    relation_type: ConceptRelationType
    origin: str = Field(default="user_created", min_length=2, max_length=80)
    status: PedagogicalMemoryStatus = PedagogicalMemoryStatus.CANDIDATE


class PageMappingUpdate(APIModel):
    pdf_page_number: int = Field(ge=1)
    scan_layout: ScanLayout
    printed_left_label: str | None = Field(default=None, max_length=50)
    printed_right_label: str | None = Field(default=None, max_length=50)
    printed_full_label: str | None = Field(default=None, max_length=50)
    rotation: Literal[0, 90, 180, 270] = 0
    status: PedagogicalMemoryStatus = PedagogicalMemoryStatus.CANDIDATE
    origin: str = Field(default="user", min_length=2, max_length=80)
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="after")
    def labels_fit_layout(self):
        if self.scan_layout == ScanLayout.SINGLE_PAGE and (
            self.printed_left_label or self.printed_right_label
        ):
            raise ValueError("single-page scans use printed_full_label")
        return self


class EvidenceLocationCreate(APIModel):
    concept_id: str = Field(min_length=8, max_length=100)
    source_version_id: int = Field(ge=1)
    chunk_id: int | None = Field(default=None, ge=1)
    editorial_section_id: int | None = Field(default=None, ge=1)
    pdf_page_number: int | None = Field(default=None, ge=1)
    region: EvidenceRegion = EvidenceRegion.UNKNOWN
    custom_bbox: NormalizedBBox | None = None
    heading: str | None = Field(default=None, max_length=500)
    evidence_snippet: str = Field(default="", max_length=600)
    extraction_quality: float = Field(default=0, ge=0, le=1)
    status: PedagogicalMemoryStatus = PedagogicalMemoryStatus.CANDIDATE
    origin: str = Field(min_length=2, max_length=80)

    @model_validator(mode="after")
    def custom_region_has_bbox(self):
        if (self.region == EvidenceRegion.CUSTOM) != (self.custom_bbox is not None):
            raise ValueError("custom regions require exactly one normalized bounding box")
        return self


class MemoryFeedbackRequest(APIModel):
    verdict: MemoryFeedbackVerdict
    comment: str | None = Field(default=None, max_length=2_000)
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class LocationFeedbackRequest(MemoryFeedbackRequest):
    region: EvidenceRegion | None = None
    printed_left_label: str | None = Field(default=None, max_length=50)
    printed_right_label: str | None = Field(default=None, max_length=50)
    printed_full_label: str | None = Field(default=None, max_length=50)
    scan_layout: ScanLayout | None = None
    suggested_concept_name: str | None = Field(default=None, max_length=200)


class MemoryFeedbackRead(APIModel):
    review_id: str
    target_type: Literal["response", "location", "concept", "relation", "page_mapping"]
    target_id: str
    verdict: str
    resulting_status: PedagogicalMemoryStatus | None
    created_at: datetime


class MemoryReviewRequest(APIModel):
    action: MemoryReviewAction
    comment: str | None = Field(default=None, max_length=2_000)
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class MemoryRevertRequest(APIModel):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    comment: str | None = Field(default=None, max_length=2_000)


class MemoryAuditRead(APIModel):
    id: int
    operation_id: str
    actor: str
    action: str
    target_type: str
    target_id: str
    query_id: str | None
    before: dict[str, JsonValue]
    after: dict[str, JsonValue]
    comment: str | None
    created_at: datetime


class MemoryReviewQueueItem(APIModel):
    target_type: Literal["concept", "location", "relation"]
    target_id: str
    title: str
    subtitle: str | None
    status: PedagogicalMemoryStatus
    priority: int
    used_by_queries: int = Field(ge=0)
    source_role: PedagogicalRole | None = None
    last_used_at: datetime | None = None


class PedagogicalMemoryImportRequest(APIModel):
    include_sections: bool = True
    include_knowledge: bool = True
    include_teacher_history: bool = True
    confirm_herder_akkusativ_pdf_89: bool = False
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class PedagogicalMemoryImportRead(APIModel):
    concepts_created: int = Field(ge=0)
    aliases_created: int = Field(ge=0)
    locations_created: int = Field(ge=0)
    query_links_created: int = Field(ge=0)
    mappings_created: int = Field(ge=0)
    skipped: int = Field(ge=0)
    idempotent_replay: bool = False


class QueryMemoryRead(APIModel):
    query_id: str
    concepts: list[PedagogicalConceptSummary]
    locations: list[EvidenceLocationRead]
    response_feedback: MemoryFeedbackVerdict | None
    memory_used: bool


class ReferenceIndexEntry(APIModel):
    hierarchy_level: Literal[
        "top_level_theme", "section", "subsection", "item", "front_matter", "back_matter"
    ]
    theme_number: int | None = Field(default=None, ge=1, le=51)
    local_number: str | None = Field(default=None, max_length=30)
    parent_path: list[str] = Field(default_factory=list, max_length=12)
    title_es: str | None = Field(default=None, max_length=1_000)
    title_de: str | None = Field(default=None, max_length=1_000)
    printed_page_label: str | None = Field(default=None, max_length=30)
    raw_visible_text: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(ge=0, le=1)
    visual_region: Literal["top", "middle", "bottom", "unknown"] = "unknown"
    notes: str | None = Field(default=None, max_length=2_000)
    parse_status: Literal["verified", "uncertain", "cropped"]
    editorial_status: Literal["verified", "user_confirmed", "needs_review"] = "verified"


class ReferenceIndexPage(APIModel):
    reference_pdf_page: int = Field(ge=1)
    physical_index_page: int | None = Field(default=None, ge=1)
    entries: list[ReferenceIndexEntry] = Field(max_length=250)


class CanonicalRouteValidationRead(APIModel):
    valid: bool
    theme_count: int = Field(ge=0)
    missing_theme_numbers: list[int]
    duplicate_theme_numbers: list[int]
    sequence_monotonic: bool
    printed_pages_monotonic: bool
    canonical_checks: dict[str, bool]
    errors: list[str]
    warnings: list[str]


class CanonicalOutlineNodeRead(APIModel):
    id: int
    parent_id: int | None
    hierarchy_level: str
    local_number: str | None
    title_es: str | None
    title_de: str | None
    printed_page: int | None
    reference_pdf_page: int
    visual_region: str
    parse_status: str
    manual_pdf_page: int | None
    manual_scan_layout: str | None
    manual_region: str | None
    editorial_status: str
    confidence: float


class CanonicalTopicRead(APIModel):
    id: int
    stable_key: str
    theme_number: int
    title_es: str
    title_de: str | None
    printed_start: int
    printed_end: int | None
    printed_end_origin: str
    reference_pdf_page: int
    reference_visual_region: str
    manual_pdf_start: int | None
    manual_pdf_end: int | None
    manual_scan_layout: str | None
    manual_region: str | None
    editorial_status: str
    origin: str
    confidence: float
    outline: list[CanonicalOutlineNodeRead] = Field(default_factory=list)


class CanonicalLegacyMappingRead(APIModel):
    id: int
    legacy_section_id: int
    legacy_stable_key: str
    legacy_title: str
    canonical_topic_id: int | None
    theme_number: int | None
    mapping_status: Literal["exact", "probable", "ambiguous", "unmatched", "rejected"]
    score: float
    evidence: list[str]
    reason: str
    reviewed_at: datetime | None


class CanonicalRouteImportRead(APIModel):
    id: str
    source_id: str
    source_version_id: int
    route_version: int
    reference_name: str
    reference_sha256_prefix: str
    reference_size_bytes: int
    reference_page_count: int
    model: str
    parser_version: str
    status: str
    editorial_status: str
    origin: str
    active: bool
    validation: CanonicalRouteValidationRead
    warnings: list[str]
    statistics: dict[str, JsonValue]
    imported_at: datetime
    validated_at: datetime | None
    idempotent_replay: bool = False


class CanonicalRouteStatusRead(APIModel):
    available: bool
    active_import: CanonicalRouteImportRead | None
    topic_count: int = Field(ge=0)
    outline_count: int = Field(ge=0)
    mapping_counts: dict[str, int]
    pending_reviews: int = Field(ge=0)


class CanonicalTopicReviewRequest(APIModel):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["confirm", "correct", "incorrect", "unknown"]
    title_es: str | None = Field(default=None, min_length=1, max_length=1_000)
    title_de: str | None = Field(default=None, max_length=1_000)
    printed_start: int | None = Field(default=None, ge=1)
    printed_end: int | None = Field(default=None, ge=1)
    manual_pdf_start: int | None = Field(default=None, ge=1)
    manual_pdf_end: int | None = Field(default=None, ge=1)
    manual_scan_layout: Literal["single_page", "double_page", "mixed", "unknown"] | None = None
    manual_region: Literal["full", "left", "right", "both", "unknown"] | None = None
    comment: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def correction_has_payload(self):
        if self.action == "correct" and all(
            value is None
            for value in (
                self.title_es,
                self.title_de,
                self.printed_start,
                self.printed_end,
                self.manual_pdf_start,
                self.manual_pdf_end,
                self.manual_scan_layout,
                self.manual_region,
            )
        ):
            raise ValueError("a correction requires at least one changed field")
        return self


class CanonicalRouteRevertRequest(APIModel):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    audit_id: int = Field(ge=1)
    comment: str | None = Field(default=None, max_length=2_000)


class CanonicalRouteAuditRead(APIModel):
    id: int
    operation_id: str
    action: str
    target_type: str
    target_id: str
    before: dict[str, JsonValue]
    after: dict[str, JsonValue]
    comment: str | None
    reverts_audit_id: int | None
    created_at: datetime
