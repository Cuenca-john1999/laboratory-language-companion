#!/usr/bin/env python3
"""Read the small subset of local configuration needed by shell tooling.

The shell scripts deliberately do not ``source`` .env: a dotenv file is data,
not trusted shell code. This module has no third-party dependencies so backups
remain available even if the application virtual environment is incomplete.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

DEFAULT_DATABASE_URL = "sqlite:///./data/deutschos.sqlite3"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_PUBLIC_API_URL = "http://127.0.0.1:8000"

PUBLIC_KEYS = {
    "DEUTSCHOS_BACKUP_DIR",
    "DEUTSCHOS_CORS_ORIGINS",
    "DEUTSCHOS_DATABASE_URL",
    "DEUTSCHOS_OLLAMA_BASE_URL",
    "DEUTSCHOS_OLLAMA_MODEL",
    "DEUTSCHOS_TIMEZONE",
    "NEXT_PUBLIC_API_URL",
    "NEXT_TELEMETRY_DISABLED",
}
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigurationError(ValueError):
    """Raised when local configuration cannot be interpreted safely."""


def _decode_dotenv_value(raw_value: str, *, line_number: int) -> str:
    lexer = shlex.shlex(raw_value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        parts = list(lexer)
    except ValueError as exc:
        raise ConfigurationError(f".env:{line_number}: valor no válido: {exc}") from exc
    return " ".join(parts)


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse assignments without interpolation or shell execution."""
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigurationError(f"No se pudo leer {path}: {exc}") from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(f".env:{line_number}: se esperaba CLAVE=valor")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not KEY_PATTERN.fullmatch(key):
            raise ConfigurationError(f".env:{line_number}: clave no válida")
        values[key] = _decode_dotenv_value(raw_value.strip(), line_number=line_number)
    return values


def get_config_value(root: Path, key: str, default: str = "") -> str:
    if key in os.environ:
        return os.environ[key]
    return read_dotenv(root / ".env").get(key, default)


def database_path(root: Path) -> Path:
    """Resolve the configured SQLite URL relative to the repository root."""
    url = get_config_value(root, "DEUTSCHOS_DATABASE_URL", DEFAULT_DATABASE_URL)
    scheme, separator, raw_path = url.partition(":///")
    if not separator or scheme not in {"sqlite", "sqlite+pysqlite"}:
        raise ConfigurationError(
            "DEUTSCHOS_DATABASE_URL debe ser una URL SQLite local (sqlite:///...)."
        )

    raw_path = raw_path.split("?", 1)[0]
    decoded_path = unquote(raw_path)
    if not decoded_path or decoded_path == ":memory:":
        raise ConfigurationError(
            "La base SQLite en memoria no se puede diagnosticar ni copiar."
        )

    path = Path(decoded_path).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


def is_loopback_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Raíz del repositorio")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("database-path", help="Muestra la ruta SQLite normalizada")

    value_parser = subparsers.add_parser(
        "value", help="Muestra un valor local no secreto"
    )
    value_parser.add_argument("key", choices=sorted(PUBLIC_KEYS))
    value_parser.add_argument("--default", default="")

    loopback_parser = subparsers.add_parser(
        "is-loopback", help="Comprueba si una URL usa una dirección loopback"
    )
    loopback_parser.add_argument("url")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve(strict=False)
    try:
        if args.command == "database-path":
            print(database_path(root))
        elif args.command == "value":
            print(get_config_value(root, args.key, args.default))
        elif args.command == "is-loopback":
            return 0 if is_loopback_url(args.url) else 1
    except ConfigurationError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
