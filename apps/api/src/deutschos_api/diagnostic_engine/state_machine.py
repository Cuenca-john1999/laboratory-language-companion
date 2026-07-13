"""Explicit deterministic state machines for sessions and presented tasks."""

from __future__ import annotations

from dataclasses import dataclass

from deutschos_api.diagnostic_engine.exceptions import InvalidTransitionError
from deutschos_api.diagnostic_engine.schemas import (
    EngineSessionState,
    SessionAction,
    TaskAction,
)
from deutschos_api.models import DiagnosticSessionStatus, DiagnosticTaskStatus

ACTIVE_SESSION_STATUSES = frozenset(
    {
        DiagnosticSessionStatus.ONBOARDING,
        DiagnosticSessionStatus.CALIBRATING,
        DiagnosticSessionStatus.ASSESSING,
        DiagnosticSessionStatus.REVIEWING,
        DiagnosticSessionStatus.TIME_LIMITED,
        DiagnosticSessionStatus.COMPLETING,
    }
)
PAUSABLE_SESSION_STATUSES = frozenset(
    {
        DiagnosticSessionStatus.ONBOARDING,
        DiagnosticSessionStatus.CALIBRATING,
        DiagnosticSessionStatus.ASSESSING,
        DiagnosticSessionStatus.REVIEWING,
    }
)
TERMINAL_SESSION_STATUSES = frozenset(
    {
        DiagnosticSessionStatus.COMPLETED,
        DiagnosticSessionStatus.CANCELLED,
        DiagnosticSessionStatus.ABANDONED,
    }
)


@dataclass(frozen=True, slots=True)
class StateTransition:
    target: DiagnosticSessionStatus
    changed: bool


@dataclass(frozen=True, slots=True)
class TaskTransition:
    target: DiagnosticTaskStatus
    changed: bool


def engine_state(status: DiagnosticSessionStatus | str) -> EngineSessionState:
    status = DiagnosticSessionStatus(status)
    if status == DiagnosticSessionStatus.NOT_STARTED:
        return EngineSessionState.CREATED
    if status in ACTIVE_SESSION_STATUSES:
        return EngineSessionState.ACTIVE
    if status == DiagnosticSessionStatus.PAUSED:
        return EngineSessionState.PAUSED
    if status == DiagnosticSessionStatus.COMPLETED:
        return EngineSessionState.COMPLETED
    if status in {DiagnosticSessionStatus.CANCELLED, DiagnosticSessionStatus.ABANDONED}:
        return EngineSessionState.ABANDONED
    return EngineSessionState.FAILED


def session_transition(
    current: DiagnosticSessionStatus | str,
    action: SessionAction,
    *,
    paused_from: DiagnosticSessionStatus | str | None = None,
) -> StateTransition:
    """Return the only valid target, including documented idempotent repeats."""

    current = DiagnosticSessionStatus(current)
    if action == SessionAction.START:
        if current == DiagnosticSessionStatus.NOT_STARTED:
            return StateTransition(DiagnosticSessionStatus.ONBOARDING, True)
        if current in ACTIVE_SESSION_STATUSES:
            return StateTransition(current, False)
    elif action == SessionAction.BEGIN_CALIBRATION:
        if current == DiagnosticSessionStatus.ONBOARDING:
            return StateTransition(DiagnosticSessionStatus.CALIBRATING, True)
        if current == DiagnosticSessionStatus.CALIBRATING:
            return StateTransition(current, False)
    elif action == SessionAction.BEGIN_ASSESSMENT:
        if current == DiagnosticSessionStatus.CALIBRATING:
            return StateTransition(DiagnosticSessionStatus.ASSESSING, True)
        if current == DiagnosticSessionStatus.ASSESSING:
            return StateTransition(current, False)
    elif action == SessionAction.BEGIN_REVIEW:
        if current == DiagnosticSessionStatus.ASSESSING:
            return StateTransition(DiagnosticSessionStatus.REVIEWING, True)
        if current == DiagnosticSessionStatus.REVIEWING:
            return StateTransition(current, False)
    elif action == SessionAction.CONTINUE_ASSESSMENT:
        if current == DiagnosticSessionStatus.REVIEWING:
            return StateTransition(DiagnosticSessionStatus.ASSESSING, True)
        if current == DiagnosticSessionStatus.ASSESSING:
            return StateTransition(current, False)
    elif action == SessionAction.REACH_TIME_LIMIT:
        if current in {
            DiagnosticSessionStatus.ASSESSING,
            DiagnosticSessionStatus.REVIEWING,
        }:
            return StateTransition(DiagnosticSessionStatus.TIME_LIMITED, True)
        if current == DiagnosticSessionStatus.TIME_LIMITED:
            return StateTransition(current, False)
    elif action == SessionAction.BEGIN_COMPLETION:
        if current in {
            DiagnosticSessionStatus.REVIEWING,
            DiagnosticSessionStatus.TIME_LIMITED,
        }:
            return StateTransition(DiagnosticSessionStatus.COMPLETING, True)
        if current == DiagnosticSessionStatus.COMPLETING:
            return StateTransition(current, False)
    elif action == SessionAction.PAUSE:
        if current in PAUSABLE_SESSION_STATUSES:
            return StateTransition(DiagnosticSessionStatus.PAUSED, True)
        if current == DiagnosticSessionStatus.PAUSED:
            return StateTransition(current, False)
    elif action == SessionAction.RESUME:
        if current in ACTIVE_SESSION_STATUSES:
            return StateTransition(current, False)
        if current == DiagnosticSessionStatus.PAUSED:
            target = DiagnosticSessionStatus(paused_from or DiagnosticSessionStatus.ASSESSING)
            if target not in ACTIVE_SESSION_STATUSES - {
                DiagnosticSessionStatus.TIME_LIMITED,
                DiagnosticSessionStatus.COMPLETING,
            }:
                raise InvalidTransitionError("La fase previa pausada no es reanudable.")
            return StateTransition(target, True)
        if current == DiagnosticSessionStatus.ERROR and paused_from is not None:
            target = DiagnosticSessionStatus(paused_from)
            if target in ACTIVE_SESSION_STATUSES - {
                DiagnosticSessionStatus.TIME_LIMITED,
                DiagnosticSessionStatus.COMPLETING,
            }:
                return StateTransition(target, True)
    elif action == SessionAction.ABANDON:
        if current in {DiagnosticSessionStatus.CANCELLED, DiagnosticSessionStatus.ABANDONED}:
            return StateTransition(current, False)
        if current == DiagnosticSessionStatus.NOT_STARTED:
            return StateTransition(DiagnosticSessionStatus.CANCELLED, True)
        if current not in TERMINAL_SESSION_STATUSES:
            return StateTransition(DiagnosticSessionStatus.ABANDONED, True)
    elif action == SessionAction.COMPLETE:
        if current == DiagnosticSessionStatus.COMPLETED:
            return StateTransition(current, False)
        if current in {
            DiagnosticSessionStatus.COMPLETING,
            DiagnosticSessionStatus.REVIEWING,
            DiagnosticSessionStatus.TIME_LIMITED,
        }:
            return StateTransition(DiagnosticSessionStatus.COMPLETED, True)
    elif action == SessionAction.FAIL:
        if current == DiagnosticSessionStatus.ERROR:
            return StateTransition(current, False)
        if current not in TERMINAL_SESSION_STATUSES:
            return StateTransition(DiagnosticSessionStatus.ERROR, True)

    raise InvalidTransitionError(f"Transición inválida: {current.value} + {action.value}.")


