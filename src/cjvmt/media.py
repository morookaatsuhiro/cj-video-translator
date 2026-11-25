from __future__ import annotations

import json
import os
import subprocess
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from cjvmt.config import Settings, file_hash


def executable(name: str) -> str:
    return os.getenv(f"CJVMT_{name.upper()}", name)


def media_command(name: str, args: list[str], timeout: float) -> bytes:
    command = [executable(name), *args]
    try:
        return subprocess.run(command, check=True, timeout=timeout, capture_output=True).stdout
    except FileNotFoundError as error:
        raise RuntimeError(f"找不到 {name}。请安装 FFmpeg 或设置 CJVMT_{name.upper()}。") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{name} 处理超时（{timeout} 秒）") from error
    except subprocess.CalledProcessError as error:
        message = error.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{name} 处理失败：{message[-1000:]}") from error


def probe(path: Path, settings: Settings) -> dict:
    args = ["-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    info = json.loads(media_command("ffprobe", args, settings.values["clip"]["decode_timeout_s"]))
    streams = info.get("streams", [])
    video = next((x for x in streams if x["codec_type"] == "video"), {})
    frame_rate = video.get("avg_frame_rate", "0/1")
    duration = video.get("duration") or info.get("format", {}).get("duration") or 0
    if duration == "N/A":
        duration = info.get("format", {}).get("duration", 0)
    return {
        "duration_ms": round(float(duration) * 1000),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": float(Fraction(frame_rate)) if frame_rate != "0/0" else 0,
        "has_audio": any(x["codec_type"] == "audio" for x in streams),
    }


def ffmpeg(settings: Settings, args: list[str]) -> None:
    media_command(
        "ffmpeg",
        ["-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        settings.values["clip"]["decode_timeout_s"],
    )


def scale_filter(side: int) -> str:
    return f"scale='min({side},iw)':'min({side},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"


def video_encode() -> list[str]:
    return ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]


def browser_replay(source: Path, settings: Settings) -> Path:
    destination = source.with_suffix(".webm")
    if destination.is_file() and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return destination
    with TemporaryDirectory(dir=source.parent) as temporary:
        output = Path(temporary) / destination.name
        ffmpeg(
            settings,
            [
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:v",
                "libvpx",
                "-deadline",
                "realtime",
                "-cpu-used",
                "6",
                "-threads",
                "2",
                "-crf",
                "24",
                "-b:v",
                "500k",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "libopus",
                "-b:a",
                "96k",
                "-ar",
                "48000",
                str(output),
            ],
        )
        output.replace(destination)
    return destination


def prepare(path: Path, dest: Path, settings: Settings, *, max_seconds: float | None = None) -> dict:
    info = probe(path, settings)
    if not info["width"] or not info["duration_ms"] or not info["has_audio"]:
        raise ValueError("请选择有画面和音轨且具有有效时长的视频。")
    if max_seconds is not None and info["duration_ms"] > max_seconds * 1000:
        raise ValueError(f"视频超过 {max_seconds:g} 秒。")
    dest.mkdir(parents=True, exist_ok=True)
    clip = settings.values["clip"]
    base = ["-i", str(path)]
    ffmpeg(settings, [*base, "-map", "0:a:0", "-ac", "1", "-ar", str(clip["audio_sr"]), str(dest / "audio.wav")])
    shape = ["-vf", scale_filter(clip["long_side_px"]), *video_encode()]
    ffmpeg(settings, [*base, "-map", "0:v:0", "-an", *shape, str(dest / "silent.mp4")])
    ffmpeg(settings, [*base, "-map", "0:v:0", "-map", "0:a:0", *shape, "-c:a", "aac", str(dest / "source.mp4")])
    return {**info, "sha256": file_hash(path), "name": path.name}
