import os
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = PROJECT_ROOT / "scripts"


def launcher_environment(tmp_path: Path) -> dict[str, str]:
    materials = tmp_path / "materials"
    materials.mkdir(exist_ok=True)
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
            "DEUTSCHOS_EDUCATIONAL_MATERIALS_DIR": str(materials),
            "DEUTSCHOS_EDUCATIONAL_LIBRARY_RUNTIME_DIR": str(tmp_path / "library"),
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


def run_launcher_helper(
    command: str,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            (
                'source "$PROJECT_ROOT/scripts/launcher-common.sh"; '
                f"ensure_launcher_directories; {command}"
            ),
        ],
        cwd=PROJECT_ROOT,
        env={**environment, "PROJECT_ROOT": str(PROJECT_ROOT)},
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


def start_fake_ollama() -> subprocess.Popen[bytes]:
    # Keep Bash as the stable parent PID; a one-command script may exec sleep.
    return subprocess.Popen(
        [
            "/bin/bash",
            "-c",
            "while :; do /bin/sleep 30; done",
            "ollama",
            "serve",
        ],
        start_new_session=True,
    )


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait(timeout=5)


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


def test_start_rejects_ollama_port_owned_by_external_process(tmp_path):
    class NonOllamaHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), NonOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = launcher_environment(tmp_path)
    environment["DEUTSCHOS_LAUNCHER_OLLAMA_PORT"] = str(server.server_port)
    environment["DEUTSCHOS_LAUNCHER_OLLAMA_BIN"] = "/usr/bin/true"
    models = tmp_path / "models" / "manifests"
    models.mkdir(parents=True)
    (models / "qwen3-14b").write_text("manifest", encoding="utf-8")
    environment["DEUTSCHOS_LAUNCHER_MODELS_DIR"] = str(tmp_path / "models")

    try:
        result = run_script("start.sh", environment)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 1
    assert "ocupado por un servicio que no responde como Ollama" in result.stderr


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


