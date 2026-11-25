from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from cjvmt.config import Settings, stable_hash


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, data: object) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    write_text(path, "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def versions() -> dict:
    packages = (
        "cjvmt",
        "torch",
        "torchvision",
        "transformers",
        "qwen-omni-utils",
        "faster-whisper",
        "ctranslate2",
        "av",
        "ffmpeg",
        "numpy",
        "gradio",
        "fastapi",
        "ruff",
        "pytest",
        "fugashi",
        "vllm-omni",
    )
    found = {}
    for package in packages:
        try:
            found[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return found


def inspect_command(command: list[str], cwd: Path | None = None) -> str | None:
    try:
        return subprocess.check_output(command, cwd=cwd, stderr=subprocess.DEVNULL, text=True, timeout=8).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def new_run(settings: Settings, name: str) -> Path:
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M")
    path = settings.path("results") / f"{timestamp}_{name}_{uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def base_manifest(settings: Settings, run: Path) -> dict:
    return {
        "run_id": run.name,
        "started_at": now(),
        "status": "running",
        "schema_version": settings.values["version"],
        "git_commit": inspect_command(["git", "rev-parse", "HEAD"], cwd=settings.root),
        "config": settings.values,
        "config_sha256": stable_hash(settings.values),
        "prompts_sha256": stable_hash(settings.prompts),
        "seed": settings.values["seed"],
        "packages": versions(),
        "gpu": inspect_command(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]),
    }
