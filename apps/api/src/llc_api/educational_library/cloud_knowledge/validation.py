from __future__ import annotations

import re
from collections import Counter, defaultdict

from .models import (
    AssertionOrigin,
    CanonicalContent,
    CanonicalStructure,
    ValidationIssue,
    ValidationOutcome,
    ValidationReport,
)

LEADING_NUMBER = re.compile(r"^\s*(\d+)\s*[.)]?")


def _report(issues: list[ValidationIssue], **metrics: int | float | str | bool | None):
    if any(issue.severity == "error" for issue in issues):
        outcome = ValidationOutcome.FAIL
    elif issues:
        outcome = ValidationOutcome.WARN
    else:
        outcome = ValidationOutcome.PASS
    return ValidationReport(outcome=outcome, issues=issues, metrics=metrics)


def validate_structure(
    canonical: CanonicalStructure,
    expected_pages: list[int],
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    nodes = canonical.nodes
    orders = [node.display_order for node in nodes]
    counts = Counter(orders)
    for order, count in counts.items():
        if count > 1:
            issues.append(_error("duplicate_display_order", f"Display order {order} is duplicated."))
    ids = {node.id for node in nodes}
    by_id = {node.id: node for node in nodes}
    if orders != sorted(orders):
        issues.append(_error("display_order_not_sorted", "Nodes are not in display order."))
    if orders and orders != list(range(min(orders), max(orders) + 1)):
        issues.append(_warning("display_order_gaps", "Display order contains gaps."))

    children: dict[str, list[int]] = defaultdict(list)
    for index, node in enumerate(nodes):
        path = f"nodes[{index}]"
        if node.parent_id is None:
            if node.depth != 0:
                issues.append(_error("root_depth", "A root node must have depth 0.", path))
            continue
        if node.parent_id not in ids:
            issues.append(_error("missing_parent", "Node references a missing parent.", path))
            continue
        parent = by_id[node.parent_id]
        if parent.display_order >= node.display_order:
            issues.append(_error("parent_not_preceding", "Parent must precede its child.", path))
        if node.depth != parent.depth + 1:
            issues.append(_error("incoherent_depth", "Depth must be parent depth plus one.", path))
        number = _leading_number(node.visible_number or node.raw_visible_text)
        if number is not None:
            children[parent.id].append(number)

    represented = {node.physical_pdf_page for node in nodes}
    justified = set(canonical.justified_missing_pages)
    for page in sorted(set(expected_pages) - represented - justified):
        issues.append(_error("missing_page", f"Expected PDF page {page} is not represented."))
    for page in (sorted(represented - set(expected_pages)) if expected_pages else []):
        issues.append(_error("page_outside_scope", f"PDF page {page} is outside the run scope."))

    for parent_id, numbers in children.items():
        parent = by_id[parent_id]
        parent_number = _leading_number(parent.visible_number or parent.raw_visible_text)
        if parent_number is not None and any(value <= parent_number for value in numbers) and any(
            value > parent_number for value in numbers
        ):
            issues.append(
                _warning(
                    "suspicious_hierarchy",
                    "Child numbering crosses the apparent parent number; review sibling placement.",
                    parent_id,
                )
            )

    topic_count = sum(node.kind == "topic" for node in nodes)
    numbered_topics = sum(node.kind == "topic" and node.topic_number is not None for node in nodes)
    if topic_count and numbered_topics < topic_count:
        issues.append(
            _warning(
                "topic_number_not_detected",
                f"Detected a number for {numbered_topics} of {topic_count} topic nodes.",
            )
        )
    return _report(
        issues,
        nodes=len(nodes),
        topics=topic_count,
        numbered_topics=numbered_topics,
        represented_pages=len(represented),
        expected_pages=len(set(expected_pages)),
    )


def validate_content(canonical: CanonicalContent, expected_pages: list[int]) -> ValidationReport:
    issues: list[ValidationIssue] = []
    item_counts = Counter(item.id for item in canonical.items)
    relation_counts = Counter(relation.id for relation in canonical.relations)
    for item_id, count in item_counts.items():
        if count > 1:
            issues.append(_error("duplicate_item_id", f"Knowledge item ID is duplicated: {item_id}"))
    for relation_id, count in relation_counts.items():
        if count > 1:
            issues.append(_error("duplicate_relation_id", f"Relation ID is duplicated: {relation_id}"))

    item_ids = set(item_counts)
    expected = set(expected_pages)
    for index, item in enumerate(canonical.items):
        path = f"items[{index}]"
        if item.extractor_status == AssertionOrigin.EXTRACTED and not item.evidence:
            issues.append(_error("extracted_item_without_evidence", "Extracted item requires evidence.", path))
        _validate_evidence(item.evidence, expected, issues, path)

    for index, relation in enumerate(canonical.relations):
        path = f"relations[{index}]"
        if relation.source_item_id not in item_ids or relation.target_item_id not in item_ids:
            issues.append(_error("relation_target_missing", "Relation references a missing item.", path))
        if relation.extractor_status == AssertionOrigin.EXTRACTED and not relation.evidence:
            issues.append(
                _error("extracted_relation_without_evidence", "Extracted relation requires evidence.", path)
            )
        _validate_evidence(relation.evidence, expected, issues, path)
    return _report(
        issues,
        items=len(canonical.items),
        relations=len(canonical.relations),
        extracted_items=sum(
            item.extractor_status == AssertionOrigin.EXTRACTED for item in canonical.items
        ),
        inferred_items=sum(
            item.extractor_status == AssertionOrigin.INFERRED for item in canonical.items
        ),
    )


def _validate_evidence(evidence, expected: set[int], issues: list[ValidationIssue], path: str) -> None:
    for offset, reference in enumerate(evidence):
        if expected and reference.physical_pdf_page not in expected:
            issues.append(
                _error(
                    "evidence_page_outside_scope",
                    f"Evidence page {reference.physical_pdf_page} is outside the run scope.",
                    f"{path}.evidence[{offset}]",
                )
            )


def _leading_number(value: str) -> int | None:
    match = LEADING_NUMBER.search(value)
    return int(match.group(1)) if match else None


def _warning(code: str, message: str, path: str | None = None) -> ValidationIssue:
    return ValidationIssue(severity="warning", code=code, message=message, path=path)


def _error(code: str, message: str, path: str | None = None) -> ValidationIssue:
    return ValidationIssue(severity="error", code=code, message=message, path=path)
