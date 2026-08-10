from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from llc_api.core.time import utc_now
from llc_api.educational_library.canonical_route import CanonicalRouteService
from llc_api.educational_library.editorial import LibraryEditorialService
from llc_api.educational_library.schemas import (
    CanonicalOutlineNodeRead,
    LibraryError,
    SourceRead,
)
from llc_api.models import (
    SkillEvidence,
    StudentSkill,
    StudyEvent,
    StudyMissionType,
    StudyNote,
    StudyPracticalStatus,
    StudyPreference,
    StudyQuestion,
    StudySectionState,
    StudySession,
    StudySessionStatus,
    StudyWorkbookLink,
)

from .exceptions import (
    StudyConflictError,
    StudyContractError,
    StudyLibraryUnavailableError,
    StudyNotFoundError,
)
from .missions import build_mission, build_plan, resolve_mission_type
from .schemas import (
    StudyDashboardRead,
    StudyDataRead,
    StudyMemoryRead,
    StudyMissionRead,
    StudyNoteRead,
    StudyNoteWrite,
    StudyPathRead,
    StudyPositionUpdate,
    StudyPreferenceRead,
    StudyPreferenceUpdate,
    StudyQuestionRead,
    StudyQuestionStatusUpdate,
    StudyQuestionWrite,
    StudyRecommendationRead,
    StudySectionRead,
    StudySectionStateUpdate,
    StudySessionCreate,
    StudySessionDeleteRead,
    StudySessionRead,
    StudySessionSummaryRead,
    StudyTransitionRequest,
    WorkbookLinkCreate,
    WorkbookLinkRead,
    WorkbookLinkReview,
    WorkbookLinkUpdate,
)


@dataclass(frozen=True)
class _RouteSection:
    id: int
    stable_key: str
    title: str
    topic: str | None
    page_start: int | None
    page_end: int | None
    editorial_status: str
    theme_number: int | None = None
    title_es: str | None = None
    title_de: str | None = None
    printed_start: int | None = None
    printed_end: int | None = None
    printed_end_origin: str = "unknown"
    reference_pdf_page: int | None = None
    manual_scan_layout: str | None = None
    manual_region: str | None = None
    outline: list[CanonicalOutlineNodeRead] = dc_field(default_factory=list)
    canonical: bool = False


