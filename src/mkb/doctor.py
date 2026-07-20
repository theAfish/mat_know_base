"""Read-only local development and deployment diagnostics."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def emit(self, state: str, name: str, detail: str) -> None:
        print(f"{state:>4}  {name:<22} {detail}")
        if state == "FAIL":
            self.failures += 1
        elif state == "WARN":
            self.warnings += 1

    def check(self, name: str, check: Callable[[], str]) -> None:
        try:
            self.emit("OK", name, check())
        except Exception as exc:
            self.emit("FAIL", name, f"{type(exc).__name__}: {exc}")


def command_version(command: list[str]) -> str:
    executable = shutil.which(command[0])
    if not executable:
        raise RuntimeError(f"{command[0]} not found")
    result = subprocess.run(
        [executable, *command[1:]], capture_output=True, text=True, timeout=15, check=False
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or "version check failed")
    return (result.stdout or result.stderr).strip().splitlines()[0]


def node_version() -> str:
    value = command_version(["node", "--version"])
    match = re.search(r"(\d+)\.(\d+)", value)
    if not match or int(match.group(1)) < 20:
        raise RuntimeError(f"{value}; requires Node.js >=20")
    return f"{value}; requires >=20"


def tcp_status(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def compose_status() -> str:
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    if not result.stdout.strip():
        return "services are not started (run make up when needed)"
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    unhealthy = [row.get("Service", "unknown") for row in rows if row.get("Health") == "unhealthy"]
    if unhealthy:
        raise RuntimeError("unhealthy: " + ", ".join(unhealthy))
    return ", ".join(
        f"{row.get('Service')}={row.get('State')}" +
        (f"/{row.get('Health')}" if row.get("Health") else "") for row in rows
    )


def main() -> int:
    report = Report()
    print("MKB doctor (read-only)\n")

    version = sys.version_info
    report.emit(
        "OK" if version >= (3, 10) else "FAIL",
        "Python",
        f"{version.major}.{version.minor}.{version.micro} ({sys.executable}); requires >=3.10",
    )
    report.check("Node.js", node_version)
    report.check("npm", lambda: command_version(["npm", "--version"]))
    report.check("Docker", lambda: command_version(["docker", "--version"]))
    report.check("Docker Compose", lambda: command_version(["docker", "compose", "version"]))

    env_file = ROOT / ".env"
    report.emit("OK" if env_file.is_file() else "FAIL", "Configuration", ".env exists" if env_file.is_file() else "missing .env; copy .env.example")

    from mkb.config import settings

    warnings, errors = settings.startup_issues()
    for message in errors:
        report.emit("FAIL", "Configuration", message)
    for message in warnings:
        report.emit("WARN", "Configuration", message)
    if not errors and not warnings:
        report.emit("OK", "Configuration", f"safe for {settings.deployment_mode.value} mode")

    endpoints = {
        "PostgreSQL port": (settings.pg_host, settings.pg_port),
        "S3 port": (urlsplit(settings.s3_endpoint).hostname or "localhost", urlsplit(settings.s3_endpoint).port or 80),
        "API port": (settings.api_host, settings.api_port),
    }
    for name, (host, port) in endpoints.items():
        open_ = tcp_status(host, port)
        report.emit("OK" if open_ else "WARN", name, f"{host}:{port} is {'reachable' if open_ else 'not listening'}")

    report.check("Compose services", compose_status)

    from mkb.web.diagnostics import check_database, check_object_storage

    def database() -> str:
        result = check_database()
        if not result.get("ok"):
            raise RuntimeError(
                f"revision {result.get('current_revision')} != {result.get('expected_revision')}"
            )
        return f"revision {result.get('revision')} is current"

    def buckets() -> str:
        result = check_object_storage()
        return f"{result['bucket_count']} required buckets are accessible"

    report.check("Database revision", database)
    report.check("Object storage", buckets)

    usage = shutil.disk_usage(ROOT)
    free_gib = usage.free / 1024**3
    report.emit("OK" if free_gib >= 2 else "WARN", "Disk space", f"{free_gib:.1f} GiB free at {ROOT}")

    backend = settings.pdf_backend
    if backend == "mineru_api":
        token = bool(settings.mineru_api_token)
        report.emit("OK" if token else "FAIL", "PDF processor", "MinerU API token configured" if token else "MKB_MINERU_API_TOKEN is missing")
    else:
        available = importlib.util.find_spec("mineru") is not None
        report.emit("OK" if available else "FAIL", "PDF processor", "local MinerU import available" if available else "mineru package is unavailable")
    tesseract = shutil.which("tesseract")
    report.emit("OK" if tesseract else "WARN", "Image OCR", tesseract or "tesseract executable not found; image OCR is unavailable")
    credentials = bool(settings.openai_api_key or settings.llm_api_key or settings.google_api_key)
    report.emit("OK" if credentials else "WARN", "LLM processor", f"credentials present for {settings.extraction_model}" if credentials else "no LLM API credential detected")

    print(f"\nSummary: {report.failures} failure(s), {report.warnings} warning(s)")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
