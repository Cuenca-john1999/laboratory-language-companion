import os
import signal
import socket
import subprocess
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = PROJECT_ROOT / "scripts"


def launcher_environment(tmp_path: Path) -> dict[str, str]:
    materials = tmp_path / "materials"
    materials.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "LLC_LAUNCHER_TEST_MODE": "1",
            "LLC_LAUNCHER_LM_STUDIO_PORT": "19434",
            "LLC_LAUNCHER_API_PORT": "19000",
            "LLC_LAUNCHER_WEB_PORT": "19300",
            "LLC_LAUNCHER_RUN_DIR": str(tmp_path / "run"),
            "LLC_LAUNCHER_LOG_DIR": str(tmp_path / "logs"),
            "LLC_LAUNCHER_NO_OPEN": "1",
            "LLC_LAUNCHER_NO_ALERT": "1",
            "LLC_LAUNCHER_TIMEOUT": "1",
            "LLC_LAUNCHER_STOP_TIMEOUT": "1",
            "LLC_EDUCATIONAL_MATERIALS_DIR": str(materials),
            "LLC_EDUCATIONAL_LIBRARY_RUNTIME_DIR": str(tmp_path / "library"),
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


def start_fake_lm_studio() -> subprocess.Popen[bytes]:
    # Keep Bash as the stable parent PID; a one-command script may exec sleep.
    return subprocess.Popen(
        [
            "/bin/bash",
            "-c",
            "while :; do /bin/sleep 30; done",
            "lm_studio",
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


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def start_exact_llc_api(tmp_path: Path, port: int) -> subprocess.Popen[bytes]:
    runtime = tmp_path / "library"
    materials = tmp_path / "materials"
    materials.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "LLC_DATABASE_URL": f"sqlite:///{tmp_path / 'api.sqlite3'}",
            "LLC_EDUCATIONAL_MATERIALS_DIR": str(materials),
            "LLC_EDUCATIONAL_LIBRARY_RUNTIME_DIR": str(runtime),
        }
    )
    subprocess.run(
        [
            str(PROJECT_ROOT / ".venv/bin/python"),
            "-m",
            "alembic",
            "-c",
            str(PROJECT_ROOT / "apps/api/alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process = subprocess.Popen(
        [
            str(PROJECT_ROOT / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "llc_api.main:app",
            "--app-dir",
            str(PROJECT_ROOT / "apps/api/src"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return process
        except OSError:
            if process.poll() is not None:
                break
            time.sleep(0.1)
    terminate_process_group(process)
    raise RuntimeError("exact LLC API did not start")


def test_launcher_prefers_llc_environment_and_accepts_legacy_fallback(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_API_PORT"] = "19001"
    environment["DEUTSCHOS_LAUNCHER_API_PORT"] = "19002"
    environment.pop("LLC_LAUNCHER_WEB_PORT")
    environment["DEUTSCHOS_LAUNCHER_WEB_PORT"] = "19302"

    result = run_launcher_helper('printf "%s %s\\n" "$API_PORT" "$WEB_PORT"', environment)

    assert result.returncode == 0
    assert result.stdout == "19001 19302\n"


def test_start_reports_missing_lm_studio_with_nonzero_status(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_LM_STUDIO_BIN"] = str(tmp_path / "lm_studio-does-not-exist")

    result = run_script("start.sh", environment)

    assert result.returncode == 1
    assert "LM Studio no está instalado" in result.stderr
    assert not (tmp_path / "run" / "start.lock").exists()


@pytest.mark.skip(reason="LM Studio model storage is managed externally")
def test_start_reports_missing_model_without_downloading(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_LM_STUDIO_BIN"] = "/usr/bin/true"
    environment["LLC_LAUNCHER_MODELS_DIR"] = str(tmp_path / "empty-models")

    result = run_script("start.sh", environment)

    assert result.returncode == 1
    assert "No hay modelos" in result.stderr
    assert "No se descargará ninguno" in result.stderr


def test_start_rejects_lm_studio_port_owned_by_external_process(tmp_path):
    class NonLMStudioHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), NonLMStudioHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_LM_STUDIO_PORT"] = str(server.server_port)
    environment["LLC_LAUNCHER_LM_STUDIO_BIN"] = "/usr/bin/true"
    models = tmp_path / "models" / "manifests"
    models.mkdir(parents=True)
    (models / "qwen3-14b").write_text("manifest", encoding="utf-8")
    environment["LLC_LAUNCHER_MODELS_DIR"] = str(tmp_path / "models")

    try:
        result = run_script("start.sh", environment)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 1
    assert "ocupado por un servicio que no responde como LM Studio" in result.stderr


def test_lm_studio_probe_rejects_noncompatible_http_200(tmp_path):
    class WrongServiceHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), WrongServiceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_LM_STUDIO_PORT"] = str(server.server_port)
    try:
        result = run_launcher_helper("lm_studio_ready", environment)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode != 0


def test_cli_exit_zero_without_models_endpoint_is_not_success(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_LMS_BIN"] = "/usr/bin/true"

    result = run_script("start.sh", environment)

    assert result.returncode == 1
    assert "LM Studio no arrancó antes del tiempo límite" in result.stderr
    assert "lms server start finalizó con código 0" in result.stdout
    assert "Iniciando FastAPI" not in result.stdout


def test_lms_resolution_is_absolute_and_does_not_depend_on_path(tmp_path):
    fake_home = tmp_path / "home"
    cli = fake_home / ".lmstudio" / "bin" / "lms"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    cli.chmod(0o700)
    environment = launcher_environment(tmp_path)
    environment["HOME"] = str(fake_home)
    environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"

    result = run_launcher_helper(
        'resolve_lms_bin; printf "%s\\n%s\\n" "$LMS_BIN" "$LMS_BIN_DISPLAY"',
        environment,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [str(cli), "~/.lmstudio/bin/lms"]


def test_status_reports_and_stop_cleans_stale_pid_without_signalling(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_MODELS_DIR"] = str(tmp_path / "empty-models")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    stale_pid = run_directory / "api.pid"
    stale_pid.write_text("999999\nMon Jan  1 00:00:00 2001\n", encoding="utf-8")

    status = run_script("status.sh", environment)
    stop = run_script("stop.sh", environment)

    assert status.returncode != 0
    assert "stale · proceso inexistente" in status.stdout
    assert stop.returncode == 0
    assert "se elimina sin enviar señales" in stop.stdout
    assert not stale_pid.exists()


def test_status_machine_output_is_stable_and_parseable(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_MODELS_DIR"] = str(tmp_path / "empty-models")

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
        "format": "llc-status-v2",
        "ssd": "available",
        "model_count": "0",
        "library_path": str(tmp_path / "materials"),
        "library": "available",
        "library_source_count": "0",
        "lm_studio": "inactive",
        "api": "inactive",
        "web": "inactive",
        "pid_lm_studio": "absent",
        "pid_api": "absent",
        "pid_web": "absent",
        "result": "stopped",
    }


def test_status_machine_queries_lm_studio_models_once(tmp_path):
    requests = 0

    class LMStudioHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal requests
            requests += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"google/gemma-4-12b-qat"}]}')

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), LMStudioHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_LM_STUDIO_PORT"] = str(server.server_port)

    try:
        result = subprocess.run(
            [str(SCRIPTS / "status.sh"), "--machine"],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    fields = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert requests == 1
    assert fields["lm_studio"] == "active"
    assert fields["model_count"] == "1"


def test_status_machine_reuses_api_model_snapshot_when_api_is_running(tmp_path):
    model_requests = 0

    class LLCAPIHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal model_requests
            if self.path == "/api/models":
                model_requests += 1
                payload = (
                    b'{"provider":"lm_studio","available":true,'
                    b'"models":[{"name":"google/gemma-4-12b-qat"}],"error":null}'
                )
            elif self.path == "/health":
                payload = b'{"status":"ok","service":"llc-api"}'
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), LLCAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_API_PORT"] = str(server.server_port)

    try:
        result = subprocess.run(
            [str(SCRIPTS / "status.sh"), "--machine"],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    fields = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert model_requests == 1
    assert fields["lm_studio"] == "active"
    assert fields["model_count"] == "1"


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


@pytest.mark.skip(reason="LLC no longer creates LM Studio PID files")
def test_pid_written_in_one_timezone_validates_in_another(tmp_path):
    environment = launcher_environment(tmp_path)
    process = start_fake_lm_studio()

    try:
        environment["TARGET_PID"] = str(process.pid)
        written = run_launcher_helper(
            'write_pid_file lm_studio "$TARGET_PID"',
            {**environment, "TZ": "Europe/Berlin"},
        )
        checked = run_launcher_helper(
            'pid_file_state lm_studio; printf "%s:%s\\n" "$PID_VALUE" "$PID_REASON"',
            {**environment, "TZ": "UTC"},
        )

        assert written.returncode == 0
        assert checked.returncode == 0
        assert checked.stdout.strip() == f"{process.pid}:válido"
    finally:
        terminate_process_group(process)


@pytest.mark.skip(reason="LLC no longer creates LM Studio PID files")
def test_stop_rejects_reused_pid_token_without_signalling(tmp_path):
    environment = launcher_environment(tmp_path)
    process = start_fake_lm_studio()
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    (run_directory / "lm_studio.pid").write_text(
        f"{process.pid}\nMon Jan 1 00:00:00 2001\n",
        encoding="utf-8",
    )

    try:
        stopped = run_script("stop.sh", environment)

        assert stopped.returncode == 0
        assert "huella de inicio distinta" in stopped.stdout
        assert process.poll() is None
        assert not (run_directory / "lm_studio.pid").exists()
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
        assert "stale (el PID no pertenece al comando esperado)" in stopped.stdout
        assert process.poll() is None
        assert not (run_directory / "api.pid").exists()
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_stop_is_idempotent_when_all_services_are_closed(tmp_path):
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_LMS_BIN"] = str(tmp_path / "missing-lms")

    first = run_script("stop.sh", environment)
    second = run_script("stop.sh", environment)

    assert first.returncode == 0
    assert second.returncode == 0
    assert "Servidor local de LM Studio no estaba ejecutándose" in first.stdout
    assert "Apagado parcial" not in second.stdout


def test_unknown_external_api_listener_is_not_killed(tmp_path):
    class UnknownHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), UnknownHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_API_PORT"] = str(server.server_port)
    try:
        classified = run_launcher_helper(
            'classify_role api; printf "%s:%s\\n" "$SERVICE_STATE" "$SERVICE_REASON"',
            environment,
        )
        stopped = run_script("stop.sh", environment)
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=1):
            still_running = True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert classified.stdout.startswith("external:")
    assert stopped.returncode != 0
    assert "no se envían señales" in stopped.stdout
    assert still_running


def test_exact_llc_api_can_be_adopted(tmp_path):
    port = unused_loopback_port()
    process = start_exact_llc_api(tmp_path, port)
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_API_PORT"] = str(port)
    try:
        result = run_launcher_helper(
            'classify_role api; printf "%s:%s\\n" "$SERVICE_STATE" "$SERVICE_PID"',
            environment,
        )
        record = (tmp_path / "run/api.pid").read_text(encoding="utf-8").splitlines()
        assert result.returncode == 0
        assert result.stdout.strip() == f"adopted:{process.pid}"
        assert record[0] == str(process.pid)
        assert record[2] == "adopted"
    finally:
        terminate_process_group(process)


def test_adopted_llc_api_can_be_stopped(tmp_path):
    port = unused_loopback_port()
    process = start_exact_llc_api(tmp_path, port)
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_API_PORT"] = str(port)
    try:
        adopted = run_launcher_helper("classify_role api", environment)
        stopped = run_script("stop.sh", environment)
        process.wait(timeout=5)
        assert adopted.returncode == 0
        assert stopped.returncode == 0
        assert "api · adopted" in stopped.stdout
        assert process.returncode is not None
        assert not (tmp_path / "run/api.pid").exists()
    finally:
        terminate_process_group(process)


def test_stale_pid_with_exact_llc_listener_recovers_as_adopted(tmp_path):
    port = unused_loopback_port()
    process = start_exact_llc_api(tmp_path, port)
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_API_PORT"] = str(port)
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    (run_directory / "api.pid").write_text(
        "999999\nMon Jan  1 00:00:00 2001\nmanaged\n", encoding="utf-8"
    )
    try:
        result = run_launcher_helper(
            'classify_role api; printf "%s:%s\\n" "$SERVICE_STATE" "$SERVICE_PID"',
            environment,
        )
        assert result.stdout.strip() == f"adopted:{process.pid}"
        assert (run_directory / "api.pid").read_text(encoding="utf-8").splitlines()[2] == "adopted"
    finally:
        terminate_process_group(process)


def test_stale_pid_with_unrelated_listener_refuses_adoption(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = launcher_environment(tmp_path)
    environment["LLC_LAUNCHER_API_PORT"] = str(server.server_port)
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    (run_directory / "api.pid").write_text(
        "999999\nMon Jan  1 00:00:00 2001\nmanaged\n", encoding="utf-8"
    )
    try:
        result = run_launcher_helper(
            'classify_role api; printf "%s\\n" "$SERVICE_STATE"', environment
        )
        assert result.stdout.strip() == "external"
        assert (run_directory / "api.pid").read_text(encoding="utf-8").startswith("999999\n")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_controller_keeps_stop_available_from_partial_state():
    model = (PROJECT_ROOT / "apps/macos-controller/Sources/LLCController/ControllerModel.swift").read_text(
        encoding="utf-8"
    )
    view = (PROJECT_ROOT / "apps/macos-controller/Sources/LLCController/ControllerView.swift").read_text(
        encoding="utf-8"
    )
    assert "phase != .checking && phase != .stopping" in model
    partial_block = view[view.index("case .partial, .error:") : view.index("case .ssdUnavailable:")]
    assert 'Button("Detener")' in partial_block
    assert ".disabled(!controller.canStop)" in partial_block
    assert "operationalLogQueue.async" in model


def test_controller_uses_production_web_runtime():
    start = (SCRIPTS / "start.sh").read_text(encoding="utf-8")
    assert "LLC_RUNTIME_MODE=desktop-production" in start
    assert '"$NODE_BIN" "$WEB_RUNTIME_SERVER"' in start
    assert '"$NEXT_BIN" dev' not in start


def test_development_web_mode_remains_explicit_and_separate():
    root_package = (PROJECT_ROOT / "package.json").read_text(encoding="utf-8")
    web_package = (PROJECT_ROOT / "apps/web/package.json").read_text(encoding="utf-8")
    assert '"dev:web"' in root_package
    assert "next dev --hostname 127.0.0.1" in web_package


def test_user_runtime_has_no_hmr_or_turbopack_command():
    start = (SCRIPTS / "start.sh").read_text(encoding="utf-8").lower()
    assert "turbopack" not in start
    assert "next dev" not in start
    assert "desktop-production" in start


def test_regular_web_build_cannot_mutate_selected_runtime_release():
    root_package = (PROJECT_ROOT / "package.json").read_text(encoding="utf-8")
    builder = (SCRIPTS / "build-web-runtime.sh").read_text(encoding="utf-8")
    start = (SCRIPTS / "start.sh").read_text(encoding="utf-8")
    assert '"build:web": "npm --workspace @llc/web run build"' in root_package
    assert 'WEB_BUILD="$PROJECT_ROOT/apps/web/.next"' in builder
    assert 'RELEASES_DIR="$RUNTIME_ROOT/releases"' in builder
    assert 'mv "$STAGING_DIR" "$RELEASE_DIR"' in builder
    assert 'cd "$WEB_RUNTIME_SERVER_DIR"' in start


def test_lm_studio_stop_verifies_listener_and_port():
    stop = (SCRIPTS / "stop.sh").read_text(encoding="utf-8")
    common = (SCRIPTS / "launcher-common.sh").read_text(encoding="utf-8")
    assert stop.index('"$LMS_BIN" server stop') < stop.index('wait_for_port_free "LM Studio"')
    assert "lm_studio_listener_is_exact" in stop
    assert "stop_exact_lm_studio_listener" in common


def test_complete_shutdown_order_is_scoped_and_verifies_all_ports():
    stop = (SCRIPTS / "stop.sh").read_text(encoding="utf-8")
    assert "for ROLE in web api" in stop
    assert stop.index("for ROLE in web api") < stop.index('"$LMS_BIN" server stop')
    assert '"FastAPI:$API_PORT" "Next.js:$WEB_PORT"' in stop
    assert 'wait_for_port_free "LM Studio" "$LM_STUDIO_PORT"' in stop


def test_full_stop_has_no_ambiguous_process_kills_or_safari_target():
    stop_script = (SCRIPTS / "stop.sh").read_text(encoding="utf-8")
    common = (SCRIPTS / "launcher-common.sh").read_text(encoding="utf-8")
    controller_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "apps/macos-controller/Sources/LLCController").glob("*.swift")
    )

    combined = "\n".join((stop_script, common, controller_source))
    assert "killall" not in combined
    assert "pkill -f" not in combined
    assert 'withBundleIdentifier: "com.apple.Safari"' not in combined
    assert "lms server stop" in stop_script
    assert "forceTerminate()" in controller_source
    assert combined.index("terminate()") < combined.index("forceTerminate()")


@pytest.mark.skip(reason="LLC no longer manages LM Studio processes")
def test_stop_does_not_adopt_service_that_changed_pid(tmp_path):
    environment = launcher_environment(tmp_path)
    original = start_fake_lm_studio()
    environment["TARGET_PID"] = str(original.pid)
    written = run_launcher_helper('write_pid_file lm_studio "$TARGET_PID"', environment)
    assert written.returncode == 0
    terminate_process_group(original)

    replacement = start_fake_lm_studio()
    try:
        stopped = run_script("stop.sh", environment)

        assert stopped.returncode == 0
        assert "proceso inexistente" in stopped.stdout
        assert replacement.poll() is None
        assert not (tmp_path / "run" / "lm_studio.pid").exists()
    finally:
        terminate_process_group(replacement)


@pytest.mark.skip(reason="LLC no longer manages LM Studio processes")
def test_stop_sends_sigterm_to_validated_managed_process(tmp_path):
    environment = launcher_environment(tmp_path)
    process = start_fake_lm_studio()

    try:
        environment["TARGET_PID"] = str(process.pid)
        written = run_launcher_helper('write_pid_file lm_studio "$TARGET_PID"', environment)
        assert written.returncode == 0

        stopped = run_script("stop.sh", environment)
        process.wait(timeout=5)

        assert stopped.returncode == 0
        assert "con SIGTERM" in stopped.stdout
        assert "lm_studio detenido limpiamente" in stopped.stdout
        assert process.returncode == -15
        assert not (tmp_path / "run" / "lm_studio.pid").exists()
    finally:
        terminate_process_group(process)


def test_launcher_artifacts_and_model_store_are_ignored():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in ("/dist/", "/logs/", "/run/", "/LM Studio/"):
        assert entry in gitignore


def test_launcher_uses_native_applescript_without_terminal():
    generator = (SCRIPTS / "create-macos-launcher.sh").read_text(encoding="utf-8")
    assert "/usr/bin/osacompile" in generator
    assert 'APP_PATH="$LEGACY_DIR/LLC Launcher.app"' in generator
    assert 'do shell script ("/bin/test -d "' in generator
    assert "/usr/bin/test" not in generator
    assert "do shell script" in generator
    assert "Terminal" not in generator


def test_native_controller_build_reuses_launcher_scripts():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "apps/macos-controller/Sources/LLCController").glob("*.swift")
    )
    build_script = (SCRIPTS / "build-macos-app.sh").read_text(encoding="utf-8")

    assert 'appendingPathComponent("scripts/start.sh")' in source
    assert 'appendingPathComponent("scripts/stop.sh")' in source
    assert 'appendingPathComponent("scripts/status.sh")' in source
    assert "Process()" in source
    assert 'executable: URL(fileURLWithPath: "/bin/bash")' in source
    assert "Terminal" not in source
    assert 'APP_PATH="$DIST_DIR/LLC.app"' in build_script
    assert "/usr/bin/codesign" in build_script


def test_launcher_targets_web_app_exactly_and_never_uses_ambiguous_app_name():
    start_script = (SCRIPTS / "start.sh").read_text(encoding="utf-8")
    controller_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "apps/macos-controller/Sources/LLCController").glob("*.swift")
    )

    assert "$HOME/Applications/LLC.app" in start_script
    assert "$HOME/Applications/DeutschOS.app" in start_script
    assert "open -a LLC" not in start_script
    assert "open -a LLC" not in controller_source
    assert 'open -a "LM Studio"' not in controller_source
    assert "NSWorkspace.shared.openApplication" in controller_source
    assert "homeDirectoryForCurrentUser" in controller_source


def test_native_startup_order_and_cancellation_are_explicit():
    controller = (
        PROJECT_ROOT / "apps/macos-controller/Sources/LLCController/ControllerModel.swift"
    ).read_text(encoding="utf-8")
    coordinator = (
        PROJECT_ROOT / "apps/macos-controller/Sources/LLCController/LMStudioCoordinator.swift"
    ).read_text(encoding="utf-8")

    assert controller.index("lmStudioCoordinator.ensureReady") < controller.index(
        'appendingPathComponent("scripts/start.sh")'
    )
    assert controller.index("phase = .startingAPI") < controller.index("phase = .openingWeb")
    assert "stop-during-start" in (
        PROJECT_ROOT / "apps/macos-controller/Sources/LLCController/ControllerView.swift"
    ).read_text(encoding="utf-8")
    assert "currentAction.cancel()" in controller
    assert "cleanupAfterFailure" in coordinator
    assert '["server", "stop"]' in coordinator
