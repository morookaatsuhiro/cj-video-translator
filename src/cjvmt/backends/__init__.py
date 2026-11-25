from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cjvmt.config import Settings


@dataclass(frozen=True)
class Request:
    system: str
    user: str
    condition: str
    direction: str
    source: str
    max_tokens: int
    video: Path | None = None
    audio: Path | None = None
    frames: tuple[Path, ...] = ()
    interleaved: bool = False
    fps: float = 2
    max_pixels: int = 200704
    task: str = "translate"


class Backend(Protocol):
    identity: dict

    def generate(self, request: Request) -> dict: ...


def make_backend(settings: Settings, name: str) -> Backend:
    if name == "mock":
        from cjvmt.backends.mock import Mock

        return Mock()
    if name == "qwen_omni":
        from cjvmt.backends.qwen_omni import QwenOmni

        return QwenOmni(settings)
    if name == "local_omni":
        from cjvmt.backends.local_omni import LocalOmni

        return LocalOmni(settings)
    raise ValueError(f"后端 {name} 未启用。")
