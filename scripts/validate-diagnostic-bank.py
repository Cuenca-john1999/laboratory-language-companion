#!/usr/bin/env python3
"""Validate local diagnostic-bank manifests and print an editorial summary."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = PROJECT_ROOT / "apps" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from deutschos_api.content import (  # noqa: E402
    DiagnosticContentError,
    EditorialStatus,
    analyze_diagnostic_bank,
    load_diagnostic_banks,
)
from deutschos_api.core.config import get_settings  # noqa: E402
from deutschos_api.learning_engine.curriculum import CURRICULUM_VERSION  # noqa: E402


def curriculum_skill_ids() -> set[int]:
    database_path = get_settings().database_path
    if database_path is None or not database_path.is_file():
        return set()
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            rows = connection.execute(
                "SELECT skill_id FROM curriculum_skills WHERE curriculum_version = ?",
                (CURRICULUM_VERSION,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise DiagnosticContentError(
            "No se pudo leer el currículo local para contrastar skill_id."
        ) from exc
    return {int(row[0]) for row in rows}


def _values(values: list[str]) -> str:
    return ", ".join(values) if values else "ninguno"


def _print_reachability(report) -> None:
    print("Alcanzabilidad:")
    print(f"  Banco: {report.bank_id}@{report.bank_version}")
    print(f"  Preparado para reviewed: {'sí' if report.ready_for_review else 'no'}")
    print(
        "  Tareas alcanzables: "
        f"{report.potentially_reachable_task_count}/{report.total_task_count}"
    )
    maximum_tasks = report.maximum_observed_tasks_per_trajectory
    print(
        "  Máximo desde sesión vacía: "
        + (str(maximum_tasks) if maximum_tasks is not None else "inconcluso")
    )
    print(
        "  Máximo de evidencias evaluables: "
        f"{report.maximum_evaluable_evidence_observed}"
    )
    reachable_axes = [
        item.axis.value
        for item in report.axis_coverage
        if item.reachable_task_count > 0
    ]
    print(f"  Ejes alcanzables: {_values(reachable_axes)}")
    print(
        "  Ejes sin candidatos: "
        f"{_values([axis.value for axis in report.missing_candidate_axes])}"
    )
    print(
        "  Ejes sin entrada: "
        f"{_values([axis.value for axis in report.axes_without_entry_candidate])}"
    )
    print(
        "  Tareas permanentemente inalcanzables: "
        f"{_values(report.permanently_unreachable_tasks)}"
    )
    exploration = report.exploration
    print(
        "  Exploración: "
        f"{'conclusiva' if exploration.conclusive else 'inconclusa'}; "
        f"estados={exploration.states_explored}; límite={exploration.state_limit}"
    )
    if report.problems:
        print("  Problemas:")
        for issue in report.problems:
            subject = issue.task_id or (
                issue.axis.value if issue.axis is not None else report.bank_id
            )
            print(
                f"    - [{issue.severity.value}] {issue.code.value} ({subject}): {issue.message}"
            )
    if report.warnings:
        print("  Advertencias:")
        for issue in report.warnings:
            subject = issue.task_id or (
                issue.axis.value if issue.axis is not None else report.bank_id
            )
            print(f"    - [{issue.code.value}] {subject}: {issue.message}")


def validate(directory: Path, *, require_ready: bool = False) -> int:
    try:
        banks = load_diagnostic_banks(
            directory,
            curriculum_skill_ids={CURRICULUM_VERSION: curriculum_skill_ids()},
        )
    except DiagnosticContentError as exc:
        print(f"Banco diagnóstico inválido: {exc}", file=sys.stderr)
        return 1

    tasks = [task for bank in banks for task in bank.tasks]
    statuses = Counter(bank.manifest.editorial_status.value for bank in banks)
    difficulties = Counter(task.difficulty for task in tasks)
    task_types = Counter(task.task_type.value for task in tasks)
    print("Banco diagnóstico válido")
    print(f"Ruta: {directory.resolve()}")
    print(f"Bancos: {len(banks)}")
    print(f"Tareas: {len(tasks)}")
    print(
        "Estado editorial: "
        + _values([f"{status}={count}" for status, count in sorted(statuses.items())])
    )
    axes = {axis.value for task in tasks for axis in (task.axis, *task.secondary_axes)}
    print(f"Ejes: {_values(sorted(axes))}")
    print(
        "Habilidades: "
        + _values(
            [
                str(skill_id)
                for skill_id in sorted(
                    {task.skill_id for task in tasks if task.skill_id}
                )
            ]
        )
    )
    print(
        "Dificultad: "
        + _values(
            [
                f"{difficulty}={count}"
                for difficulty, count in sorted(difficulties.items())
            ]
        )
    )
    print(
        "Tipos: "
        + _values(
            [f"{task_type}={count}" for task_type, count in sorted(task_types.items())]
        )
    )
    reports = [analyze_diagnostic_bank(bank) for bank in banks]
    for report in reports:
        _print_reachability(report)

    if require_ready and not reports:
        print("No existe ningún banco que pueda declararse preparado.", file=sys.stderr)
        return 1
    blocked = any(
        not report.ready_for_review
        and (
            require_ready
            or report.editorial_status
            in {EditorialStatus.REVIEWED, EditorialStatus.PRODUCTION}
        )
        for report in reports
    )
    return 1 if blocked else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=PROJECT_ROOT / "data" / "diagnostic",
        help="Directorio del banco; por defecto data/diagnostic.",
    )
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Devuelve código no cero si algún banco no está preparado para reviewed.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return validate(args.path.expanduser(), require_ready=args.require_ready)


if __name__ == "__main__":
    raise SystemExit(main())
