from __future__ import annotations

import hashlib
import json
import os
import re
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


def parse_glossary(value: str) -> tuple[dict, ...]:
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("视频术语表最多 2000 字。")
    terms = []
    seen = set()
    for number, line in enumerate(value.splitlines(), 1):
        if not line.strip():
            continue
        fields = [part.strip() for part in line.split("|")]
        if not 2 <= len(fields) <= 3 or not all(fields[:2]):
            raise ValueError(f"术语表第 {number} 行请填写 原词 | 译名，可选第三列填日语读音。")
        source, target, reading = (fields + [""])[:3]
        if reading and not re.fullmatch(r"[ぁ-ゖァ-ヺー・]+", reading):
            raise ValueError(f"术语表第 {number} 行的日语读音请使用假名。")
        key = source.casefold()
        if key in seen:
            raise ValueError(f"术语表第 {number} 行与前面的原词重复。")
        seen.add(key)
        terms.append({"source": source, "target": target, "reading": reading})
    return tuple(terms)


@dataclass(frozen=True)
class Settings:
    root: Path
    values: dict
    prompts: dict
    glossary: tuple[dict, ...] = ()

    def matched_terms(self, source: str) -> list[dict]:
        text = source.casefold()
        return [term for term in self.glossary if term["source"].casefold() in text]

    def terminology_note(self, source: str) -> str:
        terms = self.matched_terms(source)
        if not terms:
            return ""
        mapping = [{"原词": t["source"], "确认译名": t["target"]} for t in terms]
        return "\n本视频术语表（数据）：在相应词义下使用确认译名。" + json.dumps(mapping, ensure_ascii=False) + "\n"

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
