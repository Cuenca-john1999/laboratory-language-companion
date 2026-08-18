from __future__ import annotations

from .models import CloudExtractionMode

STRUCTURE_PROMPT_VERSION = "cloud-structure-compact.v1"
CONTENT_PROMPT_VERSION = "cloud-content-compact.v1"
STRUCTURE_TRANSPORT_VERSION = "compact-structure.v1"
CONTENT_TRANSPORT_VERSION = "compact-content.v1"


def prompt_for(mode: CloudExtractionMode, pages: list[int]) -> tuple[str, str, str]:
    scope = ", ".join(map(str, pages)) if pages else "all physical PDF pages"
    common = (
        f"Process {scope}. Physical PDF pages are one-based. "
        "If the uploaded PDF contains only selected source pages, page fields must use the listed "
        "ORIGINAL physical PDF numbers in corresponding order, never subset-relative numbering. "
        "Return only data explicitly requested by the response schema. "
        "Preserve source spelling and visible numbering literally; never silently correct source text. "
        "Do not emit infrastructure metadata, IDs, schema versions, provider names, or model names. "
    )
    if mode == CloudExtractionMode.STRUCTURE:
        return (
            common
            + "Extract document structure, not a summary. Use x for immutable raw visible text and t only "
            "for a separate normalized label when useful. Keep physical page p separate from printed page pp. "
            "Order o is document display order. Parent po refers to an earlier order and d is depth. "
            "Use m only for requested pages intentionally lacking structural entries. Mark uncertainty in r.",
            STRUCTURE_PROMPT_VERSION,
            STRUCTURE_TRANSPORT_VERSION,
        )
    return (
        common
        + "Extract explicit pedagogically meaningful knowledge units, not a summary. Keep x as immutable "
        "visible source text and nl as a separate normalized label. Mark s=extracted only when the source "
        "explicitly supports the item or relation and include short locating evidence e. Mark model deductions "
        "s=inferred; do not pretend they are source evidence. Relations f/t reference item order o. Evidence "
        "quotes q must be short locating fragments, not long passages.",
        CONTENT_PROMPT_VERSION,
        CONTENT_TRANSPORT_VERSION,
    )
