import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from backup_bundle import BackupError, create_backup  # noqa: E402
from local_config import get_config_value, is_loopback_url  # noqa: E402


def make_local_root(tmp_path: Path) -> Path:
    root = tmp_path / "LLC Project"
    data = root / "data"
    data.mkdir(parents=True)
    with sqlite3.connect(data / "deutschos.sqlite3") as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO alembic_version VALUES ('test-revision')")
        connection.execute("CREATE TABLE private_learning_data (value TEXT)")
        connection.execute("INSERT INTO private_learning_data VALUES ('local')")
        connection.commit()
    return root


def backup_args(root: Path, destination: Path, *, label: str = "test") -> argparse.Namespace:
    return argparse.Namespace(
        root=root,
        destination=str(destination),
        database_only=False,
        label=label,
    )


def test_backup_is_consistent_unique_and_excludes_secrets_and_models(tmp_path, monkeypatch):
    monkeypatch.setenv("LLC_DATABASE_URL", "sqlite:///./data/deutschos.sqlite3")
    root = make_local_root(tmp_path)
    (root / ".env").write_text(
        "LLC_TIMEZONE=Europe/Berlin\n"
        "NEXT_PUBLIC_API_URL=http://127.0.0.1:8000\n"
        "LLC_LM_STUDIO_BASE_URL=http://user:password@127.0.0.1:1234\n"
        "OPENAI_API_KEY=never-copy-this\n",
        encoding="utf-8",
    )
    (root / "node_modules").mkdir()
    (root / "node_modules" / "private.txt").write_text("generated", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "private.txt").write_text("generated", encoding="utf-8")
    (root / "data" / "models").mkdir()
    (root / "data" / "models" / "large.gguf").write_text("weights", encoding="utf-8")
    (root / "data" / "adapter.mlx").write_text("weights", encoding="utf-8")
    (root / "data" / "pedagogy").mkdir()
    (root / "data" / "pedagogy" / "notes.json").write_text("{}", encoding="utf-8")
    destination = tmp_path / "external backups"

    first = create_backup(backup_args(root, destination))
    second = create_backup(backup_args(root, destination))
    assert first != second
    assert first.is_dir() and second.is_dir()
    assert first.name.startswith("llc-")

    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["alembic_revision"] == "test-revision"
    assert manifest["configuration_files"] == ["configuration/.env"]
    assert manifest["excluded_configuration_keys"] == [
        "LLC_LM_STUDIO_BASE_URL",
        "OPENAI_API_KEY",
    ]
    assert manifest["copied_data_files"] == ["pedagogy/notes.json"]
    assert "models/" in manifest["excluded_data_entries"]
    assert "adapter.mlx" in manifest["excluded_data_entries"]

    safe_env = (first / "configuration" / ".env").read_text(encoding="utf-8")
    assert "LLC_TIMEZONE" in safe_env
    assert "NEXT_PUBLIC_API_URL" in safe_env
    assert "OPENAI_API_KEY" not in safe_env
    assert "LLC_LM_STUDIO_BASE_URL" not in safe_env
    assert "user:password" not in safe_env
    assert "never-copy-this" not in safe_env
    assert not any("node_modules" in path.as_posix() for path in first.rglob("*"))
    assert not any(".venv" in path.as_posix() for path in first.rglob("*"))
    assert not any(path.suffix == ".gguf" for path in first.rglob("*"))
    assert not any(path.suffix == ".mlx" for path in first.rglob("*"))

    with sqlite3.connect(first / "database" / "deutschos.sqlite3") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT value FROM private_learning_data").fetchone() == (
            "local",
        )


def test_loopback_urls_reject_embedded_credentials():
    assert is_loopback_url("http://127.0.0.1:8000")
    assert is_loopback_url("http://localhost:1234")
    assert not is_loopback_url("http://user:password@127.0.0.1:8000")
    assert not is_loopback_url("http://token@localhost:1234")


def test_local_config_prefers_llc_and_falls_back_to_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("DEUTSCHOS_TIMEZONE", "UTC")
    monkeypatch.delenv("LLC_TIMEZONE", raising=False)
    assert get_config_value(tmp_path, "LLC_TIMEZONE") == "UTC"

    monkeypatch.setenv("LLC_TIMEZONE", "Europe/Berlin")
    assert get_config_value(tmp_path, "LLC_TIMEZONE") == "Europe/Berlin"


def test_backup_reports_missing_database_without_publishing_partial_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("LLC_DATABASE_URL", "sqlite:///./data/deutschos.sqlite3")
    root = tmp_path / "missing database"
    root.mkdir()
    destination = tmp_path / "backups"
    try:
        create_backup(backup_args(root, destination))
    except BackupError as exc:
        assert "No existe la base de datos" in str(exc)
    else:  # pragma: no cover - protects the contract if implementation regresses
        raise AssertionError("missing database should fail")
    assert not any(destination.iterdir())


def test_backup_cli_reports_an_unwritable_or_invalid_destination_clearly(tmp_path):
    root = make_local_root(tmp_path)
    environment = os.environ.copy()
    environment["LLC_DATABASE_URL"] = "sqlite:///./data/deutschos.sqlite3"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_ROOT / "backup_bundle.py"),
            "--root",
            str(root),
            "/dev/null/not-a-directory",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 1
    assert result.stderr.startswith("Error de backup:")
    assert "Traceback" not in result.stderr
