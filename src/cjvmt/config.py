from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class Settings:
    root: Path
    values: dict
    prompts: dict

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        explicit = path or os.getenv("CJVMT_CONFIG")
        if explicit:
            config_path = Path(explicit).expanduser().resolve()
        else:
            candidates = (*Path.cwd().parents,)
            root = next((p for p in (Path.cwd(), *candidates) if (p / "configs/mvp.yaml").is_file()), None)
            root = root or Path(__file__).resolve().parents[2]
            config_path = root / "configs/mvp.yaml"
        root = config_path.parent.parent
        try:
            from dotenv import load_dotenv

            load_dotenv(root / ".env", override=False)
        except ImportError:
            pass
        values = yaml.safe_load(config_path.read_text())
        prompts = yaml.safe_load((root / "configs/prompts/prompts_mvp.yaml").read_text())
        return cls(root, values, prompts)

    def path(self, name: str) -> Path:
        return self.root / self.values["paths"][name]
