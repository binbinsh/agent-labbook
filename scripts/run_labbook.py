#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version as package_version
import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
STATE_ROOT = ROOT / ".labbook"
RUNTIME_ROOT = STATE_ROOT / "runtime"
RUNTIME_FLAG = "AGENT_LABBOOK_RUNTIME_ACTIVE"
REQUIREMENTS_PATH = ROOT / "scripts" / "mcp-runtime-requirements.txt"

def _runtime_key() -> str:
    digest = hashlib.sha256(REQUIREMENTS_PATH.read_bytes()).hexdigest()[:12]
    return f"{digest}-py{sys.version_info.major}.{sys.version_info.minor}"


def _venv_dir() -> Path:
    return RUNTIME_ROOT / "runtimes" / _runtime_key()


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python3"


def _marker_path(venv_dir: Path) -> Path:
    return venv_dir / ".agent-labbook-runtime"


def _runtime_ready(venv_dir: Path) -> bool:
    return _venv_python(venv_dir).exists() and _marker_path(venv_dir).exists()


def _write_runtime_marker(venv_dir: Path) -> None:
    _marker_path(venv_dir).write_text(REQUIREMENTS_PATH.read_text(encoding="utf-8"), encoding="utf-8")


def _current_env_has_compatible_mcp() -> bool:
    if importlib.util.find_spec("mcp") is None:
        return False
    try:
        raw_version = package_version("mcp")
    except PackageNotFoundError:
        return True
    parts = tuple(int(part) for part in raw_version.split(".")[:3])
    return parts >= (1, 26, 0)


def _bootstrap_runtime() -> Path:
    venv_dir = _venv_dir()
    python_path = _venv_python(venv_dir)
    if _runtime_ready(venv_dir):
        return python_path

    sys.stderr.write(f"Bootstrapping Agent Labbook MCP runtime in {venv_dir}\n")
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        venv.EnvBuilder(with_pip=True).create(venv_dir)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Could not create the Agent Labbook runtime environment. "
            "Use a Python build that includes venv and ensurepip."
        ) from exc
    python_path = _venv_python(venv_dir)
    command = [
        str(python_path),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--upgrade",
        "-r",
        str(REQUIREMENTS_PATH),
    ]
    completed = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, text=True)
    if completed.returncode != 0:
        raise SystemExit(
            "Could not bootstrap the Agent Labbook MCP runtime. "
            "Ensure this machine has network access the first time the server starts."
        )
    _write_runtime_marker(venv_dir)
    return python_path


def _reexec_into_runtime(argv: list[str]) -> None:
    if os.environ.get(RUNTIME_FLAG) == "1":
        return
    if _current_env_has_compatible_mcp():
        return
    python_path = _bootstrap_runtime()
    env = dict(os.environ)
    env[RUNTIME_FLAG] = "1"
    os.execve(str(python_path), [str(python_path), str(Path(__file__).resolve()), *argv], env)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args[:1] == ["mcp"]:
        _reexec_into_runtime(args)

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from labbook.cli import main as cli_main

    return int(cli_main(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
