from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from deutschos_api.models import Mistake, StudentProfile
from deutschos_api.providers.base import (
    MalformedStructuredOutputError,
    ModelProvider,
    ProviderResponseError,
    ProviderUnavailableError,
)

from .cache import LibraryCache, stable_cache_key
from .database import LibraryDatabase
from .memory import PedagogicalMemoryService
from .routing import LibraryModelRouter, ModelRole, ModelRoutingError
from .schemas import (
    EvidenceConfidence,
    LibraryNotFoundError,
    LibraryProviderUnavailableError,
    LibraryTeacherError,
    PedagogicalMemoryStatus,
    QueryAmbiguity,
    SourceLookupMode,
    SourceLookupStatus,
    TeacherAnswerDraft,
    TeacherAskRequest,
    TeacherConversationSummary,
    TeacherFailureReason,
    TeacherIntent,
    TeacherPublicAnswer,
    TeacherQueryPlan,
    TeacherQueryRead,
    TeacherQueryStatus,
    TeacherSourceRead,
    TeacherTimings,
)
from .search import EducationalSearchService
from .service import json_dump, json_load, utc_text
from .source_lookup import (
    DeterministicSourceLookupService,
    SourceLookupExecution,
    detect_source_lookup,
)

PROMPT_ROOT = Path(__file__).resolve().parents[1] / "prompts"
QUERY_PLAN_VERSION = "library-query-plan.v1"
TEACHER_ANSWER_VERSION = "library-teacher-answer.v1"
TEACHER_REPAIR_VERSION = "library-teacher-answer-repair.v1"

_SOLUTION_NAMES = ("lösung", "loesung", "solutions", "answer key", "respuestas", "solucionario")
_BANNED_GROUNDING_BYPASSES = (
    "según mis conocimientos",
    "aunque las fuentes no lo dicen",
    "fuera de las fuentes",
    "fts5",
    "bm25",
    "reciprocal rank",
    "system prompt",
    "/volumes/",
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeacherLimits:
    max_search_queries: int = 6
    max_sources: int = 5
    max_chunks: int = 10
    max_chunks_per_source: int = 2
    max_context_characters: int = 18_000


@dataclass(frozen=True)
class LearnerContext:
    explanation_language: str = "es"
    level_hint: str = "introductory"
    learning_goals: tuple[str, ...] = ()
    learning_preferences: dict[str, object] = field(default_factory=dict)
    recent_error_categories: tuple[str, ...] = ()

    def prompt_payload(self) -> dict[str, object]:
        return {
            "explanation_language": self.explanation_language,
            "level_hint": self.level_hint,
            "learning_goals": list(self.learning_goals[:5]),
            "learning_preferences": self.learning_preferences,
            "recent_error_categories": list(self.recent_error_categories[:3]),
        }


@dataclass
class _Evidence:
    chunk_id: int
    source_id: str
    source_version_id: int
    source_name: str
    source_version: int
    title: str | None
    text: str
    page_start: int | None
    page_end: int | None
    start_seconds: float | None
    end_seconds: float | None
    review_status: str
    rights: str
    content_role: str
    extraction_quality: float
    editorial_confidence: float
    source_priority: int
    pedagogical_role: str
    page_quality: str | None
    duplicate_group: str
    content_hash: str
    score: float
    matched_queries: set[str] = field(default_factory=set)
    knowledge_statuses: set[str] = field(default_factory=set)


def learner_context_from_db(db: Session) -> LearnerContext:
    """Read only the pedagogically useful subset of the normal learner state."""
    profile = db.scalar(select(StudentProfile).limit(1))
    if profile is None:
        return LearnerContext()

    def decode(raw: str, fallback: object) -> object:
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return fallback

    goals = decode(profile.learning_goals, [])
    preferences = decode(profile.learning_preferences, {})
    mistakes = db.scalars(
        select(Mistake)
        .where(Mistake.status != "ignored")
        .order_by(Mistake.last_seen_at.desc())
        .limit(3)
    ).all()
    native = profile.native_language.casefold()
    explanation_language = "es" if native in {"es", "spanish", "español", "castellano"} else "es"
    return LearnerContext(
        explanation_language=explanation_language,
        level_hint="pre-A1/A1",
        learning_goals=tuple(str(value) for value in goals) if isinstance(goals, list) else (),
        learning_preferences=(
            {str(key): value for key, value in preferences.items()}
            if isinstance(preferences, dict)
            else {}
        ),
        recent_error_categories=tuple(mistake.category for mistake in mistakes),
    )


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[\wÀ-ÿÄÖÜäöüß-]+", text, flags=re.UNICODE)
        if len(token) > 2
    }


