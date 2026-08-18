from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from llc_api.schemas.base import APIModel


class CloudExtractionMode(StrEnum):
    STRUCTURE = "structure"
    CONTENT = "content"


class ProviderAvailability(StrEnum):
    AVAILABLE = "available"
    TEMPORARILY_LIMITED = "temporarily_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION_ERROR = "authentication_error"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN = "unknown"


class ValidationOutcome(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class AssertionOrigin(StrEnum):
    EXTRACTED = "extracted"
    INFERRED = "inferred"


class ProviderCapabilities(APIModel):
    supports_pdf: bool = False
    supports_images: bool = False
    supports_structured_output: bool = False
    supports_file_upload: bool = False
    supports_thinking_control: bool = False
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)

    def missing(self, required: CapabilityRequirements) -> list[str]:
        missing: list[str] = []
        for name in (
            "supports_pdf",
            "supports_images",
            "supports_structured_output",
            "supports_file_upload",
            "supports_thinking_control",
        ):
            if getattr(required, name) and not getattr(self, name):
                missing.append(name)
        return missing


class CapabilityRequirements(APIModel):
    supports_pdf: bool = False
    supports_images: bool = False
    supports_structured_output: bool = True
    supports_file_upload: bool = False
    supports_thinking_control: bool = False


class CloudProviderDescriptor(APIModel):
    provider_id: str
    display_name: str
    model: str
    availability: ProviderAvailability
    configured: bool
    capabilities: ProviderCapabilities


