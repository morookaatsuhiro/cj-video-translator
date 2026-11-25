from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock

import numpy as np
from PIL import Image

from cjvmt.clip import write_wav
from cjvmt.config import Settings
from cjvmt.media import ffmpeg, probe, scale_filter, video_encode


class WebcamInput:
    def __init__(self, settings: Settings, *, record_replay: bool = False):
        self.settings = settings
        self.config = settings.values["realtime"]
        self.limit = self.config["sr"] * settings.values["demo"]["live_max_sec"]
        self.rate = self.config["sr"]
        self.window = self.config["vad"]["chunk_samples"]
        self.incoming = Queue(maxsize=32)
        self.lock = Lock()
        self.stopped = Event()
        self.ready = Event()
        self.record_replay = record_replay
        self.replay_ready = Event()
        self.recording: Path | None = None
        self.received = 0

    def finish_recording(self, recording: Path | None) -> bool:
        with self.lock:
            if self.replay_ready.is_set():
                return False
            self.recording = recording
            self.replay_ready.set()
            self._stop()
            return True

    def push(self, pcm: bytes, picture: bytes | None) -> None:
        if not pcm or len(pcm) % (2 * self.window) or len(pcm) > 2 * self.rate:
            raise ValueError("音频必须是按 512 个采样对齐的单声道 PCM。")
        if picture:
            try:
                with Image.open(io.BytesIO(picture), formats=["JPEG"]) as image:
                    side = self.config["frame_max_side_px"]
                    if max(image.size) > side or min(image.size) < 2:
                        raise ValueError("摄像头帧尺寸超出限制。")
                    image.verify()
            except (OSError, Image.DecompressionBombError) as error:
                raise ValueError("摄像头帧必须为有效 JPEG。") from error
        with self.lock:
            if not self.ready.is_set() or self.stopped.is_set():
                raise RuntimeError("摄像头会话暂时不接收数据。")
            samples = min(len(pcm) // 2, self.limit - self.received)
            try:
                self.incoming.put_nowait((pcm[: samples * 2], picture))
            except Full as error:
                raise RuntimeError("摄像头输入队列已满。") from error
            self.received += samples
            if self.received == self.limit:
                self._stop()

    def stop(self) -> None:
        with self.lock:
            self._stop()

    def close(self) -> None:
        with self.lock:
            self._stop()
            self.replay_ready.set()
            if self.recording is not None:
                self.recording.unlink(missing_ok=True)
                self.recording = None

    def _stop(self) -> None:
        if not self.stopped.is_set():
            self.stopped.set()
            try:
                self.incoming.put_nowait(None)
            except Full:
                pass

    def prepare(self, run: Path, manifest: dict, on_progress: Callable[[str], None] | None = None):
        rate = self.rate

        def feed(_zero):
            packets, frames = [], []
            position = 0
            self.ready.set()
            while not (self.stopped.is_set() and self.incoming.empty()):
                try:
                    packet = self.incoming.get(timeout=12)
                except Empty:
                    self.stop()
                    break
                if packet is None:
                    break
                pcm, jpeg = packet
                data = np.frombuffer(pcm, dtype="<i2")
                new_frames = []
                if jpeg:
                    path = run / "session/frames" / f"{len(frames) + 1:05d}.jpg"
                    path.write_bytes(jpeg)
                    new_frames.append((position + len(data), path))
                    frames.extend(new_frames)
                packets.append(data)
                for begin in range(0, len(data), self.window):
                    audio = data[begin : begin + self.window]
                    position += len(audio)
                    emit_frames = new_frames if begin + self.window >= len(data) else []
                    yield audio, emit_frames
            manifest["media"] = {"duration_ms": round(position / rate * 1000), "has_audio": bool(packets)}
            if packets:
                if self.record_replay:
                    if on_progress and not self.replay_ready.is_set():
                        on_progress("正在接收录制的视频")
                    self.replay_ready.wait(30)
                with self.lock:
                    self.replay_ready.set()
                    recording = self.recording
                if on_progress:
                    on_progress("正在保存录音并生成回放视频")
                media = run / "media"
                media.mkdir(exist_ok=True)
                samples = np.concatenate(packets)
                write_wav(media / "audio.wav", samples, rate)
                if recording is not None and self.save_recording(recording, media / "source.mp4"):
                    manifest["media"]["replay"] = "continuous"
                elif frames:
                    self.save_video(run, samples, frames)
                    manifest["media"]["replay"] = "sampled"

        return feed

    def save_recording(self, recording: Path, output: Path) -> bool:
        try:
            info = probe(recording, self.settings)
            if not info["width"] or not info["has_audio"]:
                return False
            ffmpeg(
                self.settings,
                [
                    "-i",
                    str(recording),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-t",
                    f"{self.limit / self.rate:g}",
                    "-vf",
                    scale_filter(self.settings.values["clip"]["long_side_px"]),
                    *video_encode(),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    str(output),
                ],
            )
        except (RuntimeError, ValueError):
            output.unlink(missing_ok=True)
            return False
        finally:
            recording.unlink(missing_ok=True)
        return True

    def save_video(self, run: Path, samples: np.ndarray, frames: list[tuple[int, Path]]) -> None:
        media = run / "media"
        wav = media / "audio.wav"
        spec = media / "frames.txt"
        ends = [stamp for stamp, _ in frames[1:]] + [len(samples)]
        starts = [0, *(stamp for stamp, _ in frames[1:])]
        lines = ["ffconcat version 1.0"]
        for (_, path), start, end in zip(frames, starts, ends, strict=True):
            if end > start:
                lines.extend((f"file ../session/frames/{path.name}", f"duration {(end - start) / self.rate:.6f}"))
        lines.append(f"file ../session/frames/{frames[-1][1].name}")
        spec.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ffmpeg(
            self.settings,
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(spec),
                "-i",
                str(wav),
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-r",
                "25",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                "-t",
                f"{len(samples) / self.rate:.6f}",
                "-movflags",
                "+faststart",
                str(media / "source.mp4"),
            ],
        )
