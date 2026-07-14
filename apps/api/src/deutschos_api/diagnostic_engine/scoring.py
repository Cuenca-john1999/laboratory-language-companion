"""Pure, deterministic scoring for text diagnostic candidates.

The scorer deliberately understands only closed, versioned rubrics.  Free text
that cannot be decided from those rubrics remains ``not_evaluable`` until a
future structured evaluator exists; it is never guessed from an LLM.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from deutschos_api.models import DiagnosticPolarity

from .exceptions import InvalidSubmissionContractError
from .schemas import (
    AmbiguityRisk,
    EvaluationOutcome,
    EvaluationResult,
    NoAnswer,
    ResponseSubmission,
    RubricStrategy,
    SingleChoiceAnswer,
    TaskCandidate,
    TextAnswer,
)

SCORES = {
    EvaluationOutcome.INCORRECT: 0.0,
    EvaluationOutcome.PARTIAL: 0.4,
    EvaluationOutcome.CORRECT_WITH_HELP: 0.7,
    EvaluationOutcome.CORRECT_WITHOUT_HELP: 1.0,
}

_RISK_CONFIDENCE = {
    AmbiguityRisk.LOW: 1.0,
    AmbiguityRisk.MEDIUM: 0.85,
    AmbiguityRisk.HIGH: 0.65,
}
_WHITESPACE = re.compile(r"\s+")


def _normalise(value: str, *, case_sensitive: bool) -> str:
    normalised = unicodedata.normalize("NFC", value).strip()
    normalised = _WHITESPACE.sub(" ", normalised)
    return normalised if case_sensitive else normalised.casefold()


def _normalised_values(values: Sequence[str], *, case_sensitive: bool) -> set[str]:
    return {_normalise(value, case_sensitive=case_sensitive) for value in values}


def _has_help(submission: ResponseSubmission) -> bool:
    return any(item != "none" for item in submission.assistance)


def _rubric_snapshot(candidate: TaskCandidate, *, match: str) -> dict[str, object]:
    return {
        "strategy": candidate.rubric.strategy.value,
        "match": match,
        "candidate_id": candidate.candidate_id,
        "candidate_version": candidate.version,
    }


def _not_evaluable(
    candidate: TaskCandidate,
    *,
    reason_code: str,
    justification: str,
) -> EvaluationResult:
    return EvaluationResult(
        outcome=EvaluationOutcome.NOT_EVALUABLE,
        score=None,
        polarity=DiagnosticPolarity.INSUFFICIENT,
        evaluator_confidence=0.0,
        justification=justification,
        reason_codes=[reason_code],
        rubric_snapshot=_rubric_snapshot(candidate, match="not_evaluable"),
    )


def _evaluated(
    candidate: TaskCandidate,
    *,
    outcome: EvaluationOutcome,
    match: str,
    reason_code: str,
    justification: str,
) -> EvaluationResult:
    polarity = (
        DiagnosticPolarity.NEGATIVE
        if outcome == EvaluationOutcome.INCORRECT
        else DiagnosticPolarity.POSITIVE
    )
    return EvaluationResult(
        outcome=outcome,
        score=SCORES[outcome],
        polarity=polarity,
        evaluator_confidence=_RISK_CONFIDENCE[candidate.ambiguity_risk],
        justification=justification,
        reason_codes=[reason_code],
        rubric_snapshot=_rubric_snapshot(candidate, match=match),
    )


def _correct_result(
    candidate: TaskCandidate,
    submission: ResponseSubmission,
    *,
    match: str,
) -> EvaluationResult:
    with_help = _has_help(submission)
    return _evaluated(
        candidate,
        outcome=(
            EvaluationOutcome.CORRECT_WITH_HELP
            if with_help
            else EvaluationOutcome.CORRECT_WITHOUT_HELP
        ),
        match=match,
        reason_code="deterministic_match_with_help" if with_help else "deterministic_match",
        justification=(
            "La respuesta coincide con la clave determinista y registra ayuda."
            if with_help
            else "La respuesta coincide con la clave determinista sin ayuda."
        ),
    )


def _ordered_token_match(
    response: str,
    candidate: TaskCandidate,
) -> tuple[str, float]:
    """Return a deterministic match label and ordered-token coverage."""

    case_sensitive = candidate.rubric.case_sensitive
    response_tokens = _normalise(response, case_sensitive=case_sensitive).split()
    expected_tokens = [
        _normalise(token, case_sensitive=case_sensitive)
        for token in candidate.rubric.expected_tokens
    ]
    if response_tokens == expected_tokens:
        return "exact", 1.0

    cursor = 0
    matched = 0
    for token in response_tokens:
        if cursor < len(expected_tokens) and token == expected_tokens[cursor]:
            matched += 1
            cursor += 1
    coverage = matched / len(expected_tokens)
    if coverage == 1.0:
        return "accepted", coverage
    if coverage >= 0.5:
        return "partial", coverage
    return "none", coverage


def evaluate_response(
    candidate: TaskCandidate,
    submission: ResponseSubmission,
) -> EvaluationResult:
    """Evaluate one text response without external services or hidden inference."""

    if submission.abandoned:
        return _not_evaluable(
            candidate,
            reason_code="task_abandoned",
            justification="La tarea fue abandonada y no aporta evidencia lingüística.",
        )
    if submission.instruction_state == "not_understood":
        return _not_evaluable(
            candidate,
            reason_code="instruction_not_understood",
            justification="No se entendió la instrucción; no se infiere desconocimiento del alemán.",
        )

    if isinstance(submission.answer, NoAnswer):
        return _not_evaluable(
            candidate,
            reason_code="empty_response",
            justification="Una respuesta vacía no permite evaluar la habilidad.",
        )

    if candidate.rubric.strategy == RubricStrategy.OPTION_ID:
        if not isinstance(submission.answer, SingleChoiceAnswer):
            raise InvalidSubmissionContractError("La tarea cerrada exige selected_option_id.")
        selected_option_id = submission.answer.selected_option_id
        available_option_ids = {
            option["id"]
            for option in candidate.options
            if isinstance(option, dict) and isinstance(option.get("id"), str)
        }
        if selected_option_id not in available_option_ids:
            raise InvalidSubmissionContractError(
                "La opción indicada no pertenece al contrato de la tarea."
            )
        if selected_option_id in candidate.rubric.partial_option_ids:
            return _evaluated(
                candidate,
                outcome=EvaluationOutcome.PARTIAL,
                match="partial_option_id",
                reason_code="deterministic_partial_option_id",
                justification="La opción coincide con una identidad parcial versionada.",
            )
        if selected_option_id in candidate.rubric.accepted_option_ids:
            return _correct_result(candidate, submission, match="option_id")
        return _evaluated(
            candidate,
            outcome=EvaluationOutcome.INCORRECT,
            match="option_id_no_match",
            reason_code="deterministic_option_id_no_match",
            justification="La opción pertenece a la tarea, pero no coincide con la clave.",
        )

    if isinstance(submission.answer, SingleChoiceAnswer):
        raise InvalidSubmissionContractError("La tarea textual no acepta selected_option_id.")

    response = (
        submission.answer.text
        if isinstance(submission.answer, TextAnswer)
        else submission.response_text or ""
    )
    if not response.strip():
        return _not_evaluable(
            candidate,
            reason_code="empty_response",
            justification="Una respuesta vacía no permite evaluar la habilidad.",
        )
    if submission.response_language and submission.response_language.casefold().startswith("es"):
        return _not_evaluable(
            candidate,
            reason_code="spanish_response_requires_clarification",
            justification=(
                "La respuesta en español se conserva, pero no se interpreta automáticamente "
                "como desconocimiento del alemán."
            ),
        )
    if not candidate.auto_evaluable or candidate.rubric.strategy == RubricStrategy.MANUAL_ONLY:
        return _not_evaluable(
            candidate,
            reason_code="deterministic_rubric_unavailable",
            justification="Esta respuesta libre no tiene una clave determinista suficiente.",
        )
    if submission.out_of_topic:
        return _evaluated(
            candidate,
            outcome=EvaluationOutcome.INCORRECT,
            match="out_of_topic",
            reason_code="out_of_topic",
            justification="La respuesta no aborda el objetivo explícito de la tarea.",
        )
    if submission.partially_communicative:
        return _evaluated(
            candidate,
            outcome=EvaluationOutcome.PARTIAL,
            match="partially_communicative",
            reason_code="partially_communicative",
            justification="La respuesta comunica parte de la intención, pero no cumple el objetivo.",
        )

    case_sensitive = candidate.rubric.case_sensitive
    normalised = _normalise(response, case_sensitive=case_sensitive)
    partial_answers = _normalised_values(
        candidate.rubric.partial_answers,
        case_sensitive=case_sensitive,
    )
    if normalised in partial_answers:
        return _evaluated(
            candidate,
            outcome=EvaluationOutcome.PARTIAL,
            match="partial_answer",
            reason_code="deterministic_partial_match",
            justification="La respuesta coincide con una variante parcial definida en la rúbrica.",
        )

    if candidate.rubric.strategy == RubricStrategy.ORDERED_TOKENS:
        match, coverage = _ordered_token_match(response, candidate)
        if match in {"exact", "accepted"}:
            return _correct_result(candidate, submission, match=match)
        if match == "partial":
            result = _evaluated(
                candidate,
                outcome=EvaluationOutcome.PARTIAL,
                match="ordered_tokens_partial",
                reason_code="ordered_tokens_partial",
                justification="La respuesta conserva parte suficiente del orden objetivo.",
            )
            return result.model_copy(
                update={
                    "rubric_snapshot": {
                        **result.rubric_snapshot,
                        "ordered_token_coverage": round(coverage, 6),
                    }
                }
            )
    else:
        accepted_answers = _normalised_values(
            candidate.rubric.accepted_answers,
            case_sensitive=case_sensitive,
        )
        if normalised in accepted_answers:
            match = (
                "exact" if candidate.rubric.strategy == RubricStrategy.EXACT_MATCH else "accepted"
            )
            return _correct_result(candidate, submission, match=match)

    return _evaluated(
        candidate,
        outcome=EvaluationOutcome.INCORRECT,
        match="no_match",
        reason_code="deterministic_no_match",
        justification="La respuesta no coincide con las soluciones cerradas de la rúbrica.",
    )


def evaluate_submission(
    candidate: TaskCandidate,
    submission: ResponseSubmission,
    *,
    attempt_number: int,
) -> EvaluationResult:
    """Service-facing scorer with explicit attempt metadata.

    Attempt number does not change linguistic correctness. It only makes the
    second empty submission auditable so the service can mark the task skipped.
    """

    if attempt_number < 1:
        raise ValueError("attempt_number must be at least 1")
    result = evaluate_response(candidate, submission)
    if (
        attempt_number > 1
        and result.outcome == EvaluationOutcome.NOT_EVALUABLE
        and "empty_response" in result.reason_codes
    ):
        return result.model_copy(
            update={
                "justification": (
                    "La segunda entrega vacía sigue sin aportar evidencia y agota el reintento."
                ),
                "reason_codes": ["empty_response_retry_exhausted"],
            }
        )
    return result


__all__ = ["SCORES", "evaluate_response", "evaluate_submission"]
