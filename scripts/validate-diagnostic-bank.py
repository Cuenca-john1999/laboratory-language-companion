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

from deutschos_api.content import DiagnosticContentError, load_diagnostic_banks  # noqa: E402
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


def validate(directory: Path) -> int:
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
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=PROJECT_ROOT / "data" / "diagnostic",
        help="Directorio del banco; por defecto data/diagnostic.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return validate(args.path.expanduser())


if __name__ == "__main__":
    raise SystemExit(main())
