#!/usr/bin/env python3
"""Create an atomic, private DeutschOS backup bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from local_config import (
    PUBLIC_KEYS,
    ConfigurationError,
    database_path,
    get_config_value,
    is_loopback_url,
    read_dotenv,
)

EXCLUDED_DIRECTORY_NAMES = {
    ".cache",
    "backups",
    "cache",
    "hf-cache",
    "huggingface",
    "model",
    "models",
    "lm_studio",
    "temp",
    "tmp",
}
MODEL_EXTENSIONS = {
    ".bin",
    ".ckpt",
    ".ggml",
    ".gguf",
    ".h5",
    ".mlx",
    ".onnx",
    ".pb",
    ".pt",
    ".pth",
    ".safetensors",
    ".tflite",
}
DATABASE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class BackupError(RuntimeError):
    """Raised when a backup cannot be completed safely."""


def is_safe_public_configuration(key: str, value: str) -> bool:
    """Reject credential-bearing URLs even when their key is otherwise public."""

    if key in {"DEUTSCHOS_LM_STUDIO_BASE_URL", "NEXT_PUBLIC_API_URL"}:
        return is_loopback_url(value)
    if key == "DEUTSCHOS_CORS_ORIGINS":
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        return bool(origins) and all(is_loopback_url(origin) for origin in origins)
    return True


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quick_check(connection: sqlite3.Connection, source_name: str) -> None:
    rows = [row[0] for row in connection.execute("PRAGMA quick_check")]
    if rows != ["ok"]:
        details = "; ".join(str(row) for row in rows[:5])
        raise BackupError(f"La comprobación SQLite falló para {source_name}: {details}")


def backup_database(source_path: Path, destination_path: Path) -> str | None:
    if not source_path.is_file():
        raise BackupError(f"No existe la base de datos configurada: {source_path}")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    revision: str | None = None
    try:
        with sqlite3.connect(source_uri, uri=True, timeout=30) as source:
            source.execute("PRAGMA busy_timeout = 30000")
            quick_check(source, "la base original")
            try:
                row = source.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()
                revision = str(row[0]) if row else None
            except sqlite3.OperationalError:
                revision = None

            with sqlite3.connect(destination_path, timeout=30) as destination:
                source.backup(destination, pages=256, sleep=0.05)
                destination.commit()
                quick_check(destination, "la copia")
    except sqlite3.Error as exc:
        raise BackupError(f"SQLite no pudo crear una copia consistente: {exc}") from exc

    destination_path.chmod(0o600)
    return revision


def should_exclude_file(path: Path, database: Path) -> bool:
    if path == database:
        return True
    if path.name in {
        f"{database.name}-journal",
        f"{database.name}-shm",
        f"{database.name}-wal",
    }:
        return True
    suffix = path.suffix.lower()
    return (
        suffix in MODEL_EXTENSIONS
        or suffix in DATABASE_EXTENSIONS
        or path.name == ".gitkeep"
    )


def should_exclude_directory(name: str) -> bool:
    lowered = name.lower()
    return lowered in EXCLUDED_DIRECTORY_NAMES or lowered.startswith(
        ("huggingface-", "model-", "models-", "lm_studio-")
    )


def copy_data_tree(
    data_root: Path, destination_root: Path, database: Path
) -> tuple[list[str], list[str]]:
    copied: list[str] = []
    excluded: list[str] = []
    if not data_root.is_dir():
        return copied, excluded

    for current_root, directory_names, file_names in os.walk(
        data_root, followlinks=False
    ):
        current = Path(current_root)
        retained_directories: list[str] = []
        for name in directory_names:
            candidate = current / name
            relative = candidate.relative_to(data_root).as_posix()
            if candidate.is_symlink() or should_exclude_directory(name):
                excluded.append(relative + "/")
            else:
                retained_directories.append(name)
        directory_names[:] = retained_directories

        for name in file_names:
            source = current / name
            relative_path = source.relative_to(data_root)
            relative = relative_path.as_posix()
            if source.is_symlink() or should_exclude_file(
                source.resolve(strict=False), database
            ):
                excluded.append(relative)
                continue
            if not source.is_file():
                excluded.append(relative)
                continue
            destination = destination_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)
            destination.chmod(0o600)
            copied.append(relative)
    return copied, excluded


def copy_local_configuration(root: Path, staging: Path) -> tuple[list[str], list[str]]:
    """Copy only explicitly public configuration keys, never the raw dotenv file."""

    copied: list[str] = []
    excluded_keys: list[str] = []
    dotenv = root / ".env"
    if dotenv.is_file() and not dotenv.is_symlink():
        values = read_dotenv(dotenv)
        safe_values = {
            key: value
            for key, value in values.items()
            if key in PUBLIC_KEYS and is_safe_public_configuration(key, value)
        }
        excluded_keys = sorted(set(values) - set(safe_values))
        if safe_values:
            destination = staging / "configuration" / ".env"
            destination.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "# Copia saneada: solo configuración local no secreta permitida.",
                *(
                    f"{key}={json.dumps(value, ensure_ascii=False)}"
                    for key, value in sorted(safe_values.items())
                ),
                "",
            ]
            destination.write_text("\n".join(lines), encoding="utf-8")
            destination.chmod(0o600)
            copied.append("configuration/.env")
    return copied, excluded_keys


def unique_bundle_path(destination_root: Path, basename: str) -> Path:
    candidate = destination_root / basename
    counter = 1
    while candidate.exists():
        candidate = destination_root / f"{basename}-{counter:02d}"
        counter += 1
    return candidate


def build_manifest(staging: Path, metadata: dict[str, object]) -> None:
    files: list[dict[str, object]] = []
    for path in sorted(staging.rglob("*")):
        if path.is_file() and not path.is_symlink():
            files.append(
                {
                    "path": path.relative_to(staging).as_posix(),
                    "sha256": sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    metadata["files"] = files
    manifest = staging / "manifest.json"
    manifest.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest.chmod(0o600)


def create_backup(args: argparse.Namespace) -> Path:
    root = args.root.resolve(strict=False)
    configured_destination = args.destination or get_config_value(
        root, "DEUTSCHOS_BACKUP_DIR", "backups"
    )
    destination_root = Path(configured_destination).expanduser()
    if not destination_root.is_absolute():
        destination_root = root / destination_root
    destination_root = destination_root.resolve(strict=False)

    data_root = (root / "data").resolve(strict=False)
    default_backup_root = (root / "backups").resolve(strict=False)
    if destination_root == root:
        raise BackupError(
            "El destino debe ser un directorio dedicado, no la raíz del proyecto."
        )
    if destination_root == data_root or destination_root.is_relative_to(data_root):
        raise BackupError("El destino de backup no puede estar dentro de data/.")
    if destination_root.is_relative_to(root) and not destination_root.is_relative_to(
        default_backup_root
    ):
        raise BackupError(
            "Dentro del repositorio solo se admite ./backups; "
            "usa una ruta externa para otro destino."
        )

    if args.label and not LABEL_PATTERN.fullmatch(args.label):
        raise BackupError(
            "La etiqueta solo admite minúsculas, números y guiones (máximo 32)."
        )

    destination_existed = destination_root.exists()
    destination_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not destination_existed:
        try:
            destination_root.chmod(0o700)
        except OSError:
            pass

    created_at = datetime.now(UTC).replace(microsecond=0)
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    basename = f"deutschos-{timestamp}"
    if args.label:
        basename = f"{basename}-{args.label}"
    final_path = unique_bundle_path(destination_root, basename)
    staging = destination_root / f".{final_path.name}.partial-{os.getpid()}"
    if staging.exists():
        raise BackupError(f"Ya existe un staging inesperado: {staging}")
    staging.mkdir(mode=0o700)

    database = database_path(root)
    copied_data: list[str] = []
    excluded_data: list[str] = []
    configuration_files: list[str] = []
    excluded_configuration_keys: list[str] = []
    try:
        database_destination = staging / "database" / database.name
        revision = backup_database(database, database_destination)

        if not args.database_only:
            copied_data, excluded_data = copy_data_tree(
                data_root, staging / "data", database
            )
            configuration_files, excluded_configuration_keys = copy_local_configuration(
                root, staging
            )

        manifest: dict[str, object] = {
            "schema_version": 1,
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "database_filename": database.name,
            "alembic_revision": revision,
            "database_only": bool(args.database_only),
            "copied_data_files": copied_data,
            "configuration_files": configuration_files,
            "excluded_configuration_keys": excluded_configuration_keys,
            "excluded_data_entries": sorted(excluded_data),
        }
        build_manifest(staging, manifest)
        staging.rename(final_path)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    print(f"Backup creado: {final_path}")
    print(f"Revisión Alembic: {revision or 'sin registrar'}")
    print(f"Archivos de datos adicionales: {len(copied_data)}")
    return final_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Raíz del repositorio")
    parser.add_argument(
        "destination",
        nargs="?",
        help="Destino (por defecto DEUTSCHOS_BACKUP_DIR o ./backups)",
    )
    parser.add_argument(
        "--database-only", action="store_true", help="Copia solo SQLite"
    )
    parser.add_argument("--label", help="Etiqueta corta para el nombre del bundle")
    return parser


def main() -> int:
    os.umask(0o077)
    args = build_parser().parse_args()
    try:
        create_backup(args)
    except KeyboardInterrupt:
        print("Backup cancelado; no se publicó ningún bundle parcial.", file=sys.stderr)
        return 130
    except (BackupError, ConfigurationError, OSError) as exc:
        print(f"Error de backup: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
