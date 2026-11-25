from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

from cjvmt.config import Settings
from cjvmt.media import ffmpeg, probe, video_encode


def clip_window(start: int, end: int, total: int, opts: dict) -> tuple[int, int]:
    start_with_pad = max(0, start - opts["pre_pad_ms"])
    end_with_pad = min(total, end + opts["post_pad_ms"])
    if end_with_pad - start_with_pad <= opts["max_window_ms"]:
        return start_with_pad, end_with_pad
    start_with_pad = max(start_with_pad, end - opts["max_window_ms"])
    return start_with_pad, min(end_with_pad, start_with_pad + opts["max_window_ms"])


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2:
            raise ValueError("需要 16 位单声道 PCM 音频。")
        return np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2"), stream.getframerate()


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(samples.astype("<i2", copy=False).tobytes())


def gate_audio(samples: np.ndarray, rate: int, offset_ms: int, length_ms: int, gate: tuple[int, int], fade_ms: int):
    first = round(offset_ms * rate / 1000)
    count = round(length_ms * rate / 1000)
    available = np.pad(samples[first : first + count], (0, max(0, first + count - max(first, len(samples)))))
    start = max(0, min(count, round((gate[0] - offset_ms) * rate / 1000)))
    end = max(start, min(count, round((gate[1] - offset_ms) * rate / 1000)))
    kept = available[start:end].astype(np.float32)
    fade = min(len(kept) // 2, math.ceil(rate * fade_ms / 1000))
    if fade:
        ramp = np.linspace(0, 1, fade + 1, dtype=np.float32)[:-1]
        kept[:fade] *= ramp
        kept[-fade:] *= ramp[::-1]
    gated = np.zeros(count, dtype="<i2")
    gated[start:end] = np.rint(kept).astype("<i2")
    return gated, (start, end)


def make_clip(
    media: Path,
    dest: Path,
    line: dict,
    duration_ms: int,
    settings: Settings,
    audio: tuple[np.ndarray, int] | None = None,
) -> dict:
    opts = settings.values["clip"]
    start, end = line["t_start_abs_ms"], line["t_end_abs_ms"]
    window_start, window_end = clip_window(start, end, duration_ms, opts)
    line_id = line["line_id"]
    relative = {
        key: Path(folder) / f"{line_id}.{ext}"
        for key, folder, ext in (
            ("path_v", "clips_v", "mp4"),
            ("path_audio_gated", "audio_gated", "wav"),
            ("path_av", "clips_av", "mp4"),
            ("path_preview", "preview", "mp4"),
        )
    }
    for path in relative.values():
        (dest / path).parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", f"{window_start / 1000:.3f}"]
    length = ["-t", f"{(window_end - window_start) / 1000:.3f}"]
    video = dest / relative["path_v"]
    ffmpeg(settings, [*seek, "-i", str(media / "silent.mp4"), *length, "-an", *video_encode(), str(video)])
    info = probe(video, settings)
    if info["has_audio"] or not info["duration_ms"]:
        raise RuntimeError(f"{line_id} 无声片段校验失败。")
    samples, rate = audio or read_wav(media / "audio.wav")
    margin = opts["audio_gate_margin_ms"]
    gated, bounds = gate_audio(
        samples, rate, window_start, info["duration_ms"], (start - margin, end + margin), opts["audio_fade_ms"]
    )
    if gated[: bounds[0]].any() or gated[bounds[1] :].any():
        raise RuntimeError(f"{line_id} 门控音频校验失败。")
    wav = dest / relative["path_audio_gated"]
    write_wav(wav, gated, rate)
    ffmpeg(
        settings,
        [
            "-i",
            str(video),
            "-i",
            str(wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(dest / relative["path_av"]),
        ],
    )
    ffmpeg(
        settings,
        [
            *seek,
            "-i",
            str(media / "source.mp4"),
            *length,
            *video_encode(),
            "-c:a",
            "aac",
            str(dest / relative["path_preview"]),
        ],
    )
    return {
        **{key: str(value) for key, value in relative.items()},
        "t_start_abs_ms": window_start,
        "t_end_abs_ms": window_end,
        "video_duration_ms": info["duration_ms"],
        "sample_rate": rate,
        "audio_samples": len(gated),
        "gate_start_sample": bounds[0],
        "gate_end_sample": bounds[1],
    }