class CloudExtractionCreate(APIModel):
    source_version_id: int = Field(gt=0)
    mode: CloudExtractionMode
    provider: str = Field(default="auto", min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    pages: list[int] = Field(default_factory=list, max_length=1_000)
    initiated_by: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def normalize_pages(self):
        if any(page < 1 for page in self.pages):
            raise ValueError("pages use one-based physical PDF numbers")
        self.pages = sorted(set(self.pages))
        return self


class ProviderExtractionRequest(APIModel):
    run_id: str
    source_id: str
    source_version_id: int
    document_path: Path
    mime_type: str
    mode: CloudExtractionMode
    model: str
    pages: list[int]
    prompt: str
    response_schema: dict[str, JsonValue]


class ProviderUsage(APIModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    thinking_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ProviderRawResponse(APIModel):
    text: str
    raw: dict[str, JsonValue]
    usage: ProviderUsage = Field(default_factory=ProviderUsage)
    provider_metadata: dict[str, JsonValue] = Field(default_factory=dict)


STRUCTURE_KINDS = Literal[
    "front_matter",
    "back_matter",
    "topic",
    "division",
    "numbered_entry",
    "lettered_entry",
    "note",
    "continuation",
    "other",
]


class CompactStructureEntry(APIModel):
    k: STRUCTURE_KINDS
    p: int = Field(ge=1)
    o: int = Field(ge=1)
    x: str = Field(min_length=1, max_length=2_000)
    n: str | None = Field(default=None, max_length=160)
    t: str | None = Field(default=None, max_length=1_000)
    pp: str | None = Field(default=None, max_length=80)
    po: int | None = Field(default=None, ge=1)
    d: int = Field(default=0, ge=0, le=40)
    r: str | None = Field(default=None, max_length=500)


class CompactStructureTransport(APIModel):
    e: list[CompactStructureEntry]
    m: list[int] = Field(default_factory=list)


CONTENT_KINDS = Literal[
    "concept",
    "definition",
    "grammatical_rule",
    "condition",
    "constraint",
    "exception",
    "contrast",
    "terminology",
    "form_pattern",
    "paradigm",
    "example",
    "counterexample",
    "usage_note",
    "register_note",
    "warning",
    "cross_reference",
    "dependency",
    "prerequisite",
    "other",
]

RELATION_KINDS = Literal[
    "part_of",
    "requires",
    "uses",
    "explains",
    "contrasts_with",
    "example_of",
    "exception_to",
    "appears_in",
    "equivalent_to",
    "precedes",
    "extends",
    "commonly_confused_with",
    "supports_skill",
    "cross_references",
]


class CompactEvidence(APIModel):
    p: int = Field(ge=1)
    q: str = Field(min_length=1, max_length=600)
    pp: str | None = Field(default=None, max_length=80)
    no: int | None = Field(default=None, ge=1)


class CompactKnowledgeItem(APIModel):
    o: int = Field(ge=1)
    k: CONTENT_KINDS
    x: str = Field(min_length=1, max_length=2_000)
    nl: str | None = Field(default=None, max_length=1_000)
    b: str | None = Field(default=None, max_length=8_000)
    s: AssertionOrigin
    e: list[CompactEvidence] = Field(default_factory=list, max_length=100)


class CompactKnowledgeRelation(APIModel):
    o: int = Field(ge=1)
    f: int = Field(ge=1)
    t: int = Field(ge=1)
    k: RELATION_KINDS
    s: AssertionOrigin
    e: list[CompactEvidence] = Field(default_factory=list, max_length=100)


class CompactContentTransport(APIModel):
    i: list[CompactKnowledgeItem]
    r: list[CompactKnowledgeRelation] = Field(default_factory=list)


class CanonicalProvenance(APIModel):
    run_id: str
    provider: str
    model: str
    extraction_mode: CloudExtractionMode
    prompt_version: str
    transport_version: str
    source_id: str
    source_version_id: int
    pages: list[int]
    extracted_at: datetime


class CanonicalEvidence(APIModel):
    source_id: str
    source_version_id: int
    physical_pdf_page: int
    printed_page: str | None = None
    document_node_id: str | None = None
    excerpt: str


class CanonicalDocumentNode(APIModel):
    id: str
    kind: STRUCTURE_KINDS
    physical_pdf_page: int
    display_order: int
    raw_visible_text: str
    visible_number: str | None = None
    normalized_label: str | None = None
    printed_page: str | None = None
    parent_id: str | None = None
    depth: int
    topic_number: int | None = None
    review_marker: str | None = None


class CanonicalStructure(APIModel):
    schema_version: Literal["llc.cloud-structure.v1"] = "llc.cloud-structure.v1"
    provenance: CanonicalProvenance
    nodes: list[CanonicalDocumentNode]
    justified_missing_pages: list[int] = Field(default_factory=list)


class CanonicalKnowledgeItem(APIModel):
    id: str
    kind: CONTENT_KINDS
    raw_visible_text: str
    normalized_label: str | None = None
    content: str | None = None
    extractor_status: AssertionOrigin
    review_status: Literal["unverified", "verified", "rejected"] = "unverified"
    evidence: list[CanonicalEvidence] = Field(default_factory=list)


class CanonicalKnowledgeRelation(APIModel):
    id: str
    source_item_id: str
    target_item_id: str
    kind: RELATION_KINDS
    extractor_status: AssertionOrigin
    review_status: Literal["unverified", "verified", "rejected"] = "unverified"
    evidence: list[CanonicalEvidence] = Field(default_factory=list)


class CanonicalContent(APIModel):
    schema_version: Literal["llc.cloud-content.v1"] = "llc.cloud-content.v1"
    provenance: CanonicalProvenance
    items: list[CanonicalKnowledgeItem]
    relations: list[CanonicalKnowledgeRelation] = Field(default_factory=list)


class ValidationIssue(APIModel):
    severity: Literal["warning", "error"]
    code: str
    message: str
    path: str | None = None


class ValidationReport(APIModel):
    outcome: ValidationOutcome
    issues: list[ValidationIssue] = Field(default_factory=list)
    metrics: dict[str, int | float | str | bool | None] = Field(default_factory=dict)


class CloudArtifactRead(APIModel):
    id: str
    kind: Literal["raw", "transport", "canonical", "validation"]
    format: str
    relative_path: str
    sha256: str
    size_bytes: int
    created_at: datetime


class CloudExtractionRunRead(APIModel):
    id: str
    document_run_id: str
    source_id: str
    source_version_id: int
    provider: str
    model: str
    mode: CloudExtractionMode
    prompt_version: str
    transport_version: str
    canonical_schema_version: str
    pages: list[int]
    state: str
    provider_status: ProviderAvailability
    validation_outcome: ValidationOutcome | None
    usage: ProviderUsage
    provider_metadata: dict[str, JsonValue]
    normalized_error: ProviderAvailability | None
    provider_error_code: str | None
    provider_error_message: str | None
    provider_error_details: dict[str, JsonValue]
    artifacts: list[CloudArtifactRead]
    created_at: datetime
    updated_at: datetime
