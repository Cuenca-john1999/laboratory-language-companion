import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = PROJECT_ROOT / "scripts"


def launcher_environment(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DEUTSCHOS_LAUNCHER_TEST_MODE": "1",
            "DEUTSCHOS_LAUNCHER_OLLAMA_PORT": "19434",
            "DEUTSCHOS_LAUNCHER_API_PORT": "19000",
            "DEUTSCHOS_LAUNCHER_WEB_PORT": "19300",
            "DEUTSCHOS_LAUNCHER_RUN_DIR": str(tmp_path / "run"),
            "DEUTSCHOS_LAUNCHER_LOG_DIR": str(tmp_path / "logs"),
            "DEUTSCHOS_LAUNCHER_NO_OPEN": "1",
            "DEUTSCHOS_LAUNCHER_NO_ALERT": "1",
            "DEUTSCHOS_LAUNCHER_TIMEOUT": "1",
        }
    )
    return environment


def run_script(name: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPTS / name)],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


def test_start_reports_missing_ollama_with_nonzero_status(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["DEUTSCHOS_LAUNCHER_OLLAMA_BIN"] = str(tmp_path / "ollama-does-not-exist")

    result = run_script("start.sh", environment)

    assert result.returncode == 1
    assert "Ollama no está instalado" in result.stderr
    assert not (tmp_path / "run" / "start.lock").exists()


def test_start_reports_missing_model_without_downloading(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["DEUTSCHOS_LAUNCHER_OLLAMA_BIN"] = "/usr/bin/true"
    environment["DEUTSCHOS_LAUNCHER_MODELS_DIR"] = str(tmp_path / "empty-models")

    result = run_script("start.sh", environment)

    assert result.returncode == 1
    assert "No hay modelos" in result.stderr
    assert "No se descargará ninguno" in result.stderr


def test_status_reports_and_stop_cleans_stale_pid_without_signalling(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["DEUTSCHOS_LAUNCHER_MODELS_DIR"] = str(tmp_path / "empty-models")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    stale_pid = run_directory / "api.pid"
    stale_pid.write_text("999999\nMon Jan  1 00:00:00 2001\n", encoding="utf-8")

    status = run_script("status.sh", environment)
    stop = run_script("stop.sh", environment)

    assert status.returncode != 0
    assert "PID file huérfano" in status.stdout
    assert stop.returncode == 0
    assert "se elimina sin enviar señales" in stop.stdout
    assert not stale_pid.exists()


def test_launcher_artifacts_and_model_store_are_ignored():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in ("/dist/", "/logs/", "/run/", "/Ollama/"):
        assert entry in gitignore


def test_launcher_uses_native_applescript_without_terminal():
    generator = (SCRIPTS / "create-macos-launcher.sh").read_text(encoding="utf-8")
    assert "/usr/bin/osacompile" in generator
    assert 'do shell script ("/bin/test -d "' in generator
    assert "/usr/bin/test" not in generator
    assert "do shell script" in generator
    assert "Terminal" not in generator