class GuidedStudyService:
    """Persistent personal study state over read-only editorial library references."""

    def __init__(
        self,
        db: Session,
        editorial: LibraryEditorialService,
        canonical_route: CanonicalRouteService | None = None,
    ):
        self.db = db
        self.editorial = editorial
        self.canonical_route = canonical_route or CanonicalRouteService(editorial.database)

    def _core(self) -> tuple[SourceRead, SourceRead | None, list[_RouteSection]]:
        try:
            pair = self.editorial.core_pair()
            if pair.theory is None:
                raise StudyLibraryUnavailableError(
                    "El manual principal todavía no está configurado en la biblioteca."
                )
            status = self.canonical_route.status()
            if status.available:
                sections = [
                    _RouteSection(
                        id=topic.id,
                        stable_key=topic.stable_key,
                        title=topic.title_es,
                        topic=f"Tema {topic.theme_number}",
                        page_start=topic.manual_pdf_start,
                        page_end=topic.manual_pdf_end,
                        editorial_status=topic.editorial_status,
                        theme_number=topic.theme_number,
                        title_es=topic.title_es,
                        title_de=topic.title_de,
                        printed_start=topic.printed_start,
                        printed_end=topic.printed_end,
                        printed_end_origin=topic.printed_end_origin,
                        reference_pdf_page=topic.reference_pdf_page,
                        manual_scan_layout=topic.manual_scan_layout,
                        manual_region=topic.manual_region,
                        outline=topic.outline,
                        canonical=True,
                    )
                    for topic in self.canonical_route.list_topics()
                ]
            else:
                sections = [
                    _RouteSection(
                        id=section.id,
                        stable_key=section.stable_key,
                        title=section.title,
                        topic=section.topic,
                        page_start=section.page_start,
                        page_end=section.page_end,
                        editorial_status=section.editorial_status.value,
                    )
                    for section in self.editorial.list_sections(pair.theory.id)
                ]
        except StudyLibraryUnavailableError:
            raise
        except LibraryError as exc:
            raise StudyLibraryUnavailableError(
                "La ruta Herder no está disponible en este momento."
            ) from exc
        if not sections:
            raise StudyLibraryUnavailableError(
                "El manual principal todavía no tiene secciones editoriales."
            )
        return pair.theory, pair.workbook, sections

    def _preferences(self, *, create: bool = True) -> StudyPreference:
        preference = self.db.get(StudyPreference, 1)
        if preference is None:
            preference = StudyPreference(profile_id=1, mission_preference="automatic")
            if create:
                self.db.add(preference)
                self.db.flush()
        return preference

    @staticmethod
    def _source_name(source: SourceRead) -> str:
        return source.display_alias or source.canonical_title or source.name

    def path(self, *, query: str | None = None) -> StudyPathRead:
        theory, workbook, sections = self._core()
        state_rows = self.db.scalars(
            select(StudySectionState).where(StudySectionState.source_id == theory.id)
        ).all()
        states = {item.section_stable_key: item for item in state_rows}
        exact_aliases = self.canonical_route.legacy_exact_aliases()
        for item in state_rows:
            if item.section_stable_key.startswith("herder:"):
                continue
            canonical_key = exact_aliases.get(item.section_stable_key)
            if canonical_key is not None:
                states.setdefault(canonical_key, item)
        links = self.db.scalars(
            select(StudyWorkbookLink)
            .where(
                StudyWorkbookLink.theory_source_id == theory.id,
                StudyWorkbookLink.status != "rejected",
            )
            .order_by(StudyWorkbookLink.updated_at.desc())
        ).all()
        links_by_section: dict[str, StudyWorkbookLink] = {}
        for item in links:
            links_by_section.setdefault(item.section_stable_key, item)
            canonical_key = exact_aliases.get(item.section_stable_key)
            if canonical_key is not None:
                links_by_section.setdefault(canonical_key, item)
        recent = self.db.scalars(
            select(StudySession)
            .where(StudySession.source_id == theory.id)
            .order_by(StudySession.updated_at.desc())
        ).all()
        recent_by_section: dict[str, StudySession] = {}
        for item in recent:
            if item.section_stable_key:
                recent_by_section.setdefault(item.section_stable_key, item)
                canonical_key = exact_aliases.get(item.section_stable_key)
                if canonical_key is not None:
                    recent_by_section.setdefault(canonical_key, item)
        question_counts = dict(
            self.db.execute(
                select(StudyQuestion.section_stable_key, func.count(StudyQuestion.id))
                .where(StudyQuestion.status.in_(("open", "revisit")))
                .group_by(StudyQuestion.section_stable_key)
            ).all()
        )
        for legacy_key, canonical_key in exact_aliases.items():
            if legacy_key in question_counts:
                question_counts[canonical_key] = question_counts.get(canonical_key, 0) + int(
                    question_counts[legacy_key]
                )
        folded = query.casefold() if query else None
        items: list[StudySectionRead] = []
        visible_sections = [
            section
            for section in sections
            if section.canonical or section.editorial_status != "rejected"
        ]
        for order, section in enumerate(visible_sections, start=1):
            searchable = " ".join(
                [
                    f"Tema {section.theme_number}" if section.theme_number else "",
                    section.title,
                    section.title_de or "",
                    section.topic or "",
                    str(section.printed_start or ""),
                    *[
                        f"{node.title_es or ''} {node.title_de or ''} {node.printed_page or ''}"
                        for node in section.outline
                    ],
                ]
            ).casefold()
            if folded and folded not in searchable:
                continue
            state = states.get(section.stable_key)
            last = recent_by_section.get(section.stable_key)
            items.append(
                StudySectionRead(
                    id=section.id,
                    stable_key=section.stable_key,
                    order=order,
                    title=section.title,
                    topic=section.topic,
                    source_id=theory.id,
                    source_version=theory.current_version,
                    source_name=self._source_name(theory),
                    pdf_page_start=section.page_start,
                    pdf_page_end=section.page_end,
                    printed_page_label=(
                        state.printed_page_label
                        if state and state.printed_page_label
                        else str(section.printed_start)
                        if section.printed_start
                        else None
                    ),
                    editorial_status=section.editorial_status,
                    practical_status=(
                        StudyPracticalStatus(state.practical_status)
                        if state
                        else StudyPracticalStatus.NOT_STARTED
                    ),
                    current_pdf_page=state.current_pdf_page if state else None,
                    selection_origin=state.selection_origin if state else None,
                    last_activity_at=state.last_activity_at if state else None,
                    last_session_id=last.id if last else None,
                    open_questions=int(question_counts.get(section.stable_key, 0)),
                    workbook_link=(
                        self._workbook_read(links_by_section[section.stable_key])
                        if section.stable_key in links_by_section
                        else None
                    ),
                    theme_number=section.theme_number,
                    title_es=section.title_es,
                    title_de=section.title_de,
                    printed_page_start=section.printed_start,
                    printed_page_end=section.printed_end,
                    printed_range_status=section.printed_end_origin,
                    reference_pdf_page=section.reference_pdf_page,
                    manual_scan_layout=section.manual_scan_layout,
                    manual_region=section.manual_region,
                    outline=section.outline,
                )
            )
        return StudyPathRead(
            source_id=theory.id,
            source_version=theory.current_version,
            source_name=self._source_name(theory),
            workbook_source_id=workbook.id if workbook else None,
            workbook_source_name=self._source_name(workbook) if workbook else None,
            sections=items,
        )

    def section(self, section_id: int) -> StudySectionRead:
        path = self.path()
        for section in path.sections:
            if section.id == section_id:
                return section
        raise StudyNotFoundError("La sección de estudio no existe en la ruta actual.")

    def dashboard(self) -> StudyDashboardRead:
        path = self.path()
        active = self.db.scalar(
            select(StudySession)
            .where(StudySession.status.in_(("active", "paused")))
            .order_by((StudySession.status == "active").desc(), StudySession.updated_at.desc())
        )
        recent = self.db.scalars(
            select(StudySession).order_by(StudySession.updated_at.desc()).limit(5)
        ).all()
        preference = self._preferences(create=False)
        return StudyDashboardRead(
            path_ready=True,
            total_sections=len(path.sections),
            active_session=self._session_read(active) if active else None,
            recommendation=self.recommendation(path=path, active=active),
            open_questions=int(
                self.db.scalar(
                    select(func.count(StudyQuestion.id)).where(
                        StudyQuestion.status.in_(("open", "revisit"))
                    )
                )
                or 0
            ),
            recent_sessions=[self._session_read(item) for item in recent],
            preferences=self._preference_read(preference),
        )

    def recommendation(
        self,
        *,
        path: StudyPathRead | None = None,
        active: StudySession | None = None,
    ) -> StudyRecommendationRead:
        if active is None:
            active = self.db.scalar(
                select(StudySession)
                .where(StudySession.status.in_(("active", "paused")))
                .order_by((StudySession.status == "active").desc(), StudySession.updated_at.desc())
            )
        if active is not None:
            is_active = active.status == StudySessionStatus.ACTIVE
            return StudyRecommendationRead(
                kind="active_session" if is_active else "paused_session",
                session_id=active.id,
                section_stable_key=active.section_stable_key,
                reason=(
                    "Tienes una sesión activa y puedes continuar exactamente donde la dejaste."
                    if is_active
                    else "Te propongo continuar aquí porque dejaste esta sección a medias."
                ),
            )
        route = path or self.path()
        for wanted, reason in (
            (StudyPracticalStatus.IN_PROGRESS, "Esta sección está en curso."),
            (StudyPracticalStatus.NEEDS_REVIEW, "Esta sección está marcada para repaso."),
            (StudyPracticalStatus.PAUSED, "Esta sección quedó pausada."),
            (StudyPracticalStatus.NOT_STARTED, "Es la siguiente sección disponible en Herder."),
        ):
            for section in route.sections:
                if section.practical_status == wanted:
                    return StudyRecommendationRead(
                        kind="section", section_stable_key=section.stable_key, reason=reason
                    )
        return StudyRecommendationRead(
            kind="manual",
            reason="Puedes elegir una sección o iniciar un estudio libre.",
        )

    def start(self, request: StudySessionCreate) -> StudySessionRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            session = self.db.get(StudySession, existing.target_id)
            if session is None:
                raise StudyConflictError("La operación anterior ya no tiene un destino válido.")
            return self._session_read(session)
        if request.activate and self.db.scalar(
            select(StudySession.id).where(StudySession.status == StudySessionStatus.ACTIVE)
        ):
            raise StudyConflictError("Ya existe una sesión de estudio activa.")

        theory, _, editorial_sections = self._core()
        section: _RouteSection | None = None
        if request.kind == "guided":
            section = next(
                (item for item in editorial_sections if item.id == request.section_id), None
            )
            if section is None:
                raise StudyNotFoundError("La sección ya no pertenece al manual principal.")
            source_id = theory.id
            source_version = theory.current_version
            source_name = self._source_name(theory)
            section_title = section.title
            concept = section.topic or section.title
            page_start = section.page_start
            page_end = section.page_end
            current_page = request.current_pdf_page or section.page_start
        else:
            source_id = request.source_id
            source_version = theory.current_version if request.source_id == theory.id else None
            source_name = request.source_name
            section_title = request.section_title or "Estudio libre"
            concept = request.concept_name or section_title
            page_start = request.pdf_page_start
            page_end = request.pdf_page_end
            current_page = request.current_pdf_page or page_start
        objective = request.objective or f"Comprender y revisar: {concept}."
        preference = self._preferences()
        mission_type = resolve_mission_type(
            request.mission_type,
            preferred=StudyMissionType(preference.mission_preference),
            stable_seed=f"{section_title}:{concept}",
        )
        now = utc_now()
        section_state = None
        if section is not None:
            section_state = self._upsert_state(
                theory,
                section,
                practical_status=(
                    StudyPracticalStatus.IN_PROGRESS
                    if request.activate
                    else StudyPracticalStatus.NOT_STARTED
                ),
                current_page=current_page,
                printed_page=(
                    request.printed_page_label
                    or (str(section.printed_start) if section.printed_start else None)
                ),
                selection_origin=request.start_origin.value,
                now=now,
            )
            preference.active_source_id = theory.id
            preference.active_section_stable_key = section.stable_key
        session = StudySession(
            id=str(uuid4()),
            profile_id=1,
            section_state_id=section_state.id if section_state else None,
            kind=request.kind,
            status="active" if request.activate else "planned",
            source_id=source_id,
            source_version=source_version,
            source_name=source_name,
            section_id=section.id if section else None,
            section_stable_key=section.stable_key if section else None,
            section_title=section_title,
            concept_name=concept,
            pdf_page_start=page_start,
            pdf_page_end=page_end,
            current_pdf_page=current_page,
            printed_page_label=(
                request.printed_page_label
                or (str(section.printed_start) if section and section.printed_start else None)
            ),
            objective=objective,
            mission_type=mission_type.value,
            mission=build_mission(mission_type, concept=concept, objective=objective),
            plan=build_plan(planned_minutes=request.planned_minutes, objective=objective),
            checklist=[],
            planned_minutes=request.planned_minutes,
            active_seconds=0,
            started_at=now,
            resumed_at=now if request.activate else None,
        )
        self.db.add(session)
        self.db.flush()
        self._record_event(
            request.operation_id, "session", session.id, "start", {}, payload, result_id=session.id
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise StudyConflictError("Ya existe una sesión de estudio activa.") from exc
        return self._session_read(session)

    def get_session(self, session_id: str) -> StudySessionRead:
        session = self.db.get(StudySession, session_id)
        if session is None:
            raise StudyNotFoundError("La sesión de estudio no existe.")
        return self._session_read(session)

    def transition(self, session_id: str, request: StudyTransitionRequest) -> StudySessionRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            session = self.db.get(StudySession, existing.target_id)
            if session is None:
                raise StudyConflictError("La operación anterior ya no tiene un destino válido.")
            return self._session_read(session)
        session = self._session(session_id)
        before = self._session_snapshot(session)
        now = utc_now()
        action = request.action
        if action in {"activate", "resume"}:
            if session.status == "active":
                pass
            elif session.status not in {"planned", "paused"}:
                raise StudyConflictError("Una sesión cerrada no se puede reanudar.")
            else:
                other = self.db.scalar(
                    select(StudySession.id).where(
                        StudySession.status == "active", StudySession.id != session.id
                    )
                )
                if other:
                    raise StudyConflictError("Ya existe otra sesión de estudio activa.")
                session.status = "active"
                session.resumed_at = now
                session.paused_at = None
                self._set_linked_state(session, StudyPracticalStatus.IN_PROGRESS, now)
        elif action == "pause":
            if session.status == "paused":
                pass
            elif session.status != "active":
                raise StudyConflictError("Solo una sesión activa puede pausarse.")
            else:
                self._accumulate_active_time(session, now)
                session.status = "paused"
                session.paused_at = now
                session.resumed_at = None
                self._set_linked_state(session, StudyPracticalStatus.PAUSED, now)
        elif action in {"complete", "abandon"}:
            target = "completed" if action == "complete" else "abandoned"
            if session.status == target:
                pass
            elif session.status in {"completed", "abandoned"}:
                raise StudyConflictError("La sesión ya está cerrada con otro estado.")
            else:
                if session.status == "active":
                    self._accumulate_active_time(session, now)
                session.status = target
                session.closed_at = now
                session.resumed_at = None
                session.subjective_result = request.subjective_result
                session.final_pdf_page = request.final_pdf_page or session.current_pdf_page
                session.final_workbook_exercise = request.final_workbook_exercise
                session.next_action = request.next_action
                self._close_linked_state(session, request.subjective_result, action, now)
        else:  # pragma: no cover - Pydantic prevents it
            raise StudyContractError("La transición solicitada no existe.")
        self._record_event(
            request.operation_id,
            "session",
            session.id,
            action,
            before,
            payload,
            result_id=session.id,
        )
        self.db.commit()
        return self._session_read(session)

    def update_position(self, session_id: str, request: StudyPositionUpdate) -> StudySessionRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self.get_session(existing.target_id)
        session = self._session(session_id)
        if session.status not in {"active", "paused"}:
            raise StudyConflictError("No se puede mover la posición de una sesión cerrada.")
        if request.current_pdf_page is not None:
            if session.pdf_page_start and request.current_pdf_page < session.pdf_page_start:
                raise StudyContractError("La página queda fuera de la sección.")
            if session.pdf_page_end and request.current_pdf_page > session.pdf_page_end:
                raise StudyContractError("La página queda fuera de la sección.")
            session.current_pdf_page = request.current_pdf_page
        if request.printed_page_label is not None:
            session.printed_page_label = request.printed_page_label
        if request.checklist is not None:
            session.checklist = request.checklist
        state = (
            self.db.get(StudySectionState, session.section_state_id)
            if session.section_state_id
            else None
        )
        if state:
            state.current_pdf_page = session.current_pdf_page
            state.printed_page_label = session.printed_page_label
            state.last_activity_at = utc_now()
        self._record_event(
            request.operation_id,
            "session",
            session.id,
            "position",
            {},
            payload,
            result_id=session.id,
        )
        self.db.commit()
        return self._session_read(session)

    def update_section_state(
        self, section_id: int, request: StudySectionStateUpdate
    ) -> StudySectionRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self.section(section_id)
        theory, _, sections = self._core()
        section = next((item for item in sections if item.id == section_id), None)
        if section is None:
            raise StudyNotFoundError("La sección de estudio no existe.")
        self._upsert_state(
            theory,
            section,
            practical_status=request.practical_status,
            current_page=request.current_pdf_page,
            printed_page=None,
            selection_origin=request.selection_origin.value,
            now=utc_now(),
        )
        self._record_event(
            request.operation_id,
            "section",
            str(section_id),
            "update_state",
            {},
            payload,
            result_id=str(section_id),
        )
        self.db.commit()
        return self.section(section_id)

    def history(
        self,
        *,
        status: StudySessionStatus | None = None,
        mission_type: StudyMissionType | None = None,
        limit: int = 50,
    ) -> list[StudySessionRead]:
        statement = select(StudySession)
        if status:
            statement = statement.where(StudySession.status == status.value)
        if mission_type:
            statement = statement.where(StudySession.mission_type == mission_type.value)
        rows = self.db.scalars(
            statement.order_by(StudySession.updated_at.desc()).limit(limit)
        ).all()
        return [self._session_read(item) for item in rows]

    def data_overview(self) -> StudyDataRead:
        sessions = self.db.scalars(
            select(StudySession).order_by(StudySession.updated_at.desc())
        ).all()
        preference = self.db.get(StudyPreference, 1)
        active_session_id = self.db.scalar(
            select(StudySession.id)
            .where(StudySession.status.in_(("active", "paused")))
            .order_by((StudySession.status == "active").desc(), StudySession.updated_at.desc())
        )
        path = self.path()
        started_topics = int(
            self.db.scalar(
                select(func.count(StudySectionState.id)).where(
                    StudySectionState.practical_status != StudyPracticalStatus.NOT_STARTED
                )
            )
            or 0
        )
        return StudyDataRead(
            total_sessions=len(sessions),
            sessions=[self._session_summary(item) for item in sessions],
            memory=StudyMemoryRead(
                route_topics=len(path.sections),
                started_topics=started_topics,
                student_skills=int(self.db.scalar(select(func.count(StudentSkill.id))) or 0),
                skill_evidence=int(self.db.scalar(select(func.count(SkillEvidence.id))) or 0),
                saved_notes=int(self.db.scalar(select(func.count(StudyNote.id))) or 0),
                saved_questions=int(self.db.scalar(select(func.count(StudyQuestion.id))) or 0),
                active_session_id=active_session_id,
                preferences_persisted=preference is not None,
                mission_preference=(
                    StudyMissionType(preference.mission_preference) if preference else None
                ),
            ),
        )

    def create_note(self, request: StudyNoteWrite) -> StudyNoteRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self._note_read(self._note(existing.target_id))
        self._validate_session_reference(request.session_id)
        note = StudyNote(id=str(uuid4()), profile_id=1, **payload)
        self.db.add(note)
        self.db.flush()
        self._record_event(
            request.operation_id, "note", note.id, "create", {}, payload, result_id=note.id
        )
        self.db.commit()
        return self._note_read(note)

    def update_note(self, note_id: str, request: StudyNoteWrite) -> StudyNoteRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self._note_read(self._note(existing.target_id))
        note = self._note(note_id)
        before = self._note_read(note).model_dump(mode="json")
        self._validate_session_reference(request.session_id)
        for field, value in payload.items():
            setattr(note, field, value)
        self._record_event(
            request.operation_id, "note", note.id, "update", before, payload, result_id=note.id
        )
        self.db.commit()
        return self._note_read(note)

    def list_notes(self, *, session_id: str | None = None) -> list[StudyNoteRead]:
        statement = select(StudyNote)
        if session_id:
            statement = statement.where(StudyNote.session_id == session_id)
        rows = self.db.scalars(statement.order_by(StudyNote.updated_at.desc())).all()
        return [self._note_read(item) for item in rows]

    def delete_note(self, note_id: str, operation_id: str) -> None:
        note = self._note(note_id)
        payload = {"deleted": True}
        existing = self._idempotent(operation_id, payload)
        if existing:
            return
        self.db.delete(note)
        self._record_event(operation_id, "note", note_id, "delete", {}, payload, result_id=note_id)
        self.db.commit()

    def create_question(self, request: StudyQuestionWrite) -> StudyQuestionRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self._question_read(self._question(existing.target_id))
        self._validate_session_reference(request.session_id)
        question = StudyQuestion(id=str(uuid4()), profile_id=1, **payload)
        self.db.add(question)
        self.db.flush()
        self._record_event(
            request.operation_id,
            "question",
            question.id,
            "create",
            {},
            payload,
            result_id=question.id,
        )
        self.db.commit()
        return self._question_read(question)

    def list_questions(
        self, *, status: str | None = None, session_id: str | None = None
    ) -> list[StudyQuestionRead]:
        statement = select(StudyQuestion)
        if status:
            statement = statement.where(StudyQuestion.status == status)
        if session_id:
            statement = statement.where(StudyQuestion.session_id == session_id)
        rows = self.db.scalars(statement.order_by(StudyQuestion.updated_at.desc())).all()
        return [self._question_read(item) for item in rows]

    def update_question(
        self, question_id: str, request: StudyQuestionStatusUpdate
    ) -> StudyQuestionRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self._question_read(self._question(existing.target_id))
        question = self._question(question_id)
        before = self._question_read(question).model_dump(mode="json")
        question.status = request.status
        question.answer_query_id = request.answer_query_id
        self._record_event(
            request.operation_id,
            "question",
            question.id,
            "update",
            before,
            payload,
            result_id=question.id,
        )
        self.db.commit()
        return self._question_read(question)

    def delete_question(self, question_id: str, operation_id: str) -> None:
        question = self._question(question_id)
        payload = {"deleted": True}
        existing = self._idempotent(operation_id, payload)
        if existing:
            return
        self.db.delete(question)
        self._record_event(
            operation_id, "question", question_id, "delete", {}, payload, result_id=question_id
        )
        self.db.commit()

    def create_workbook_link(self, request: WorkbookLinkCreate) -> WorkbookLinkRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self._workbook_read(self._workbook(existing.target_id))
        theory, workbook, sections = self._core()
        if workbook is None:
            raise StudyLibraryUnavailableError("El workbook principal no está configurado.")
        if request.theory_source_id != theory.id or not any(
            item.stable_key == request.theory_section_stable_key for item in sections
        ):
            raise StudyContractError("La relación no apunta a una sección Herder vigente.")
        duplicate = self.db.scalar(
            select(StudyWorkbookLink).where(
                StudyWorkbookLink.theory_source_id == theory.id,
                StudyWorkbookLink.section_stable_key == request.theory_section_stable_key,
                StudyWorkbookLink.workbook_pdf_page == request.workbook_pdf_page,
                StudyWorkbookLink.exercise_start == request.exercise_start,
                StudyWorkbookLink.exercise_end == request.exercise_end,
            )
        )
        if duplicate:
            raise StudyConflictError("Esta relación de práctica ya existe.")
        link = StudyWorkbookLink(
            id=str(uuid4()),
            profile_id=1,
            theory_source_id=theory.id,
            theory_source_version=theory.current_version,
            section_stable_key=request.theory_section_stable_key,
            workbook_source_id=workbook.id,
            workbook_source_version=workbook.current_version,
            workbook_pdf_page=request.workbook_pdf_page,
            workbook_printed_page=request.printed_page_label,
            exercise_start=request.exercise_start,
            exercise_end=request.exercise_end,
            region=request.region,
            comment=request.comment,
            status="candidate",
        )
        self.db.add(link)
        self.db.flush()
        self._record_event(
            request.operation_id, "workbook_link", link.id, "create", {}, payload, result_id=link.id
        )
        self.db.commit()
        return self._workbook_read(link)

    def list_workbook_links(
        self, *, section_stable_key: str | None = None
    ) -> list[WorkbookLinkRead]:
        statement = select(StudyWorkbookLink)
        if section_stable_key:
            statement = statement.where(StudyWorkbookLink.section_stable_key == section_stable_key)
        rows = self.db.scalars(statement.order_by(StudyWorkbookLink.updated_at.desc())).all()
        return [self._workbook_read(item) for item in rows]

    def review_workbook_link(self, link_id: str, request: WorkbookLinkReview) -> WorkbookLinkRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self._workbook_read(self._workbook(existing.target_id))
        link = self._workbook(link_id)
        before = self._workbook_read(link).model_dump(mode="json")
        reverts_event_id = None
        if request.action == "confirm":
            link.status = "user_confirmed"
            link.reviewed_at = utc_now()
        elif request.action == "reject":
            link.status = "rejected"
            link.reviewed_at = utc_now()
        elif request.action == "unknown":
            pass
        elif request.action == "revert":
            previous = self.db.scalar(
                select(StudyEvent)
                .where(
                    StudyEvent.target_type == "workbook_link",
                    StudyEvent.target_id == link.id,
                    StudyEvent.action.in_(("confirm", "reject")),
                )
                .order_by(StudyEvent.id.desc())
            )
            if previous is None:
                raise StudyConflictError("No existe una revisión que se pueda revertir.")
            link.status = str(previous.before_state.get("status", "candidate"))
            reviewed = previous.before_state.get("reviewed_at")
            link.reviewed_at = datetime.fromisoformat(str(reviewed)) if reviewed else None
            reverts_event_id = previous.id
        self._record_event(
            request.operation_id,
            "workbook_link",
            link.id,
            request.action,
            before,
            payload,
            result_id=link.id,
            reverts_event_id=reverts_event_id,
        )
        self.db.commit()
        return self._workbook_read(link)

    def update_workbook_link(self, link_id: str, request: WorkbookLinkUpdate) -> WorkbookLinkRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self._workbook_read(self._workbook(existing.target_id))
        link = self._workbook(link_id)
        before = self._workbook_read(link).model_dump(mode="json")
        link.workbook_pdf_page = request.workbook_pdf_page
        link.workbook_printed_page = request.printed_page_label
        link.exercise_start = request.exercise_start
        link.exercise_end = request.exercise_end
        link.region = request.region
        link.comment = request.comment
        link.status = "candidate"
        link.reviewed_at = None
        self._record_event(
            request.operation_id,
            "workbook_link",
            link.id,
            "correct",
            before,
            payload,
            result_id=link.id,
        )
        self.db.commit()
        return self._workbook_read(link)

    def preferences(self) -> StudyPreferenceRead:
        preference = self._preferences()
        self.db.commit()
        return self._preference_read(preference)

    def update_preferences(self, request: StudyPreferenceUpdate) -> StudyPreferenceRead:
        payload = request.model_dump(mode="json", exclude={"operation_id"})
        existing = self._idempotent(request.operation_id, payload)
        if existing:
            return self.preferences()
        preference = self._preferences()
        before = self._preference_read(preference).model_dump(mode="json")
        preference.mission_preference = request.mission_preference.value
        self._record_event(
            request.operation_id,
            "preference",
            "1",
            "update",
            before,
            payload,
            result_id="1",
        )
        self.db.commit()
        return self._preference_read(preference)

    def delete_session(self, session_id: str, operation_id: str) -> StudySessionDeleteRead:
        payload = {"session_ids": [session_id]}
        existing = self._idempotent(operation_id, payload)
        if existing:
            return StudySessionDeleteRead(deleted_sessions=1)
        self._session(session_id)
        try:
            self.db.execute(delete(StudySession).where(StudySession.id == session_id))
            self._record_event(
                operation_id,
                "study_sessions",
                session_id,
                "delete",
                {},
                payload,
                result_id=session_id,
                result={"deleted_sessions": 1},
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return StudySessionDeleteRead(deleted_sessions=1)

    def delete_sessions(self, session_ids: list[str], operation_id: str) -> StudySessionDeleteRead:
        payload = {"session_ids": session_ids}
        existing = self._idempotent(operation_id, payload)
        if existing:
            values = existing.after_state.get("result", {})
            return StudySessionDeleteRead.model_validate(values)
        found = set(
            self.db.scalars(select(StudySession.id).where(StudySession.id.in_(session_ids))).all()
        )
        missing = [session_id for session_id in session_ids if session_id not in found]
        if missing:
            raise StudyNotFoundError("Una o más sesiones de estudio no existen.")
        result = StudySessionDeleteRead(deleted_sessions=len(session_ids))
        try:
            self.db.execute(delete(StudySession).where(StudySession.id.in_(session_ids)))
            self._record_event(
                operation_id,
                "study_sessions",
                "selection",
                "delete_many",
                {},
                payload,
                result_id="selection",
                result=result.model_dump(mode="json"),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return result

    def clear_sessions(self, operation_id: str) -> StudySessionDeleteRead:
        payload = {"clear_sessions": True}
        existing = self._idempotent(operation_id, payload)
        if existing:
            values = existing.after_state.get("result", {})
            return StudySessionDeleteRead.model_validate(values)
        result = StudySessionDeleteRead(
            deleted_sessions=int(self.db.scalar(select(func.count(StudySession.id))) or 0)
        )
        try:
            self.db.execute(delete(StudySession))
            self._record_event(
                operation_id,
                "study_sessions",
                "all",
                "clear",
                {},
                payload,
                result_id="all",
                result=result.model_dump(mode="json"),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return result

    def _idempotent(self, operation_id: str, request: dict[str, object]) -> StudyEvent | None:
        event = self.db.scalar(select(StudyEvent).where(StudyEvent.operation_id == operation_id))
        if event is None:
            return None
        if event.after_state.get("request") != request:
            raise StudyConflictError(
                "El identificador de operación ya se utilizó con otro contenido."
            )
        return event

    def _record_event(
        self,
        operation_id: str,
        target_type: str,
        target_id: str,
        action: str,
        before: dict[str, object],
        request: dict[str, object],
        *,
        result_id: str,
        result: dict[str, object] | None = None,
        reverts_event_id: int | None = None,
    ) -> None:
        self.db.add(
            StudyEvent(
                profile_id=1,
                operation_id=operation_id,
                target_type=target_type,
                target_id=result_id or target_id,
                action=action,
                before_state=before,
                after_state={"request": request, "result": result or {"id": result_id}},
                reverts_event_id=reverts_event_id,
            )
        )

    def _upsert_state(
        self,
        theory: SourceRead,
        section: _RouteSection,
        *,
        practical_status: StudyPracticalStatus,
        current_page: int | None,
        printed_page: str | None,
        selection_origin: str,
        now: datetime,
    ) -> StudySectionState:
        state = self.db.scalar(
            select(StudySectionState).where(
                StudySectionState.source_id == theory.id,
                StudySectionState.section_stable_key == section.stable_key,
            )
        )
        if state is None:
            state = StudySectionState(
                profile_id=1,
                source_id=theory.id,
                source_version=theory.current_version,
                section_id=section.id,
                section_stable_key=section.stable_key,
                section_title=section.title,
                practical_status=practical_status.value,
                current_pdf_page=current_page,
                printed_page_label=printed_page,
                selection_origin=selection_origin,
                last_activity_at=now,
            )
            self.db.add(state)
            self.db.flush()
        else:
            state.source_version = theory.current_version
            state.section_id = section.id
            state.section_title = section.title
            state.practical_status = practical_status.value
            if current_page is not None:
                state.current_pdf_page = current_page
            if printed_page is not None:
                state.printed_page_label = printed_page
            state.selection_origin = selection_origin
            state.last_activity_at = now
        return state

    def _set_linked_state(
        self, session: StudySession, status: StudyPracticalStatus, now: datetime
    ) -> None:
        if session.section_state_id:
            state = self.db.get(StudySectionState, session.section_state_id)
            if state:
                state.practical_status = status.value
                state.current_pdf_page = session.current_pdf_page
                state.last_activity_at = now

    def _close_linked_state(
        self,
        session: StudySession,
        result: str | None,
        action: str,
        now: datetime,
    ) -> None:
        if action == "abandon":
            status = StudyPracticalStatus.PAUSED
        else:
            mapping = {
                "understood": StudyPracticalStatus.COMPLETED_BY_USER,
                "needs_review": StudyPracticalStatus.NEEDS_REVIEW,
                "difficult": StudyPracticalStatus.NEEDS_REVIEW,
                "unfinished": StudyPracticalStatus.PAUSED,
                "continue_next_time": StudyPracticalStatus.PAUSED,
                "change_topic": StudyPracticalStatus.VIEWED,
            }
            status = mapping.get(result, StudyPracticalStatus.VIEWED)
        self._set_linked_state(session, status, now)

    @staticmethod
    def _accumulate_active_time(session: StudySession, now: datetime) -> None:
        anchor = session.resumed_at or session.started_at
        if anchor:
            session.active_seconds += max(0, int((now - anchor).total_seconds()))

    def _session(self, session_id: str) -> StudySession:
        session = self.db.get(StudySession, session_id)
        if session is None:
            raise StudyNotFoundError("La sesión de estudio no existe.")
        return session

    def _note(self, note_id: str) -> StudyNote:
        note = self.db.get(StudyNote, note_id)
        if note is None:
            raise StudyNotFoundError("La nota no existe.")
        return note

    def _question(self, question_id: str) -> StudyQuestion:
        question = self.db.get(StudyQuestion, question_id)
        if question is None:
            raise StudyNotFoundError("La duda no existe.")
        return question

    def _workbook(self, link_id: str) -> StudyWorkbookLink:
        link = self.db.get(StudyWorkbookLink, link_id)
        if link is None:
            raise StudyNotFoundError("La relación con el workbook no existe.")
        return link

    def _validate_session_reference(self, session_id: str | None) -> None:
        if session_id and self.db.get(StudySession, session_id) is None:
            raise StudyNotFoundError("La sesión de estudio no existe.")

    @staticmethod
    def _session_snapshot(session: StudySession) -> dict[str, object]:
        return {
            "status": session.status,
            "current_pdf_page": session.current_pdf_page,
            "active_seconds": session.active_seconds,
            "subjective_result": session.subjective_result,
        }

    def _session_read(self, session: StudySession) -> StudySessionRead:
        canonical = (
            None
            if not session.section_stable_key or session.section_stable_key.startswith("herder:")
            else self.canonical_route.resolve_legacy_exact(session.section_id)
        )
        return StudySessionRead(
            id=session.id,
            kind=session.kind,
            status=session.status,
            source_id=session.source_id,
            source_version=session.source_version,
            source_name=session.source_name,
            section_id=session.section_id,
            section_stable_key=session.section_stable_key,
            section_title=canonical.title_es if canonical else session.section_title,
            concept_name=(
                f"Tema {canonical.theme_number} · {canonical.title_es}"
                if canonical
                else session.concept_name
            ),
            pdf_page_start=session.pdf_page_start,
            pdf_page_end=session.pdf_page_end,
            current_pdf_page=session.current_pdf_page,
            printed_page_label=session.printed_page_label,
            objective=session.objective,
            mission=StudyMissionRead.model_validate(session.mission),
            plan=session.plan,
            checklist=session.checklist,
            planned_minutes=session.planned_minutes,
            active_seconds=session.active_seconds,
            started_at=session.started_at,
            paused_at=session.paused_at,
            resumed_at=session.resumed_at,
            closed_at=session.closed_at,
            subjective_result=session.subjective_result,
            final_pdf_page=session.final_pdf_page,
            final_workbook_exercise=session.final_workbook_exercise,
            next_action=session.next_action,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @staticmethod
    def _session_summary(session: StudySession) -> StudySessionSummaryRead:
        mission = session.mission if isinstance(session.mission, dict) else {}
        label = mission.get("label")
        return StudySessionSummaryRead(
            id=session.id,
            status=session.status,
            section_title=session.section_title,
            concept_name=session.concept_name,
            mission_label=label if isinstance(label, str) else None,
            active_seconds=session.active_seconds,
            started_at=session.started_at,
            updated_at=session.updated_at,
        )

    @staticmethod
    def _note_read(note: StudyNote) -> StudyNoteRead:
        return StudyNoteRead.model_validate(note, from_attributes=True)

    @staticmethod
    def _question_read(question: StudyQuestion) -> StudyQuestionRead:
        return StudyQuestionRead.model_validate(question, from_attributes=True)

    @staticmethod
    def _workbook_read(link: StudyWorkbookLink) -> WorkbookLinkRead:
        return WorkbookLinkRead(
            id=link.id,
            theory_source_id=link.theory_source_id,
            theory_section_stable_key=link.section_stable_key,
            workbook_source_id=link.workbook_source_id,
            workbook_source_version=link.workbook_source_version,
            workbook_pdf_page=link.workbook_pdf_page,
            printed_page_label=link.workbook_printed_page,
            exercise_start=link.exercise_start,
            exercise_end=link.exercise_end,
            region=link.region,
            comment=link.comment,
            status=link.status,
            reviewed_at=link.reviewed_at,
            created_at=link.created_at,
            updated_at=link.updated_at,
        )

    @staticmethod
    def _preference_read(preference: StudyPreference) -> StudyPreferenceRead:
        return StudyPreferenceRead(
            mission_preference=preference.mission_preference,
            active_source_id=preference.active_source_id,
            active_section_stable_key=preference.active_section_stable_key,
        )

    def status_distribution(self) -> Counter[str]:
        return Counter(self.db.scalars(select(StudySectionState.practical_status)).all())