_TASK_TARGETS: dict[tuple[DiagnosticTaskStatus, TaskAction], DiagnosticTaskStatus] = {
    (DiagnosticTaskStatus.SELECTED, TaskAction.PRESENT): DiagnosticTaskStatus.PRESENTED,
    (DiagnosticTaskStatus.SELECTED, TaskAction.INVALIDATE): DiagnosticTaskStatus.INVALIDATED,
    (DiagnosticTaskStatus.PRESENTED, TaskAction.ANSWER): DiagnosticTaskStatus.ANSWERED,
    (DiagnosticTaskStatus.PRESENTED, TaskAction.SKIP): DiagnosticTaskStatus.SKIPPED,
    (
        DiagnosticTaskStatus.PRESENTED,
        TaskAction.MARK_NOT_UNDERSTOOD,
    ): DiagnosticTaskStatus.NOT_UNDERSTOOD,
    (DiagnosticTaskStatus.PRESENTED, TaskAction.ABANDON): DiagnosticTaskStatus.ABANDONED,
    (DiagnosticTaskStatus.PRESENTED, TaskAction.INVALIDATE): DiagnosticTaskStatus.INVALIDATED,
    (DiagnosticTaskStatus.NOT_UNDERSTOOD, TaskAction.RETRY): DiagnosticTaskStatus.PRESENTED,
    (DiagnosticTaskStatus.NOT_UNDERSTOOD, TaskAction.SKIP): DiagnosticTaskStatus.SKIPPED,
    (DiagnosticTaskStatus.NOT_UNDERSTOOD, TaskAction.ABANDON): DiagnosticTaskStatus.ABANDONED,
    (DiagnosticTaskStatus.NOT_UNDERSTOOD, TaskAction.INVALIDATE): DiagnosticTaskStatus.INVALIDATED,
    (DiagnosticTaskStatus.ANSWERED, TaskAction.EVALUATE): DiagnosticTaskStatus.EVALUATED,
    (DiagnosticTaskStatus.EVALUATED, TaskAction.RETRY): DiagnosticTaskStatus.PRESENTED,
}


def task_transition(
    current: DiagnosticTaskStatus | str,
    action: TaskAction,
) -> TaskTransition:
    current = DiagnosticTaskStatus(current)
    target = _TASK_TARGETS.get((current, action))
    if target is not None:
        return TaskTransition(target, target != current)

    idempotent_actions = {
        DiagnosticTaskStatus.PRESENTED: TaskAction.PRESENT,
        DiagnosticTaskStatus.SKIPPED: TaskAction.SKIP,
        DiagnosticTaskStatus.NOT_UNDERSTOOD: TaskAction.MARK_NOT_UNDERSTOOD,
        DiagnosticTaskStatus.ABANDONED: TaskAction.ABANDON,
        DiagnosticTaskStatus.EVALUATED: TaskAction.EVALUATE,
        DiagnosticTaskStatus.INVALIDATED: TaskAction.INVALIDATE,
    }
    if idempotent_actions.get(current) == action:
        return TaskTransition(current, False)
    raise InvalidTransitionError(f"Transición de tarea inválida: {current.value} + {action.value}.")


__all__ = [
    "ACTIVE_SESSION_STATUSES",
    "PAUSABLE_SESSION_STATUSES",
    "StateTransition",
    "TaskTransition",
    "engine_state",
    "session_transition",
    "task_transition",
]