def test_status_machine_output_is_stable_and_parseable(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["DEUTSCHOS_LAUNCHER_MODELS_DIR"] = str(tmp_path / "empty-models")

    result = subprocess.run(
        [str(SCRIPTS / "status.sh"), "--machine"],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines())

    assert result.returncode != 0
    assert fields == {
        "format": "deutschos-status-v1",
        "ssd": "available",
        "model_count": "0",
        "library_path": str(tmp_path / "materials"),
        "library": "available",
        "library_source_count": "0",
        "ollama": "inactive",
        "api": "inactive",
        "web": "inactive",
        "pid_ollama": "absent",
        "pid_api": "absent",
        "pid_web": "absent",
        "result": "stopped",
    }


def test_process_start_token_is_independent_of_caller_timezone(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["TARGET_PID"] = str(os.getpid())
    tokens = []

    for timezone in ("UTC", "Europe/Berlin", "America/New_York"):
        result = run_launcher_helper(
            'process_start_token "$TARGET_PID"',
            {**environment, "TZ": timezone},
        )
        assert result.returncode == 0
        assert result.stdout.strip()
        tokens.append(result.stdout.strip())

    assert len(set(tokens)) == 1


def test_pid_written_in_one_timezone_validates_in_another(tmp_path):
    environment = launcher_environment(tmp_path)
    process = start_fake_ollama()

    try:
        environment["TARGET_PID"] = str(process.pid)
        written = run_launcher_helper(
            'write_pid_file ollama "$TARGET_PID"',
            {**environment, "TZ": "Europe/Berlin"},
        )
        checked = run_launcher_helper(
            'pid_file_state ollama; printf "%s:%s\\n" "$PID_VALUE" "$PID_REASON"',
            {**environment, "TZ": "UTC"},
        )

        assert written.returncode == 0
        assert checked.returncode == 0
        assert checked.stdout.strip() == f"{process.pid}:válido"
    finally:
        terminate_process_group(process)


def test_stop_rejects_reused_pid_token_without_signalling(tmp_path):
    environment = launcher_environment(tmp_path)
    process = start_fake_ollama()
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    (run_directory / "ollama.pid").write_text(
        f"{process.pid}\nMon Jan 1 00:00:00 2001\n",
        encoding="utf-8",
    )

    try:
        stopped = run_script("stop.sh", environment)

        assert stopped.returncode == 0
        assert "huella de inicio distinta" in stopped.stdout
        assert process.poll() is None
        assert not (run_directory / "ollama.pid").exists()
    finally:
        terminate_process_group(process)


def test_stop_rejects_external_command_even_with_valid_start_token(tmp_path):
    environment = launcher_environment(tmp_path)
    process = subprocess.Popen(["/bin/sleep", "30"])

    try:
        environment["TARGET_PID"] = str(process.pid)
        token = run_launcher_helper(
            'process_start_token "$TARGET_PID"',
            environment,
        ).stdout.strip()
        run_directory = tmp_path / "run"
        run_directory.mkdir(exist_ok=True)
        (run_directory / "api.pid").write_text(
            f"{process.pid}\n{token}\n",
            encoding="utf-8",
        )

        stopped = run_script("stop.sh", environment)

        assert stopped.returncode == 0
        assert "no pertenece al comando esperado" in stopped.stdout
        assert process.poll() is None
        assert not (run_directory / "api.pid").exists()
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_stop_does_not_adopt_service_that_changed_pid(tmp_path):
    environment = launcher_environment(tmp_path)
    original = start_fake_ollama()
    environment["TARGET_PID"] = str(original.pid)
    written = run_launcher_helper('write_pid_file ollama "$TARGET_PID"', environment)
    assert written.returncode == 0
    terminate_process_group(original)

    replacement = start_fake_ollama()
    try:
        stopped = run_script("stop.sh", environment)

        assert stopped.returncode == 0
        assert "proceso inexistente" in stopped.stdout
        assert replacement.poll() is None
        assert not (tmp_path / "run" / "ollama.pid").exists()
    finally:
        terminate_process_group(replacement)


def test_stop_sends_sigterm_to_validated_managed_process(tmp_path):
    environment = launcher_environment(tmp_path)
    process = start_fake_ollama()

    try:
        environment["TARGET_PID"] = str(process.pid)
        written = run_launcher_helper('write_pid_file ollama "$TARGET_PID"', environment)
        assert written.returncode == 0

        stopped = run_script("stop.sh", environment)
        process.wait(timeout=5)

        assert stopped.returncode == 0
        assert "con SIGTERM" in stopped.stdout
        assert "ollama detenido limpiamente" in stopped.stdout
        assert process.returncode == -15
        assert not (tmp_path / "run" / "ollama.pid").exists()
    finally:
        terminate_process_group(process)


def test_launcher_artifacts_and_model_store_are_ignored():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in ("/dist/", "/logs/", "/run/", "/Ollama/"):
        assert entry in gitignore


def test_launcher_uses_native_applescript_without_terminal():
    generator = (SCRIPTS / "create-macos-launcher.sh").read_text(encoding="utf-8")
    assert "/usr/bin/osacompile" in generator
    assert 'APP_PATH="$LEGACY_DIR/DeutschOS Launcher.app"' in generator
    assert 'do shell script ("/bin/test -d "' in generator
    assert "/usr/bin/test" not in generator
    assert "do shell script" in generator
    assert "Terminal" not in generator


def test_native_controller_build_reuses_launcher_scripts():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "apps/macos-controller/Sources/DeutschOSController").glob(
            "*.swift"
        )
    )
    build_script = (SCRIPTS / "build-macos-app.sh").read_text(encoding="utf-8")

    assert 'appendingPathComponent("scripts/start.sh")' in source
    assert 'appendingPathComponent("scripts/stop.sh")' in source
    assert 'appendingPathComponent("scripts/status.sh")' in source
    assert "Process()" in source
    assert 'process.executableURL = URL(fileURLWithPath: "/bin/bash")' in source
    assert "Terminal" not in source
    assert 'APP_PATH="$DIST_DIR/DeutschOS.app"' in build_script
    assert "/usr/bin/codesign" in build_script