def _jaccard(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _short_snippet(text: str, terms: str, limit: int = 420) -> str:
    normalised = re.sub(r"\s+", " ", text).strip()
    folded = normalised.casefold()
    positions = [folded.find(token) for token in _tokens(terms)]
    match = min((position for position in positions if position >= 0), default=0)
    start = max(0, match - 90)
    end = min(len(normalised), start + limit)
    return ("…" if start else "") + normalised[start:end] + ("…" if end < len(normalised) else "")


def _sanitise_internal_references(text: str | None) -> str | None:
    if text is None:
        return None

    def replacement(match: re.Match[str]) -> str:
        folded = match.group(0).casefold()
        return (
            "las evidencias recuperadas"
            if "chunks" in folded or re.search(r"(?:,|\by\b)\s*#?\d+", folded)
            else "la evidencia recuperada"
        )

    sanitised = re.sub(
        r"\b(?:el\s+|los\s+|the\s+)?chunks?\s*(?:n[úu]mero\s*)?#?\d+"
        r"(?:\s*(?:,|y)\s*#?\d+)*\b",
        replacement,
        text,
        flags=re.IGNORECASE,
    )
    # Public answer strings are rendered as plain text. Removing lightweight
    # Markdown emphasis avoids exposing model formatting markers without
    # interpreting HTML or changing the linguistic content.
    return sanitised.replace("*", "").replace("`", "")


class EducationalTeacherService:
    def __init__(
        self,
        database: LibraryDatabase,
        search: EducationalSearchService,
        model_provider: ModelProvider,
        *,
        default_model: str,
        limits: TeacherLimits | None = None,
        model_router: LibraryModelRouter | None = None,
        cache: LibraryCache | None = None,
        memory: PedagogicalMemoryService | None = None,
        source_lookup: DeterministicSourceLookupService | None = None,
    ):
        self.database = database
        self.search = search
        self.model_provider = model_provider
        self.default_model = default_model
        self.limits = limits or TeacherLimits()
        self.model_router = model_router
        self.cache = cache or LibraryCache(database)
        self.memory = memory or PedagogicalMemoryService(database)
        self.source_lookup = source_lookup or DeterministicSourceLookupService(
            database,
            search,
            memory=self.memory,
            cache=self.cache,
        )

    async def ask(
        self,
        request: TeacherAskRequest,
        *,
        learner: LearnerContext | None = None,
    ) -> TeacherQueryRead:
        started = perf_counter()
        learner = learner or LearnerContext()
        conversation_id, parent = self._resolve_conversation(request.conversation_id)
        parent_plan = json_load(str(parent["plan_json"]), {}) if parent else {}
        parent_target = str(parent_plan.get("target_expression") or "") or None
        detection_started = perf_counter()
        lookup_detection = detect_source_lookup(
            request.question,
            parent_target=parent_target,
        )
        intent_detection_ms = self._elapsed_ms(detection_started)
        lookup: SourceLookupExecution | None = None
        if lookup_detection.mode in {SourceLookupMode.PURE, SourceLookupMode.MIXED}:
            lookup = await self.source_lookup.resolve(
                request.question,
                source_id=request.source_id,
                parent_target=parent_target,
                detection=lookup_detection,
                expanded=request.continuation_action == "expand",
            )
        if lookup_detection.mode == SourceLookupMode.PURE and lookup is not None:
            return self._persist_source_lookup(
                request,
                conversation_id,
                parent,
                lookup,
                started,
                intent_detection_ms,
            )

        model = self.default_model
        if not model and self.model_router is None:
            if lookup is not None and self._lookup_has_location(lookup):
                return self._persist_source_lookup(
                    request,
                    conversation_id,
                    parent,
                    lookup,
                    started,
                    intent_detection_ms,
                    mixed=True,
                    failure_reason=TeacherFailureReason.MODEL_UNAVAILABLE,
                )
            raise LibraryProviderUnavailableError("No hay un modelo docente local configurado.")

        planning_started = perf_counter()
        try:
            plan, planner_model, plan_cache_hit = await self._plan(request, learner, parent, model)
        except (LibraryTeacherError, LibraryProviderUnavailableError):
            if lookup is not None and self._lookup_has_location(lookup):
                return self._persist_source_lookup(
                    request,
                    conversation_id,
                    parent,
                    lookup,
                    started,
                    intent_detection_ms,
                    mixed=True,
                    failure_reason=TeacherFailureReason.GENERATION_FAILURE,
                )
            raise
        if lookup_detection.mode == SourceLookupMode.MIXED:
            plan = plan.model_copy(
                update={
                    "intent": TeacherIntent.GRAMMAR_EXPLANATION,
                    "required_evidence": ["explicación", "ejemplo"],
                }
            )
        planning_ms = self._elapsed_ms(planning_started)

        retrieval_started = perf_counter()
        try:
            (
                evidence,
                knowledge,
                retrieval_mode,
                semantic_available,
                retrieval_phases,
            ) = await self._retrieve(request, plan)
        except LibraryTeacherError:
            if lookup is not None and self._lookup_has_location(lookup):
                return self._persist_source_lookup(
                    request,
                    conversation_id,
                    parent,
                    lookup,
                    started,
                    intent_detection_ms,
                    mixed=True,
                    failure_reason=TeacherFailureReason.RETRIEVAL_FAILURE,
                )
            raise
        retrieval_ms = self._elapsed_ms(retrieval_started)
        confidence, confidence_score, confidence_warnings = self._confidence(evidence, knowledge)

        generation_started = perf_counter()
        validation_ms = 0
        repair_ms = 0
        answer_model = planner_model
        generation_failure: TeacherFailureReason | None = None
        if not evidence or confidence == EvidenceConfidence.INSUFFICIENT:
            if lookup is not None and self._lookup_has_location(lookup):
                draft = self._mixed_lookup_failure_answer(lookup)
                status = TeacherQueryStatus.COMPLETED
                generation_failure = TeacherFailureReason.WEAK_EVIDENCE
                confidence = self._lookup_confidence(lookup)
            else:
                draft = self._insufficient_answer(plan)
                status = TeacherQueryStatus.INSUFFICIENT
        else:
            try:
                (
                    draft,
                    validation_ms,
                    repair_ms,
                    answer_model,
                    generation_failure,
                ) = await self._generate(
                    request,
                    plan,
                    learner,
                    parent,
                    evidence,
                    knowledge,
                    model,
                )
            except LibraryProviderUnavailableError:
                if lookup is None or not self._lookup_has_location(lookup):
                    raise
                draft = self._mixed_lookup_failure_answer(lookup)
                generation_failure = TeacherFailureReason.GENERATION_FAILURE
            if draft.evidence_sufficient:
                if lookup is not None:
                    draft = draft.model_copy(
                        update={
                            "answer_kind": "teacher_answer_with_source_lookup",
                            "source_lookup": lookup.result,
                            "used_generation": generation_failure is None,
                        }
                    )
                status = TeacherQueryStatus.COMPLETED
            else:
                if lookup is not None and self._lookup_has_location(lookup):
                    draft = self._mixed_lookup_failure_answer(lookup)
                    confidence = self._lookup_confidence(lookup)
                    status = TeacherQueryStatus.COMPLETED
                else:
                    draft = self._insufficient_answer(plan)
                    confidence = EvidenceConfidence.INSUFFICIENT
                    status = TeacherQueryStatus.INSUFFICIENT
        if not evidence:
            failure_reason = generation_failure or TeacherFailureReason.NO_EVIDENCE
        elif confidence == EvidenceConfidence.INSUFFICIENT and lookup is None:
            failure_reason = generation_failure or TeacherFailureReason.WEAK_EVIDENCE
        else:
            failure_reason = generation_failure
        generation_ms = self._elapsed_ms(generation_started)

        warnings = list(dict.fromkeys([*confidence_warnings, *draft.warnings]))
        if confidence == EvidenceConfidence.INSUFFICIENT:
            warnings.append(
                "La biblioteca no contiene soporte suficiente para una explicación segura."
            )
        timings = TeacherTimings(
            planning_ms=planning_ms,
            retrieval_ms=retrieval_ms,
            generation_ms=max(0, generation_ms - validation_ms - repair_ms),
            validation_ms=validation_ms,
            total_ms=self._elapsed_ms(started),
            embedding_ms=retrieval_phases["embedding_ms"],
            fts_ms=retrieval_phases["fts_ms"],
            vector_ms=retrieval_phases["vector_ms"],
            ranking_ms=retrieval_phases["ranking_ms"],
            repair_ms=repair_ms,
            intent_detection_ms=intent_detection_ms,
            memory_lookup_ms=lookup.memory_ms if lookup else 0,
            hybrid_fallback_ms=lookup.hybrid_ms if lookup else 0,
        )
        query_id = self._persist(
            request=request,
            conversation_id=conversation_id,
            parent_query_id=parent["id"] if parent else None,
            plan=plan,
            answer=draft,
            evidence=evidence,
            model=answer_model if evidence else planner_model,
            status=status,
            confidence=confidence,
            retrieval_mode=retrieval_mode,
            semantic_available=semantic_available,
            warnings=warnings,
            timings=timings,
            confidence_score=confidence_score,
            failure_reason=failure_reason,
            models={"planner": planner_model, "teacher": answer_model},
            cache_hit=plan_cache_hit,
        )
        self.memory.remember_teacher_query(query_id)
        if lookup is not None:
            self.memory.link_lookup_query(
                query_id,
                list(lookup.concept_ids),
                list(lookup.location_ids),
            )
        return self.get_query(query_id)

    def _persist_source_lookup(
        self,
        request: TeacherAskRequest,
        conversation_id: str,
        parent: dict[str, object] | None,
        lookup: SourceLookupExecution,
        started: float,
        intent_detection_ms: int,
        *,
        mixed: bool = False,
        failure_reason: TeacherFailureReason | None = None,
    ) -> TeacherQueryRead:
        target = (
            lookup.result.concept.canonical_name
            if lookup.result.concept
            else lookup.detection.target
        )
        plan = TeacherQueryPlan(
            intent=TeacherIntent.SOURCE_LOOKUP,
            language="mixed",
            target_expression=target,
            user_language="es",
            ambiguity=(
                QueryAmbiguity.HIGH
                if lookup.detection.mode == SourceLookupMode.AMBIGUOUS
                else QueryAmbiguity.LOW
            ),
            possible_interpretations=[],
            search_queries=[target or request.question[:160]],
            required_evidence=["ubicación documental"],
        )
        has_location = self._lookup_has_location(lookup)
        answer = (
            self._mixed_lookup_failure_answer(lookup)
            if mixed
            else TeacherAnswerDraft(
                evidence_sufficient=has_location,
                direct_answer=lookup.result.summary,
                key_points=[],
                examples=[],
                important_nuance=None,
                ambiguity_note=None,
                follow_up_question=(
                    "¿Quieres que busque una explicación del tema?" if has_location else None
                ),
                claims=[],
                warnings=lookup.result.warnings,
                answer_kind="source_lookup",
                source_lookup=lookup.result,
                used_generation=False,
            )
        )
        status = TeacherQueryStatus.COMPLETED
        derived_failure = failure_reason
        if lookup.result.status == SourceLookupStatus.NO_LOCATION:
            status = TeacherQueryStatus.INSUFFICIENT
            derived_failure = TeacherFailureReason.NO_EVIDENCE
        elif lookup.result.status == SourceLookupStatus.RETRIEVAL_ERROR:
            status = TeacherQueryStatus.FAILED
            derived_failure = TeacherFailureReason.RETRIEVAL_FAILURE
        confidence = self._lookup_confidence(lookup)
        confidence_score = {
            EvidenceConfidence.SOLID: 0.95,
            EvidenceConfidence.MODERATE: 0.75,
            EvidenceConfidence.LIMITED: 0.45,
            EvidenceConfidence.INSUFFICIENT: 0.0,
        }[confidence]
        timings = TeacherTimings(
            planning_ms=0,
            retrieval_ms=lookup.memory_ms + lookup.hybrid_ms,
            generation_ms=0,
            validation_ms=0,
            total_ms=self._elapsed_ms(started),
            intent_detection_ms=intent_detection_ms,
            memory_lookup_ms=lookup.memory_ms,
            hybrid_fallback_ms=lookup.hybrid_ms,
        )
        evidence = self._lookup_evidence(lookup, plan)
        query_id = self._persist(
            request=request,
            conversation_id=conversation_id,
            parent_query_id=parent["id"] if parent else None,
            plan=plan,
            answer=answer,
            evidence=evidence,
            model="deterministic",
            status=status,
            confidence=confidence,
            retrieval_mode=lookup.retrieval_mode,
            semantic_available=lookup.semantic_available,
            warnings=list(dict.fromkeys([*lookup.result.warnings, *answer.warnings])),
            timings=timings,
            confidence_score=confidence_score,
            failure_reason=derived_failure,
            models={},
            cache_hit=False,
        )
        self.memory.link_lookup_query(
            query_id,
            list(lookup.concept_ids),
            list(lookup.location_ids),
        )
        return self.get_query(query_id)

    def _lookup_evidence(
        self,
        lookup: SourceLookupExecution,
        plan: TeacherQueryPlan,
    ) -> list[_Evidence]:
        if not lookup.location_ids:
            return []
        placeholders = ",".join("?" for _ in lookup.location_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT pel.id,pel.chunk_id,pel.status FROM pedagogical_evidence_locations pel "
                f"JOIN sources s ON s.id=pel.source_id WHERE pel.id IN ({placeholders}) "
                "AND pel.chunk_id IS NOT NULL AND pel.source_version_id=s.current_version_id "
                "AND s.status='present' AND s.excluded=0 "
                "AND pel.status NOT IN ('rejected','stale')",
                lookup.location_ids,
            ).fetchall()
        scores: dict[int, float] = {}
        matched: dict[int, set[str]] = {}
        weights = {"user_confirmed": 2.0, "system_verified": 1.2, "candidate": 0.3}
        for row in rows:
            chunk_id = int(row["chunk_id"])
            scores[chunk_id] = weights.get(str(row["status"]), 0.05)
            matched[chunk_id] = {"memory:source_lookup"}
        return self._deduplicate_and_select(self._load_and_rank(scores, matched, plan, []))

    @staticmethod
    def _lookup_has_location(lookup: SourceLookupExecution) -> bool:
        return bool(lookup.result.locations) and lookup.result.status not in {
            SourceLookupStatus.NO_LOCATION,
            SourceLookupStatus.RETRIEVAL_ERROR,
        }

    @staticmethod
    def _lookup_confidence(lookup: SourceLookupExecution) -> EvidenceConfidence:
        statuses = {location.review_status for location in lookup.result.locations}
        if PedagogicalMemoryStatus.USER_CONFIRMED in statuses:
            return EvidenceConfidence.SOLID
        if PedagogicalMemoryStatus.SYSTEM_VERIFIED in statuses:
            return EvidenceConfidence.MODERATE
        if statuses:
            return EvidenceConfidence.LIMITED
        return EvidenceConfidence.INSUFFICIENT

    @staticmethod
    def _mixed_lookup_failure_answer(lookup: SourceLookupExecution) -> TeacherAnswerDraft:
        return TeacherAnswerDraft(
            evidence_sufficient=True,
            direct_answer=(
                "No pude preparar la explicación docente, pero la ubicación documental sigue "
                f"disponible. {lookup.result.summary}"
            ),
            key_points=[],
            examples=[],
            important_nuance=None,
            ambiguity_note=None,
            follow_up_question="¿Quieres reintentar únicamente la explicación?",
            claims=[],
            warnings=[
                *lookup.result.warnings,
                "La explicación no se completó; la ubicación se validó por separado.",
            ],
            answer_kind="teacher_answer_with_source_lookup",
            source_lookup=lookup.result,
            used_generation=False,
        )

    def persist_cancelled(self, request: TeacherAskRequest) -> TeacherQueryRead:
        conversation_id, parent = self._resolve_conversation(request.conversation_id)
        plan = TeacherQueryPlan(
            intent=TeacherIntent.UNKNOWN,
            language="unknown",
            target_expression=None,
            user_language="es",
            ambiguity=QueryAmbiguity.HIGH,
            possible_interpretations=[],
            search_queries=[request.question[:160]],
            required_evidence=[],
        )
        timings = TeacherTimings(
            planning_ms=0,
            retrieval_ms=0,
            generation_ms=0,
            validation_ms=0,
            total_ms=0,
        )
        query_id = self._persist(
            request=request,
            conversation_id=conversation_id,
            parent_query_id=parent["id"] if parent else None,
            plan=plan,
            answer=self._insufficient_answer(plan),
            evidence=[],
            model="none",
            status=TeacherQueryStatus.CANCELLED,
            confidence=EvidenceConfidence.INSUFFICIENT,
            retrieval_mode="lexical",
            semantic_available=False,
            warnings=["La consulta se canceló antes de completar una respuesta verificada."],
            timings=timings,
            confidence_score=0,
            failure_reason=TeacherFailureReason.CANCELLED,
            models={},
            cache_hit=False,
        )
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE teacher_queries SET stream_status='cancelled' WHERE id=?", (query_id,)
            )
        return self.get_query(query_id)

    async def _plan(
        self,
        request: TeacherAskRequest,
        learner: LearnerContext,
        parent: dict[str, object] | None,
        model: str,
    ) -> tuple[TeacherQueryPlan, str, bool]:
        deterministic = self._deterministic_plan(request) if self.model_router else None
        if deterministic is not None:
            return self._validated_plan(deterministic, request), "deterministic", False
        system = (PROMPT_ROOT / "library_query_plan_v1.md").read_text(encoding="utf-8")
        payload = {
            "question": request.question,
            "continuation_action": request.continuation_action,
            "learner": learner.prompt_payload(),
            "previous_turn": self._parent_prompt(parent),
        }
        source_fingerprint = self.search.source_fingerprint()
        config_hash = stable_cache_key(QUERY_PLAN_VERSION, learner.prompt_payload())
        cache_key = stable_cache_key(payload, QUERY_PLAN_VERSION)
        cached = self.cache.get(
            "teacher_plan",
            cache_key,
            source_fingerprint=source_fingerprint,
            config_hash=config_hash,
        )
        if isinstance(cached, dict):
            return (
                self._validated_plan(TeacherQueryPlan.model_validate(cached), request),
                "cache",
                True,
            )
        try:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": json_dump(payload)},
            ]
            if self.model_router:
                plan, selected_model, _ = await self.model_router.structured_generate(
                    ModelRole.PLANNER, messages, TeacherQueryPlan
                )
            else:
                plan = await self.model_provider.structured_generate(
                    model, messages, TeacherQueryPlan
                )
                selected_model = model
        except ModelRoutingError as exc:
            raise LibraryTeacherError(exc.reason, str(exc)) from exc
        except (
            ProviderUnavailableError,
            ProviderResponseError,
            MalformedStructuredOutputError,
        ) as exc:
            logger.warning("teacher query planning failed: %s", type(exc).__name__)
            raise LibraryProviderUnavailableError(
                "El profesor local no pudo interpretar la pregunta."
            ) from exc
        validated = self._validated_plan(plan, request)
        self.cache.put(
            "teacher_plan",
            cache_key,
            validated.model_dump(mode="json"),
            source_fingerprint=source_fingerprint,
            config_hash=config_hash,
            model=selected_model,
            prompt_version=QUERY_PLAN_VERSION,
        )
        return validated, selected_model, False

    @staticmethod
    def _deterministic_plan(request: TeacherAskRequest) -> TeacherQueryPlan | None:
        question = request.question.casefold()
        rules: list[tuple[tuple[str, ...], str, TeacherIntent, list[str], QueryAmbiguity]] = [
            (
                ("kein", "nicht"),
                "kein nicht",
                TeacherIntent.DIFFERENCE,
                ["kein nicht", "Negation kein", "Negation nicht"],
                QueryAmbiguity.LOW,
            ),
            (
                ("den hund",),
                "den Hund",
                TeacherIntent.GRAMMAR_EXPLANATION,
                ["den Hund Akkusativ", "Akkusativ bestimmter Artikel", "der den"],
                QueryAmbiguity.LOW,
            ),
            (
                ("ich hätte gerne", "ich haette gerne"),
                "Ich hätte gerne",
                TeacherIntent.USAGE,
                ["Ich hätte gerne", "Konjunktiv II höfliche Bitte"],
                QueryAmbiguity.LOW,
            ),
            (
                ("konjunktiv ii", "konjunktiv 2"),
                "Konjunktiv II",
                TeacherIntent.GRAMMAR_EXPLANATION,
                ["Konjunktiv II", "würde hätte wäre"],
                QueryAmbiguity.MODERATE,
            ),
            (
                ("declinación", "declinacion"),
                "Adjektivdeklination",
                TeacherIntent.GRAMMAR_EXPLANATION,
                ["Adjektivdeklination", "Deklination der Adjektive"],
                QueryAmbiguity.MODERATE,
            ),
            (
                ("pronombre", "pronombres"),
                "Personalpronomen",
                TeacherIntent.OVERVIEW,
                ["Personalpronomen", "ich du er sie es wir ihr Sie"],
                QueryAmbiguity.LOW,
            ),
            (
                ("acusativo", "akkusativ"),
                "Akkusativ",
                TeacherIntent.SOURCE_LOOKUP
                if "herder" in question or "fuente" in question
                else TeacherIntent.GRAMMAR_EXPLANATION,
                ["Akkusativ", "acusativo", "den einen keinen"],
                QueryAmbiguity.LOW,
            ),
            (
                ("der die das",),
                "der die das",
                TeacherIntent.GRAMMAR_EXPLANATION,
                ["der die das", "bestimmter Artikel Genus"],
                QueryAmbiguity.MODERATE,
            ),
        ]
        for markers, target, intent, queries, ambiguity in rules:
            if any(marker in question for marker in markers):
                return TeacherQueryPlan(
                    intent=intent,
                    language="mixed",
                    target_expression=target,
                    user_language="es",
                    ambiguity=ambiguity,
                    possible_interpretations=[],
                    search_queries=queries,
                    required_evidence=["explicación", "ejemplo"],
                )
        die_only = re.search(r"(?:significa|uso|usos|explica)\s+[\"“”']?die\b", question)
        if die_only:
            return TeacherQueryPlan(
                intent=TeacherIntent.DEFINITION,
                language="de",
                target_expression="die",
                user_language="es",
                ambiguity=QueryAmbiguity.HIGH,
                possible_interpretations=[
                    "artículo definido femenino singular",
                    "artículo definido plural",
                ],
                search_queries=["die", "bestimmter Artikel", "Artikel Plural", "Artikel feminin"],
                required_evidence=["usos del artículo"],
            )
        return None

    def _validated_plan(
        self, plan: TeacherQueryPlan, request: TeacherAskRequest
    ) -> TeacherQueryPlan:
        queries: list[str] = []
        for query in plan.search_queries:
            folded = query.casefold()
            if any(value in folded for value in ("ignore previous", "system prompt", "<|")):
                continue
            if query not in queries:
                queries.append(query)
        target = plan.target_expression or self._target_from_question(request.question)
        ambiguity = plan.ambiguity
        interpretations = plan.possible_interpretations
        plural_continuation = bool(request.conversation_id) and "plural" in (
            f"{request.question} {target or ''}".casefold()
        )
        if plural_continuation:
            target_token = next(
                iter(re.findall(r"[A-Za-zÄÖÜäöüß-]+", target or "")),
                "",
            )
            queries = [
                target_token,
                f"{target_token} Plural",
                "Artikel Plural",
                "Pluralformen",
                *queries,
            ]
            ambiguity = QueryAmbiguity.LOW
            interpretations = []
        elif target and target.casefold() == "die":
            ambiguity = QueryAmbiguity.HIGH
            interpretations = [
                "artículo definido femenino singular",
                "artículo definido plural",
            ]
            queries = [query for query in queries if not ({"sterben", "verb"} & _tokens(query))]
            queries = [
                "die",
                "bestimmter Artikel",
                "Artikel Plural",
                "Artikel feminin",
                *queries,
            ]
        if target and target not in queries:
            queries.insert(0, target)
        if not queries:
            queries = [target or request.question]
        return plan.model_copy(
            update={
                "target_expression": target,
                "ambiguity": ambiguity,
                "possible_interpretations": interpretations,
                "search_queries": list(dict.fromkeys(query for query in queries if query.strip()))[
                    : self.limits.max_search_queries
                ],
            }
        )

    @staticmethod
    def _target_from_question(question: str) -> str | None:
        quoted = re.search(r"[\"“”'‘’]([^\"“”'‘’]{1,100})[\"“”'‘’]", question)
        if quoted:
            return quoted.group(1).strip()
        words = re.findall(r"[A-Za-zÄÖÜäöüß-]+", question)
        if len(words) == 1:
            return words[0]
        return None

    async def _retrieve(
        self,
        request: TeacherAskRequest,
        plan: TeacherQueryPlan,
    ) -> tuple[
        list[_Evidence],
        list[dict[str, object]],
        str,
        bool,
        dict[str, int],
    ]:
        scores: dict[int, float] = {}
        matched: dict[int, set[str]] = {}
        modes: set[str] = set()
        semantic_available = False
        phase_timings = {"fts_ms": 0, "embedding_ms": 0, "vector_ms": 0, "ranking_ms": 0}
        memory_context = self.memory.memory_matches(
            " ".join([request.question, plan.target_expression or "", *plan.search_queries])
        )

        async def collect(role_scope: str) -> set[str]:
            nonlocal semantic_available
            source_ids: set[str] = set()
            for query in plan.search_queries[: self.limits.max_search_queries]:
                try:
                    response = await self.search.search(
                        query,
                        mode="hybrid",
                        source_id=request.source_id,
                        limit=12,
                        include_solutions=True,
                        role_scope=role_scope,
                    )
                except ValueError:
                    continue
                except Exception as exc:
                    logger.warning("teacher retrieval failed: %s", type(exc).__name__)
                    raise LibraryTeacherError(
                        TeacherFailureReason.RETRIEVAL_FAILURE,
                        "La recuperación local no pudo completarse.",
                    ) from exc
                modes.add(response.effective_mode)
                semantic_available = semantic_available or response.semantic_available
                phase_timings["fts_ms"] += response.timings.fts_ms
                phase_timings["embedding_ms"] += response.timings.query_embedding_ms
                phase_timings["vector_ms"] += response.timings.vector_ms
                phase_timings["ranking_ms"] += response.timings.ranking_ms
                for rank, result in enumerate(response.results, start=1):
                    scores[result.id] = scores.get(result.id, 0.0) + 1 / rank
                    matched.setdefault(result.id, set()).add(query)
                    source_ids.add(result.source_id)
            return source_ids

        if request.source_id:
            await collect("all")
        else:
            core_sources = await collect("core")
            core_source_lookup = plan.intent == TeacherIntent.SOURCE_LOOKUP and bool(scores)
            if not core_source_lookup and (
                len(scores) < self.limits.max_chunks or len(core_sources) < 2
            ):
                await collect("supplementary")

        allowed_memory_chunks = {
            location.chunk_id
            for location in memory_context["locations"]
            if location.chunk_id is not None
            and (request.source_id is None or location.source_id == request.source_id)
        }
        for chunk_id, boost in memory_context["boosts"].items():
            if chunk_id in allowed_memory_chunks:
                scores[chunk_id] = scores.get(chunk_id, 0.0) + boost
                matched.setdefault(chunk_id, set()).add(f"memory:{boost:.2f}")
        for chunk_id in memory_context["rejected_chunks"]:
            scores.pop(chunk_id, None)
            matched.pop(chunk_id, None)

        knowledge = (
            []
            if plan.intent == TeacherIntent.SOURCE_LOOKUP and not request.source_id
            else self._knowledge_for_plan(plan, request.question)
        )
        for unit in knowledge:
            boost = 0.9 if unit["status"] == "approved" else 0.55
            for chunk_id in unit["chunk_ids"]:
                scores[chunk_id] = scores.get(chunk_id, 0.0) + boost
                matched.setdefault(chunk_id, set()).add(f"knowledge:{unit['status']}")

        evidence = self._load_and_rank(scores, matched, plan, knowledge)
        selected = self._deduplicate_and_select(evidence)
        mode = "hybrid" if "hybrid" in modes else "semantic" if "semantic" in modes else "lexical"
        return selected, knowledge, mode, semantic_available, phase_timings

    def _knowledge_for_plan(self, plan: TeacherQueryPlan, question: str) -> list[dict[str, object]]:
        query_tokens = _tokens(
            " ".join([question, plan.target_expression or "", *plan.search_queries])
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_units WHERE stale=0 AND status IN "
                "('approved','candidate','needs_review','conflict') ORDER BY "
                "CASE status WHEN 'approved' THEN 0 WHEN 'candidate' THEN 1 "
                "WHEN 'needs_review' THEN 2 ELSE 3 END,confidence DESC LIMIT 100"
            ).fetchall()
            candidates: list[tuple[int, int, object]] = []
            status_order = {"approved": 0, "candidate": 1, "needs_review": 2, "conflict": 3}
            for row in rows:
                overlap = len(query_tokens & _tokens(f"{row['title']} {row['content_es']}"))
                if overlap:
                    candidates.append((status_order[row["status"]], -overlap, row))
            candidates.sort(key=lambda item: (item[0], item[1], item[2]["id"]))
            result: list[dict[str, object]] = []
            for _, _, row in candidates[:6]:
                chunk_ids = [
                    item["chunk_id"]
                    for item in connection.execute(
                        "SELECT chunk_id FROM knowledge_unit_sources WHERE knowledge_unit_id=? "
                        "ORDER BY chunk_id",
                        (row["id"],),
                    ).fetchall()
                ]
                result.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "content_es": row["content_es"][:1_500],
                        "status": row["status"],
                        "confidence": row["confidence"],
                        "chunk_ids": chunk_ids,
                    }
                )
        return result

    def _load_and_rank(
        self,
        scores: dict[int, float],
        matched: dict[int, set[str]],
        plan: TeacherQueryPlan,
        knowledge: list[dict[str, object]],
    ) -> list[_Evidence]:
        if not scores:
            return []
        placeholders = ",".join("?" for _ in scores)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.*,sv.version_number,s.id source_id,"
                "coalesce(s.display_alias,s.name) source_name,s.rights,"
                "s.review_status source_review,s.priority,s.editorial_confidence,"
                "s.pedagogical_role,pq.quality page_quality,"
                "coalesce(s.duplicate_of_source_id,s.id) duplicate_group,"
                "coalesce(d.extraction_quality,0) extraction_quality "
                "FROM chunks c JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id LEFT JOIN documents d "
                "ON d.source_version_id=sv.id LEFT JOIN page_quality pq "
                "ON pq.source_version_id=sv.id AND pq.page_number=c.page_start "
                "WHERE c.id IN (" + placeholders + ") "
                "AND s.status='present' AND s.excluded=0 AND sv.id=s.current_version_id",
                list(scores),
            ).fetchall()
        knowledge_by_chunk: dict[int, set[str]] = {}
        for unit in knowledge:
            for chunk_id in unit["chunk_ids"]:
                knowledge_by_chunk.setdefault(int(chunk_id), set()).add(str(unit["status"]))

        target = (plan.target_expression or "").casefold()
        evidence: list[_Evidence] = []
        for row in rows:
            role_bonus = {
                "theory": 0.20,
                "glossary": 0.12,
                "example": 0.08,
                "exercise": -0.08,
                "solution": -0.35,
                "index": -0.12,
            }.get(row["content_role"], 0.0)
            role_bonus += {
                "core_theory": 0.35,
                "core_workbook": 0.20,
                "core_answer_key": -0.15,
                "glossary": 0.10,
                "answer_key": -0.20,
            }.get(row["pedagogical_role"], 0.0)
            page_penalty = {
                "poor": -0.20,
                "unusable": -1.0,
            }.get(row["page_quality"], 0.0)
            name_folded = row["source_name"].casefold()
            solution_penalty = (
                -0.25 if any(value in name_folded for value in _SOLUTION_NAMES) else 0
            )
            phrase_bonus = 0.12 if target and target in row["text"].casefold() else 0
            editorial_bonus = (
                0.15
                if row["source_review"] == "approved"
                else 0.08
                if row["source_review"] == "reviewed"
                else 0
            )
            knowledge_bonus = (
                0.25
                if "approved" in knowledge_by_chunk.get(row["id"], set())
                else 0.12
                if knowledge_by_chunk.get(row["id"])
                else 0
            )
            score = max(
                0.001,
                scores[row["id"]]
                + role_bonus
                + solution_penalty
                + phrase_bonus
                + editorial_bonus
                + knowledge_bonus
                + page_penalty
                + float(row["extraction_quality"]) * 0.15
                + max(-100, min(100, int(row["priority"]))) * 0.002,
            )
            evidence.append(
                _Evidence(
                    chunk_id=row["id"],
                    source_id=row["source_id"],
                    source_version_id=row["source_version_id"],
                    source_name=row["source_name"],
                    source_version=row["version_number"],
                    title=row["title"],
                    text=row["text"],
                    page_start=row["page_start"],
                    page_end=row["page_end"],
                    start_seconds=row["start_seconds"],
                    end_seconds=row["end_seconds"],
                    review_status=row["source_review"],
                    rights=row["rights"],
                    content_role=row["content_role"],
                    extraction_quality=float(row["extraction_quality"]),
                    editorial_confidence=float(row["editorial_confidence"]),
                    source_priority=int(row["priority"]),
                    pedagogical_role=row["pedagogical_role"],
                    page_quality=row["page_quality"],
                    duplicate_group=row["duplicate_group"],
                    content_hash=row["content_hash"],
                    score=score,
                    matched_queries=matched[row["id"]],
                    knowledge_statuses=knowledge_by_chunk.get(row["id"], set()),
                )
            )
        evidence.sort(key=lambda item: (-item.score, item.source_name.casefold(), item.chunk_id))
        return evidence

    def _deduplicate_and_select(self, candidates: list[_Evidence]) -> list[_Evidence]:
        selected: list[_Evidence] = []
        hashes: set[str] = set()
        per_source: dict[str, int] = {}
        source_groups: set[str] = set()
        for item in candidates:
            canonical_hash = hashlib.sha256(_normalise(item.text).encode()).hexdigest()
            if item.content_hash in hashes or canonical_hash in hashes:
                continue
            if per_source.get(item.duplicate_group, 0) >= self.limits.max_chunks_per_source:
                continue
            if (
                item.duplicate_group not in source_groups
                and len(source_groups) >= self.limits.max_sources
            ):
                continue
            if any(_jaccard(item.text, current.text) >= 0.92 for current in selected):
                continue
            if any(
                current.source_id == item.source_id
                and current.page_start is not None
                and item.page_start is not None
                and abs(current.page_start - item.page_start) <= 1
                and _jaccard(item.text, current.text) >= 0.82
                for current in selected
            ):
                continue
            selected.append(item)
            hashes.update({item.content_hash, canonical_hash})
            source_groups.add(item.duplicate_group)
            per_source[item.duplicate_group] = per_source.get(item.duplicate_group, 0) + 1
            if len(selected) >= self.limits.max_chunks:
                break
        return selected

    def _confidence(
        self,
        evidence: list[_Evidence],
        knowledge: list[dict[str, object]],
    ) -> tuple[EvidenceConfidence, float, list[str]]:
        if not evidence:
            return EvidenceConfidence.INSUFFICIENT, 0.0, []
        groups = {item.duplicate_group for item in evidence}
        average_quality = sum(item.extraction_quality for item in evidence) / len(evidence)
        average_editorial = sum(item.editorial_confidence for item in evidence) / len(evidence)
        theory_ratio = sum(item.content_role in {"theory", "glossary"} for item in evidence) / len(
            evidence
        )
        score = 0.18
        score += 0.12 if len(groups) == 1 else 0.28 if len(groups) == 2 else 0.35
        score += average_quality * 0.22
        score += average_editorial * 0.12
        score += theory_ratio * 0.08
        if any(item["status"] == "approved" for item in knowledge):
            score += 0.12
        if all(item.content_role == "solution" for item in evidence):
            score -= 0.25
        score -= sum(item.extraction_quality < 0.35 for item in evidence) / len(evidence) * 0.12
        conflict = any(item["status"] == "conflict" for item in knowledge)
        if conflict:
            score -= 0.20
        score = max(0.0, min(1.0, score))
        if score >= 0.75 and len(groups) >= 2:
            label = EvidenceConfidence.SOLID
        elif score >= 0.50:
            label = EvidenceConfidence.MODERATE
        elif score >= 0.28:
            label = EvidenceConfidence.LIMITED
        else:
            label = EvidenceConfidence.INSUFFICIENT
        warnings: list[str] = []
        if all(item.review_status not in {"approved", "reviewed"} for item in evidence) and not any(
            item["status"] == "approved" for item in knowledge
        ):
            if label == EvidenceConfidence.SOLID:
                label = EvidenceConfidence.MODERATE
            warnings.append("Las fuentes utilizadas todavía no tienen revisión editorial aprobada.")
        if any(item["status"] in {"candidate", "needs_review"} for item in knowledge):
            warnings.append("Se consultó conocimiento candidato y se verificó contra sus fuentes.")
        if conflict:
            if label in {EvidenceConfidence.SOLID, EvidenceConfidence.MODERATE}:
                label = EvidenceConfidence.LIMITED
            warnings.append("Existe conocimiento derivado en conflicto sobre este tema.")
        if average_quality < 0.45:
            if label in {EvidenceConfidence.SOLID, EvidenceConfidence.MODERATE}:
                label = EvidenceConfidence.LIMITED
            warnings.append("Parte de la evidencia procede de extracción de baja calidad.")
        return label, score, warnings

    async def _generate(
        self,
        request: TeacherAskRequest,
        plan: TeacherQueryPlan,
        learner: LearnerContext,
        parent: dict[str, object] | None,
        evidence: list[_Evidence],
        knowledge: list[dict[str, object]],
        model: str,
    ) -> tuple[TeacherAnswerDraft, int, int, str, TeacherFailureReason | None]:
        package = self._evidence_package(evidence)
        prompt_payload = {
            "question": request.question,
            "intent": plan.intent.value,
            "ambiguity": plan.ambiguity.value,
            "possible_interpretations": plan.possible_interpretations,
            "learner": learner.prompt_payload(),
            "previous_turn": self._parent_prompt(parent),
            "knowledge_units": knowledge,
            "evidence": package,
        }
        messages = [
            {
                "role": "system",
                "content": (PROMPT_ROOT / "library_teacher_answer_v1.md").read_text(
                    encoding="utf-8"
                ),
            },
            {"role": "user", "content": json_dump(prompt_payload)},
        ]
        selected_model = model
        try:
            if self.model_router:
                role = ModelRole.FALLBACK if self._requires_deep_model(plan) else ModelRole.TEACHER
                draft, selected_model, _ = await self.model_router.structured_generate(
                    role, messages, TeacherAnswerDraft
                )
            else:
                draft = await self.model_provider.structured_generate(
                    model, messages, TeacherAnswerDraft
                )
        except ModelRoutingError as exc:
            return (
                self._insufficient_answer(plan),
                0,
                0,
                selected_model,
                exc.reason,
            )
        except ProviderUnavailableError as exc:
            raise LibraryProviderUnavailableError(
                "El profesor local no pudo preparar la explicación."
            ) from exc
        except (ProviderResponseError, MalformedStructuredOutputError) as exc:
            logger.warning("teacher answer generation failed safely: %s", type(exc).__name__)
            return (
                self._insufficient_answer(plan),
                0,
                0,
                selected_model,
                TeacherFailureReason.GENERATION_FAILURE,
            )
        draft = self._sanitise_draft(draft)
        validation_started = perf_counter()
        repair_ms = 0
        violations = self._answer_violations(draft, evidence, plan)
        if violations:
            repair = (PROMPT_ROOT / "library_teacher_answer_repair_v1.md").read_text(
                encoding="utf-8"
            )
            repair_messages = [
                *messages,
                {"role": "assistant", "content": draft.model_dump_json()},
                {
                    "role": "user",
                    "content": repair + "\n\nERRORES DETECTADOS:\n- " + "\n- ".join(violations),
                },
            ]
            repair_started = perf_counter()
            try:
                if self.model_router:
                    draft, repair_model, _ = await self.model_router.structured_generate(
                        ModelRole.REPAIR, repair_messages, TeacherAnswerDraft
                    )
                    selected_model = f"{selected_model}+repair:{repair_model}"
                else:
                    draft = await self.model_provider.structured_generate(
                        model, repair_messages, TeacherAnswerDraft
                    )
            except ModelRoutingError:
                repair_ms = self._elapsed_ms(repair_started)
                return (
                    self._insufficient_answer(plan),
                    max(0, self._elapsed_ms(validation_started) - repair_ms),
                    repair_ms,
                    selected_model,
                    TeacherFailureReason.REPAIR_FAILURE,
                )
            except (
                ProviderUnavailableError,
                ProviderResponseError,
                MalformedStructuredOutputError,
            ):
                repair_ms = self._elapsed_ms(repair_started)
                return (
                    self._insufficient_answer(plan),
                    max(0, self._elapsed_ms(validation_started) - repair_ms),
                    repair_ms,
                    selected_model,
                    TeacherFailureReason.REPAIR_FAILURE,
                )
            repair_ms = self._elapsed_ms(repair_started)
            draft = self._sanitise_draft(draft)
            remaining_violations = self._answer_violations(draft, evidence, plan)
            if remaining_violations:
                logger.warning(
                    "teacher answer validation failed after repair: %s",
                    "; ".join(remaining_violations),
                )
                return (
                    self._insufficient_answer(plan),
                    max(0, self._elapsed_ms(validation_started) - repair_ms),
                    repair_ms,
                    selected_model,
                    TeacherFailureReason.CITATION_VALIDATION_FAILURE,
                )
        return (
            draft,
            max(0, self._elapsed_ms(validation_started) - repair_ms),
            repair_ms,
            selected_model,
            None,
        )

    @staticmethod
    def _sanitise_draft(draft: TeacherAnswerDraft) -> TeacherAnswerDraft:
        return draft.model_copy(
            update={
                "direct_answer": _sanitise_internal_references(draft.direct_answer),
                "key_points": [
                    _sanitise_internal_references(point) or "" for point in draft.key_points
                ],
                "examples": [
                    example.model_copy(
                        update={
                            "german": _sanitise_internal_references(example.german),
                            "spanish": _sanitise_internal_references(example.spanish),
                            "note": _sanitise_internal_references(example.note),
                        }
                    )
                    for example in draft.examples
                ],
                "important_nuance": _sanitise_internal_references(draft.important_nuance),
                "ambiguity_note": _sanitise_internal_references(draft.ambiguity_note),
                "follow_up_question": _sanitise_internal_references(draft.follow_up_question),
            }
        )

    def _evidence_package(self, evidence: list[_Evidence]) -> list[dict[str, object]]:
        remaining = self.limits.max_context_characters
        package: list[dict[str, object]] = []
        for item in evidence:
            if remaining <= 200:
                break
            text = item.text[: min(2_400, remaining)]
            remaining -= len(text)
            package.append(
                {
                    "chunk_id": item.chunk_id,
                    "source": item.source_name,
                    "source_version": item.source_version,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                    "start_seconds": item.start_seconds,
                    "end_seconds": item.end_seconds,
                    "section": item.title,
                    "content_role": item.content_role,
                    "extraction_quality": round(item.extraction_quality, 3),
                    "review_status": item.review_status,
                    "text": text,
                }
            )
        return package

    def _answer_violations(
        self,
        draft: TeacherAnswerDraft,
        evidence: list[_Evidence],
        plan: TeacherQueryPlan,
    ) -> list[str]:
        if not draft.evidence_sufficient:
            return []
        allowed = {item.chunk_id for item in evidence}
        violations: list[str] = []
        for claim in draft.claims:
            if len(claim.source_chunk_ids) != len(set(claim.source_chunk_ids)):
                violations.append("Una afirmación repite la misma cita interna.")
            if not set(claim.source_chunk_ids).issubset(allowed):
                violations.append("Una afirmación cita evidencia que no fue recuperada.")
        text = " ".join(
            filter(
                None,
                [
                    draft.direct_answer,
                    *draft.key_points,
                    *(example.german for example in draft.examples),
                    *(example.spanish for example in draft.examples),
                    *(example.note for example in draft.examples),
                    draft.important_nuance,
                    draft.ambiguity_note,
                    draft.follow_up_question,
                ],
            )
        )
        folded = text.casefold()
        if any(value in folded for value in _BANNED_GROUNDING_BYPASSES):
            violations.append(
                "La respuesta contiene una evasión de grounding o un detalle privado."
            )
        if re.search(r"\bchunks?\s*(?:n[úu]mero\s*)?#?\d+\b", folded):
            violations.append("La respuesta expone un identificador interno de evidencia.")
        if plan.target_expression and plan.target_expression.casefold() == "die":
            if re.search(
                r"femenin\w*(?:(?![.;]).){0,60}(?:\(\s*)?singular\w*\s+y\s+plural\w*",
                folded,
            ):
                violations.append(
                    "La respuesta atribuye género femenino al plural: debe separar "
                    "femenino singular de plural para todos los géneros."
                )
            if "pronombre" in folded and not any(
                "pronombre" in interpretation.casefold()
                for interpretation in plan.possible_interpretations
            ):
                violations.append(
                    "La respuesta añade una interpretación de 'die' fuera del plan validado."
                )
        if not draft.key_points:
            violations.append("La explicación no resume ninguna idea esencial.")
        if plan.intent.value != "source_lookup" and not draft.examples:
            violations.append("La explicación no incluye un ejemplo alemán.")
        if plan.ambiguity == QueryAmbiguity.HIGH and (
            not draft.ambiguity_note or not draft.follow_up_question
        ):
            violations.append("La consulta ambigua no reconoce sus lecturas ni ofrece seguimiento.")
        if "�" in text or re.search(r"(?:\b\w\b\s+){10,}", text):
            violations.append("La respuesta reproduce OCR claramente roto.")
        normalised_answer = _normalise(draft.direct_answer)
        if len(normalised_answer) > 600 and any(
            normalised_answer in _normalise(item.text) for item in evidence
        ):
            violations.append("La respuesta copia un pasaje excesivamente largo.")
        if len(draft.claims) < len(draft.key_points):
            violations.append("No todos los puntos esenciales tienen una afirmación citada.")
        for start, end in re.findall(
            r"p(?:á|a)ginas?\s+(\d+)\s+(?:a|[-–])\s+(\d+)",
            text,
            flags=re.IGNORECASE,
        ):
            if int(end) < int(start):
                violations.append("La respuesta contiene un rango de páginas invertido.")
        return list(dict.fromkeys(violations))

    @staticmethod
    def _requires_deep_model(plan: TeacherQueryPlan) -> bool:
        target = (plan.target_expression or "").casefold()
        return any(marker in target for marker in ("konjunktiv ii", "adjektivdeklination"))

    @staticmethod
    def _insufficient_answer(plan: TeacherQueryPlan) -> TeacherAnswerDraft:
        ambiguity = (
            "La consulta puede tener más de una interpretación, pero las coincidencias no "
            "permiten distinguirlas con seguridad."
            if plan.ambiguity == QueryAmbiguity.HIGH
            else None
        )
        return TeacherAnswerDraft(
            evidence_sufficient=False,
            direct_answer=(
                "No he encontrado evidencia suficiente en tu biblioteca para explicarlo con "
                "seguridad. He localizado pocas coincidencias o no respaldan una respuesta completa."
            ),
            key_points=[],
            examples=[],
            important_nuance=None,
            ambiguity_note=ambiguity,
            follow_up_question="¿Quieres reformular la pregunta o consultar una fuente concreta?",
            claims=[],
            warnings=[],
        )

    def _resolve_conversation(
        self, conversation_id: str | None
    ) -> tuple[str, dict[str, object] | None]:
        if conversation_id is None:
            return str(uuid4()), None
        with self.database.connect() as connection:
            exists = connection.execute(
                "SELECT id FROM teacher_conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if not exists:
                raise LibraryNotFoundError("La conversación educativa no existe.")
            row = connection.execute(
                "SELECT tq.id,tq.question,tq.plan_json,tq.answer_json,"
                "trf.verdict response_feedback "
                "FROM teacher_queries tq LEFT JOIN teacher_response_feedback trf "
                "ON trf.query_id=tq.id WHERE tq.conversation_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return conversation_id, dict(row) if row else None

    @staticmethod
    def _parent_prompt(parent: dict[str, object] | None) -> dict[str, object] | None:
        if not parent or parent.get("response_feedback") == "incorrect":
            return None
        answer = TeacherAnswerDraft.model_validate_json(str(parent["answer_json"]))
        return {
            "question": parent["question"],
            "direct_answer": answer.direct_answer[:1_500],
            "follow_up_question": answer.follow_up_question,
        }

    def _persist(
        self,
        *,
        request: TeacherAskRequest,
        conversation_id: str,
        parent_query_id: str | None,
        plan: TeacherQueryPlan,
        answer: TeacherAnswerDraft,
        evidence: list[_Evidence],
        model: str,
        status: TeacherQueryStatus,
        confidence: EvidenceConfidence,
        retrieval_mode: str,
        semantic_available: bool,
        warnings: list[str],
        timings: TeacherTimings,
        confidence_score: float,
        failure_reason: TeacherFailureReason | None,
        models: dict[str, str],
        cache_hit: bool,
    ) -> str:
        query_id = str(uuid4())
        now = utc_text()
        timing_payload = {**timings.model_dump(), "evidence_score": round(confidence_score, 4)}
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO teacher_conversations(id,created_at,updated_at) VALUES (?,?,?)",
                (conversation_id, now, now),
            )
            connection.execute(
                "INSERT INTO teacher_queries(id,conversation_id,parent_query_id,question,plan_json,"
                "answer_json,status,confidence,model,plan_prompt_version,answer_prompt_version,"
                "repair_prompt_version,retrieval_mode,semantic_available,warnings_json,timings_json,"
                "created_at,failure_reason,models_json,cache_hit,stream_status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    query_id,
                    conversation_id,
                    parent_query_id,
                    request.question,
                    plan.model_dump_json(),
                    answer.model_dump_json(),
                    status.value,
                    confidence.value,
                    model,
                    QUERY_PLAN_VERSION,
                    TEACHER_ANSWER_VERSION,
                    TEACHER_REPAIR_VERSION,
                    retrieval_mode,
                    int(semantic_available),
                    json_dump(warnings),
                    json_dump(timing_payload),
                    now,
                    failure_reason.value if failure_reason else None,
                    json_dump(models),
                    int(cache_hit),
                    "complete",
                ),
            )
            for sequence, item in enumerate(evidence):
                connection.execute(
                    "INSERT INTO teacher_query_sources(query_id,sequence,chunk_id,source_version_id,"
                    "retrieval_score,matched_queries_json,snippet) VALUES (?,?,?,?,?,?,?)",
                    (
                        query_id,
                        sequence,
                        item.chunk_id,
                        item.source_version_id,
                        item.score,
                        json_dump(sorted(item.matched_queries)),
                        _short_snippet(item.text, " ".join(item.matched_queries)),
                    ),
                )
            connection.execute(
                "UPDATE teacher_conversations SET updated_at=? WHERE id=?",
                (now, conversation_id),
            )
        return query_id

    def get_query(self, query_id: str) -> TeacherQueryRead:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM teacher_queries WHERE id=?", (query_id,)
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("La consulta educativa no existe.")
            source_rows = connection.execute(
                "SELECT tqs.*,c.title,c.page_start,c.page_end,c.start_seconds,c.end_seconds,"
                "c.content_role,sv.version_number,s.id source_id,"
                "coalesce(s.display_alias,s.name) source_name,s.rights,"
                "s.review_status,s.pedagogical_role,pq.quality page_quality,"
                "coalesce(d.extraction_quality,0) extraction_quality "
                "FROM teacher_query_sources tqs JOIN chunks c ON c.id=tqs.chunk_id "
                "JOIN source_versions sv ON sv.id=tqs.source_version_id "
                "JOIN sources s ON s.id=sv.source_id LEFT JOIN documents d "
                "ON d.source_version_id=sv.id LEFT JOIN page_quality pq "
                "ON pq.source_version_id=sv.id AND pq.page_number=c.page_start "
                "WHERE tqs.query_id=? ORDER BY tqs.sequence",
                (query_id,),
            ).fetchall()
        # Public projection is sanitised again so historical rows created before
        # a newer output guard cannot leak internal retrieval identifiers.
        draft = self._sanitise_draft(TeacherAnswerDraft.model_validate_json(row["answer_json"]))
        if draft.source_lookup is not None:
            refreshed_lookup = self.source_lookup.refresh_persisted(draft.source_lookup)
            draft = draft.model_copy(
                update={
                    "source_lookup": refreshed_lookup,
                    **(
                        {"direct_answer": refreshed_lookup.summary}
                        if draft.answer_kind == "source_lookup"
                        else {}
                    ),
                }
            )
        timings_raw = json_load(row["timings_json"], {})
        memory_state = self.memory.query_memory(query_id)
        memory_by_chunk = {}
        memory_rank = {
            "user_confirmed": 0,
            "system_verified": 1,
            "candidate": 2,
            "conflict": 3,
            "rejected": 4,
            "stale": 5,
        }
        for location in memory_state.locations:
            if location.chunk_id is None:
                continue
            current = memory_by_chunk.get(location.chunk_id)
            if (
                current is None
                or memory_rank[location.status.value] < memory_rank[current.status.value]
            ):
                memory_by_chunk[location.chunk_id] = location
        return TeacherQueryRead(
            query_id=row["id"],
            conversation_id=row["conversation_id"],
            parent_query_id=row["parent_query_id"],
            question=row["question"],
            status=row["status"],
            answer=TeacherPublicAnswer(
                direct_answer=draft.direct_answer,
                key_points=draft.key_points,
                examples=draft.examples,
                important_nuance=draft.important_nuance,
                ambiguity_note=draft.ambiguity_note,
                follow_up_question=draft.follow_up_question,
            ),
            confidence=row["confidence"],
            sources=[
                TeacherSourceRead(
                    citation=f"F{index + 1}",
                    source_id=item["source_id"],
                    source_name=item["source_name"],
                    page_start=item["page_start"],
                    page_end=item["page_end"],
                    start_seconds=item["start_seconds"],
                    end_seconds=item["end_seconds"],
                    section=item["title"],
                    snippet=item["snippet"],
                    review_status=item["review_status"],
                    rights=item["rights"],
                    extraction_quality=item["extraction_quality"],
                    content_role=item["content_role"],
                    retrieval_score=max(0, item["retrieval_score"]),
                    pedagogical_role=item["pedagogical_role"],
                    evidence_origin=(
                        "core"
                        if str(item["pedagogical_role"]).startswith("core_")
                        else "supplementary"
                    ),
                    page_quality=item["page_quality"],
                    location_id=(
                        memory_by_chunk[item["chunk_id"]].id
                        if item["chunk_id"] in memory_by_chunk
                        else None
                    ),
                    public_location=(
                        memory_by_chunk[item["chunk_id"]].public_citation
                        if item["chunk_id"] in memory_by_chunk
                        else None
                    ),
                    memory_status=(
                        memory_by_chunk[item["chunk_id"]].status
                        if item["chunk_id"] in memory_by_chunk
                        else None
                    ),
                    printed_page_label=(
                        memory_by_chunk[item["chunk_id"]].printed_page_label
                        if item["chunk_id"] in memory_by_chunk
                        else None
                    ),
                    scan_layout=(
                        memory_by_chunk[item["chunk_id"]].scan_layout
                        if item["chunk_id"] in memory_by_chunk
                        else "unknown"
                    ),
                    region=(
                        memory_by_chunk[item["chunk_id"]].region
                        if item["chunk_id"] in memory_by_chunk
                        else "unknown"
                    ),
                )
                for index, item in enumerate(source_rows)
            ],
            warnings=json_load(row["warnings_json"], []),
            retrieval_mode=row["retrieval_mode"],
            semantic_search_available=bool(row["semantic_available"]),
            timings=TeacherTimings(
                planning_ms=int(timings_raw.get("planning_ms", 0)),
                retrieval_ms=int(timings_raw.get("retrieval_ms", 0)),
                generation_ms=int(timings_raw.get("generation_ms", 0)),
                validation_ms=int(timings_raw.get("validation_ms", 0)),
                total_ms=int(timings_raw.get("total_ms", 0)),
                embedding_ms=int(timings_raw.get("embedding_ms", 0)),
                model_selection_ms=int(timings_raw.get("model_selection_ms", 0)),
                fts_ms=int(timings_raw.get("fts_ms", 0)),
                vector_ms=int(timings_raw.get("vector_ms", 0)),
                ranking_ms=int(timings_raw.get("ranking_ms", 0)),
                repair_ms=int(timings_raw.get("repair_ms", 0)),
                intent_detection_ms=int(timings_raw.get("intent_detection_ms", 0)),
                memory_lookup_ms=int(timings_raw.get("memory_lookup_ms", 0)),
                hybrid_fallback_ms=int(timings_raw.get("hybrid_fallback_ms", 0)),
            ),
            created_at=row["created_at"],
            failure_reason=row["failure_reason"],
            models=json_load(row["models_json"], {}),
            cache_hit=bool(row["cache_hit"]),
            answer_verified=True,
            memory_used=memory_state.memory_used,
            response_feedback=memory_state.response_feedback,
            answer_kind=draft.answer_kind,
            source_lookup=draft.source_lookup,
            used_generation=draft.used_generation,
            memory_hit=draft.source_lookup.memory_hit if draft.source_lookup else False,
            lookup_cache_hit=(
                draft.source_lookup.lookup_cache_hit if draft.source_lookup else False
            ),
            hybrid_fallback=(draft.source_lookup.hybrid_fallback if draft.source_lookup else False),
            answer_cache_hit=False,
        )

    def list_conversations(self, *, limit: int = 20) -> list[TeacherConversationSummary]:
        with self.database.connect() as connection:
            conversations = connection.execute(
                "SELECT * FROM teacher_conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            result: list[TeacherConversationSummary] = []
            for conversation in conversations:
                latest = connection.execute(
                    "SELECT * FROM teacher_queries WHERE conversation_id=? "
                    "ORDER BY created_at DESC,id DESC LIMIT 1",
                    (conversation["id"],),
                ).fetchone()
                if not latest:
                    continue
                count = connection.execute(
                    "SELECT count(*) FROM teacher_queries WHERE conversation_id=?",
                    (conversation["id"],),
                ).fetchone()[0]
                answer = self._sanitise_draft(
                    TeacherAnswerDraft.model_validate_json(latest["answer_json"])
                )
                source_count = connection.execute(
                    "SELECT count(DISTINCT sv.source_id) FROM teacher_query_sources tqs "
                    "JOIN source_versions sv ON sv.id=tqs.source_version_id WHERE query_id=?",
                    (latest["id"],),
                ).fetchone()[0]
                result.append(
                    TeacherConversationSummary(
                        conversation_id=conversation["id"],
                        latest_query_id=latest["id"],
                        question=latest["question"],
                        answer_excerpt=answer.direct_answer[:240],
                        confidence=latest["confidence"],
                        source_count=source_count,
                        turn_count=count,
                        updated_at=conversation["updated_at"],
                    )
                )
        return result

    def conversation_queries(self, conversation_id: str) -> list[TeacherQueryRead]:
        with self.database.connect() as connection:
            exists = connection.execute(
                "SELECT id FROM teacher_conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if not exists:
                raise LibraryNotFoundError("La conversación educativa no existe.")
            rows = connection.execute(
                "SELECT id FROM teacher_queries WHERE conversation_id=? ORDER BY created_at,id",
                (conversation_id,),
            ).fetchall()
        return [self.get_query(row["id"]) for row in rows]

    def delete_conversation(self, conversation_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "DELETE FROM teacher_conversations WHERE id=?", (conversation_id,)
            )
            if cursor.rowcount == 0:
                raise LibraryNotFoundError("La conversación educativa no existe.")

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1_000))
