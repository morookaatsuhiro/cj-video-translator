from __future__ import annotations

import csv
import json
from collections import deque
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from queue import Queue
from threading import Thread
from time import monotonic, sleep
from typing import Callable, Iterator

import numpy as np

from cjvmt.asr import LANGUAGE_UNCERTAIN, asr_confidence, detect_direction, make_asr, trusted_context
from cjvmt.backends import Backend, make_backend
from cjvmt.checks import inspect_text
from cjvmt.clip import read_wav, write_wav
from cjvmt.config import Settings, file_hash
from cjvmt.media import ffmpeg, prepare, scale_filter
from cjvmt.pipeline import subtitle
from cjvmt.segment import neutralize
from cjvmt.store import base_manifest, new_run, now, write_json, write_text
from cjvmt.translate import Translator

EventSink = Callable[[dict], None]
LATENCY_METRICS = ("asr", "quick", "multimodal")
STAGE_METRICS = (
    "vad_wait",
    "queue_wait",
    "asr_inference",
    "quick_translation",
    "observation",
    "multimodal_translation",
)


class Ring:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.chunks = deque()
        self.frames = deque()
        self.end = 0

    def append(self, samples: np.ndarray) -> None:
        self.chunks.append((self.end, samples))
        self.end += len(samples)
        cutoff = self.end - self.capacity
        while self.chunks and self.chunks[0][0] + len(self.chunks[0][1]) <= cutoff:
            self.chunks.popleft()
        while self.frames and self.frames[0][0] < cutoff:
            self.frames.popleft()

    def audio(self, start: int, end: int) -> np.ndarray:
        if start < max(0, self.end - self.capacity) or end > self.end or start >= end:
            raise ValueError("句子已超出音频缓存范围。")
        return np.concatenate(
            [
                chunk[max(0, start - offset) : end - offset]
                for offset, chunk in self.chunks
                if offset < end and offset + len(chunk) > start
            ]
        )

    def images(self, start: int, end: int, limit: int) -> tuple[Path, ...]:
        paths = [path for time, path in self.frames if start <= time <= end]
        if len(paths) > limit:
            paths = [paths[index] for index in np.linspace(0, len(paths) - 1, limit, dtype=int)]
        return tuple(paths)


class SentenceQueue(Queue):
    def pending(self) -> int:
        with self.mutex:
            return len(self.queue) - bool(self.queue and self.queue[-1] is None)


class SilenceDetector:
    def __init__(self, rate: int, settings: dict, minimum_ms: int):
        vad = settings["vad"]
        self.threshold = vad["threshold"]
        self.pause = round(rate * vad["min_silence_ms"] / 1000)
        self.padding = round(rate * vad["speech_pad_ms"] / 1000)
        self.maximum = round(rate * settings["max_utt_sec"])
        self.minimum = round(rate * minimum_ms / 1000)
        self.start = self.end = None
        self.previous = 0

    def feed(self, start: int, end: int, probability: float) -> tuple[int, int] | None:
        if probability >= self.threshold:
            if self.start is None:
                self.start = max(self.previous, start - self.padding)
            self.end = end
        if self.start is None:
            return None
        if end - self.end >= self.pause:
            return self.finish(min(end, self.end + self.padding, self.start + self.maximum))
        if end - self.start >= self.maximum:
            return self.finish(min(self.end + self.padding, self.start + self.maximum))
        return None

    def finish(self, end: int | None = None) -> tuple[int, int] | None:
        if self.start is None:
            return None
        start, end = self.start, self.end if end is None else end
        self.start = self.end = None
        self.previous = end
        return (start, end) if end - start >= self.minimum else None


class Silero:
    def __init__(self, chunk: int):
        from faster_whisper.vad import get_vad_model

        self.session = get_vad_model().session
        self.chunk = chunk
        self.hidden = np.zeros((1, 1, 128), dtype=np.float32)
        self.cell = self.hidden.copy()
        self.context = np.zeros(64, dtype=np.float32)

    def __call__(self, samples: np.ndarray) -> float:
        pcm = samples.astype(np.float32) / 32768
        pcm = np.pad(pcm, (0, self.chunk - len(pcm)))
        array = np.concatenate([self.context, pcm])
        output, self.hidden, self.cell = self.session.run(
            None,
            {"input": array[np.newaxis, :], "h": self.hidden, "c": self.cell},
        )
        self.context = pcm[-64:]
        return float(output.item())


@dataclass
class Sentence:
    index: int
    start: int
    end: int
    pcm: np.ndarray
    frames: tuple[Path, ...]
    frame_fps: float
    queued_at: float
    vad_wait_ms: float


def milliseconds(start: float) -> float:
    return round((monotonic() - start) * 1000, 1)


def percentiles(values: list[float]) -> dict:
    return {
        "n": len(values),
        "median_ms": round(float(np.median(values)), 1) if values else None,
        "p90_ms": round(float(np.quantile(values, 0.9)), 1) if values else None,
    }


def metric_summary(rows: list[dict], status: str) -> dict:
    output = {"status": status, "utterances": len(rows), "skipped": sum(row["skipped"] for row in rows)}
    output["skip_rate"] = output["skipped"] / len(rows) if rows else None
    for metric in LATENCY_METRICS:
        output[metric] = percentiles(
            [row["latency_ms"][metric] for row in rows if row["latency_ms"].get(metric) is not None]
        )
    output["stages"] = {
        metric: percentiles([row["stage_ms"][metric] for row in rows if metric in row.get("stage_ms", {})])
        for metric in STAGE_METRICS
    }
    return output


def work(
    queue: Queue,
    run: Path,
    settings: Settings,
    backend: Backend,
    asr,
    mode: str,
    direction: str,
    rate: int,
    clock_zero: float,
    emit: EventSink,
    rows: list[dict],
    on_direction: EventSink | None = None,
    hotwords: tuple[str, ...] = (),
) -> None:
    translator = Translator(settings, backend)
    config = settings.values["realtime"]
    context_settings = settings.values["context"]
    incoming = iter(queue.get, None)
    pending = []
    if direction == "auto":
        samples = 0
        interval = round(rate * config["language_probe_sec"])
        last_probe = 0
        detection = None
        for sentence in incoming:
            pending.append(sentence)
            samples += len(sentence.pcm)
            if samples - last_probe >= interval:
                detection = detect_direction(asr, np.concatenate([item.pcm for item in pending]), settings)
                if detection:
                    break
                if samples >= rate * config["language_max_speech_sec"]:
                    raise ValueError(LANGUAGE_UNCERTAIN)
                last_probe = samples
        else:
            if not pending:
                return
            if samples > last_probe:
                detection = detect_direction(asr, np.concatenate([item.pcm for item in pending]), settings)
        if not detection:
            raise ValueError(LANGUAGE_UNCERTAIN)
        direction = detection["direction"]
        if on_direction:
            on_direction(detection)
    language = settings.values["asr"]["lang"][direction]
    session = run / "session"
    with (
        (session / "session.jsonl").open("w", encoding="utf-8") as log,
        (run / "predictions.jsonl").open("w", encoding="utf-8") as predictions,
    ):
        for sentence in chain(pending, incoming):
            queue_wait = milliseconds(sentence.queued_at)
            line = f"L{sentence.index:03}"
            start, end = round(sentence.start * 1000 / rate), round(sentence.end * 1000 / rate)

            def latency() -> float:
                return round((monotonic() - clock_zero) * 1000 - end, 1)

            try:
                wav = session / "audio" / f"{line}.wav"
                write_wav(wav, sentence.pcm, rate)
                asr_start = monotonic()
                if hasattr(asr, "transcribe_sentence"):
                    groups = asr.transcribe_sentence(wav, language, beam_size=config["asr_beam_size"])
                else:
                    groups = asr.transcribe(wav, language, duration_ms=end - start, offset_ms=start)
                original = "".join(group["text"] for group in groups).strip()
                if not original:
                    emit({"type": "no_speech", "line_id": line})
                    continue
                quality = asr_confidence(groups)
                revision = None
                if hasattr(asr, "recheck") and getattr(queue, "pending", queue.qsize)() <= config["queue_max"]:
                    history = trusted_context(rows, start, "end_ms", settings)
                    revision = asr.recheck(wav, language, original, quality, history, hotwords)
                    if revision and revision["applied"]:
                        original = revision["candidate"]
                asr_time = milliseconds(asr_start)
                plain = neutralize(original, settings)
                text = plain if mode == "compare" else original
                context = [
                    previous["text_neutral" if mode == "compare" else "text_asr"]
                    for previous in rows
                    if start - previous["end_ms"] <= context_settings["prev_window_ms"]
                ]
                turns = context_settings["prev_turns"]
                context = context[-turns:] if turns else []
                record = {
                    "line_id": line,
                    "start_ms": start,
                    "end_ms": end,
                    "text_asr": original,
                    "text_neutral": plain,
                    "model_input": text,
                    "media": {
                        "audio": str(wav.relative_to(run)),
                        "frames": [str(path.relative_to(run)) for path in sentence.frames],
                    },
                    "latency_ms": {"asr": latency()},
                    "stage_ms": {
                        "vad_wait": sentence.vad_wait_ms,
                        "queue_wait": queue_wait,
                        "asr_inference": asr_time,
                    },
                    "asr_warning": quality["warning"],
                    "asr_confidence": quality,
                    **({"asr_recheck": revision} if revision else {}),
                    "skipped": False,
                    "changed": False,
                    "observation": None,
                }
                emit({**record, "type": "original"})
                common = {
                    "item_id": f"{run.name}:{line}",
                    "direction": direction,
                    "mode": mode,
                    "source": text,
                    "context": context,
                }
                quick, calls = translator.line(
                    **common, condition="T_neutral" if mode == "compare" else "T_asr", media={}
                )
                record["quick"] = quick["text"]
                record["stage_ms"]["quick_translation"] = calls[-1]["elapsed_ms"]
                record["latency_ms"]["quick"] = latency()
                emit({**record, "type": "quick"})
                backlog = getattr(queue, "pending", queue.qsize)() > config["queue_max"]
                if not config["two_pass"] or backlog or not sentence.frames:
                    record["skipped"] = True
                    record["skip_reason"] = (
                        "backlog" if backlog else ("media_unavailable" if not sentence.frames else "disabled")
                    )
                else:
                    full, more = translator.line(
                        **common,
                        condition="T+V+A_sep",
                        media={"audio": wav, "frames": sentence.frames},
                        fps=sentence.frame_fps,
                    )
                    calls.extend(more)
                    if len(more) == 2:
                        record["stage_ms"]["observation"] = more[0]["elapsed_ms"]
                    record["stage_ms"]["multimodal_translation"] = more[-1]["elapsed_ms"]
                    record.update(
                        multimodal=full["text"],
                        changed=quick["text"] != full["text"],
                        observation=full["observation"],
                        checks=inspect_text(full["text"], direction, full["observation"]),
                    )
                    record["latency_ms"]["multimodal"] = latency()
                for call in calls:
                    predictions.write(
                        json.dumps({**call, "line_id": line, "run_id": run.name}, ensure_ascii=False) + "\n"
                    )
                predictions.flush()
                rows.append(record)
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
                log.flush()
                emit({**record, "type": "sentence"})
            except Exception as error:
                emit({"type": "sentence_error", "line_id": line, "message": str(error)})
                raise


def file_sim(
    settings: Settings,
    source: Path,
    direction: str,
    mode: str,
    backend_name: str,
    emit: EventSink,
    *,
    run: Path | None = None,
    engine: Backend | None = None,
    asr=None,
    wait_for_player: Callable[[], None] | None = None,
    source_kind: str | None = None,
    hotwords: tuple[str, ...] = (),
) -> Path:
    config = settings.values["realtime"]
    rate, frame_fps = config["sr"], config["frame_fps"]
    chunk = config["vad"]["chunk_samples"]

    def prepare_video(out: Path, manifest: dict):
        info = prepare(source, out / "media", settings, max_seconds=settings.values["demo"]["live_max_sec"])
        manifest["media"] = info
        data, actual_rate = read_wav(out / "media/audio.wav")
        if actual_rate != rate:
            raise ValueError("实时音频与 VAD 的采样率必须一致。")
        data = data[: round(info["duration_ms"] * rate / 1000)]
        ffmpeg(
            settings,
            [
                "-i",
                str(out / "media/silent.mp4"),
                "-vf",
                f"fps={frame_fps}," + scale_filter(config["frame_max_side_px"]),
                "-q:v",
                "4",
                str(out / "session/frames/%05d.jpg"),
            ],
        )
        paths = sorted((out / "session/frames").glob("*.jpg"))

        def feed(zero):
            frame_index = 0
            for start in range(0, len(data), chunk):
                audio = data[start : start + chunk]
                end = start + len(audio)
                sleep(max(0, zero + end / rate - monotonic()))
                frames = []
                while frame_index < len(paths) and (time := round(frame_index * rate / frame_fps)) <= end:
                    frames.append((time, paths[frame_index]))
                    frame_index += 1
                yield audio, frames

        return feed

    identity = {"input_name": source.name, "input_sha256": file_hash(source), "source_kind": source_kind}
    return run_session(
        settings,
        direction,
        mode,
        backend_name,
        emit,
        prepare_video,
        identity,
        run=run,
        engine=engine,
        asr=asr,
        wait_for_player=wait_for_player,
        hotwords=hotwords,
    )


Feed = Callable[[float], Iterator[tuple[np.ndarray, list[tuple[int, Path]]]]]


def run_session(
    settings: Settings,
    direction: str,
    mode: str,
    backend_name: str,
    emit: EventSink,
    prepare_input: Callable[[Path, dict], Feed],
    identity: dict,
    *,
    run: Path | None = None,
    engine: Backend | None = None,
    asr=None,
    wait_for_player: Callable[[], None] | None = None,
    hotwords: tuple[str, ...] = (),
) -> Path:
    import copy

    out = run or new_run(settings, "session")
    session = out / "session"
    frames = session / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    manifest = base_manifest(settings, out)
    manifest.update(
        kind="realtime",
        direction=direction,
        prompt_mode=mode,
        **identity,
        conditions=["T_neutral" if mode == "compare" else "T_asr", "T+V+A_sep"],
        asr_hotwords=hotwords,
    )
    write_json(out / "manifest.json", manifest)
    rows: list[dict] = []
    config = settings.values["realtime"]
    queue = SentenceQueue()
    errors: list[Exception] = []
    failure = None
    startup = {}

    def publish(event: dict) -> None:
        emit(copy.deepcopy(event))

    try:
        if (direction != "auto" and direction not in settings.values["asr"]["lang"]) or mode not in settings.values[
            "prompt_modes"
        ]["available"]:
            raise ValueError("翻译方向或提示词模式无效。")
        emit({"type": "status", "message": "正在准备媒体和本地模型"})
        started = monotonic()
        feed = prepare_input(out, manifest)
        startup["media"] = milliseconds(started)
        engine = engine or make_backend(settings, backend_name)
        asr = asr or make_asr(settings, backend_name)
        webcam = identity.get("source_kind") == "webcam"

        def on_direction(detection: dict) -> None:
            manifest.update(direction=detection["direction"], language_detection=detection)
            publish({"type": "language", **detection})

        if direction == "auto" and not webcam:
            publish({"type": "status", "message": "正在识别源语言"})
            started = monotonic()
            detection = detect_direction(asr, out / "media/audio.wav", settings)
            startup["language_probe"] = milliseconds(started)
            if not detection:
                raise ValueError(LANGUAGE_UNCERTAIN)
            direction = detection["direction"]
            on_direction(detection)
        if hasattr(asr, "warm"):
            publish({"type": "progress", "message": "正在预热语音识别模型"})
            started = monotonic()
            asr.warm(settings.values["asr"]["lang"].get(direction, "ja"))
            startup["asr_warmup"] = milliseconds(started)
        if hasattr(engine, "warm"):
            publish({"type": "progress", "message": "正在加载翻译模型，首次运行可能较久"})
            started = monotonic()
            engine.warm()
            startup["translation_warmup"] = milliseconds(started)
        manifest.update(
            model=engine.identity,
            asr={
                "model": asr.model_id,
                "revision": asr.revision,
                "device": getattr(asr, "device", None),
                "compute_type": getattr(asr, "compute_type", None),
            },
        )
        write_json(out / "manifest.json", manifest)
        rate = config["sr"]
        frame_fps = config["frame_fps"]
        chunk = config["vad"]["chunk_samples"]
        if backend_name == "mock":

            def probability(samples: np.ndarray) -> float:
                return float(bool(samples.size and np.max(np.abs(samples.astype(np.int32))) > 500))
        else:
            probability = Silero(chunk)
        ring = Ring(round(rate * config["ring_buffer_sec"]))
        detector = SilenceDetector(rate, config, settings.values["segment"]["min_dur_ms"])
        index = 0
        publish(
            {
                "type": "ready",
                "run_id": out.name,
                "mock": backend_name == "mock",
                "webcam": webcam,
            }
        )
        if wait_for_player:
            wait_for_player()
        zero = monotonic()

        def process() -> None:
            try:
                work(
                    queue,
                    out,
                    settings,
                    engine,
                    asr,
                    mode,
                    direction,
                    rate,
                    zero,
                    publish,
                    rows,
                    on_direction,
                    hotwords,
                )
            except Exception as error:
                errors.append(error)

        worker = Thread(target=process, name="sentence-translator")
        worker.start()

        def submit(bounds: tuple[int, int] | None) -> None:
            nonlocal index
            if bounds:
                start, end = bounds
                paths = ring.images(start - round(config["pre_context_sec"] * rate), end, config["max_frames"])
                pcm = ring.audio(start, end)
                index += 1
                queue.put(
                    Sentence(
                        index, start, end, pcm, paths, frame_fps, monotonic(), round((ring.end - end) * 1000 / rate, 1)
                    )
                )

        try:
            for audio, new_frames in feed(zero):
                if errors:
                    raise errors[0]
                start = ring.end
                ring.frames.extend(new_frames)
                ring.append(audio)
                submit(detector.feed(start, ring.end, probability(audio)))
            submit(detector.finish())
            publish({"type": "progress", "message": "正在完成剩余字幕", "stage": "remaining", "total": index})
        finally:
            queue.put(None)
            worker.join()
        if errors:
            raise errors[0]
        publish({"type": "progress", "message": "正在导出字幕"})
        manifest["status"] = "complete"
    except Exception as error:
        manifest.update(status="failed", error=str(error))
        failure = error
    finally:
        summary = metric_summary(rows, manifest["status"])
        manifest.update(ended_at=now(), summary=summary, startup_ms=startup, line_count=len(rows))
        write_json(out / "manifest.json", manifest)
        with (session / "latency_summary.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["metric", "n", "median_ms", "p90_ms", "skip_rate"])
            metrics = {kind: summary[kind] for kind in LATENCY_METRICS}
            metrics.update((f"stage_{name}", values) for name, values in summary["stages"].items())
            for kind, values in metrics.items():
                writer.writerow([kind, values["n"], values["median_ms"], values["p90_ms"], summary["skip_rate"]])
            for name, duration in startup.items():
                writer.writerow([f"startup_{name}", 1, duration, duration, ""])
        subtitles = [
            {
                "utterance": {"t_start_abs_ms": row["start_ms"], "t_end_abs_ms": row["end_ms"]},
                "text": {"text_asr": row["text_asr"]},
                "outputs": {
                    "final": {"text": row.get("multimodal", row["quick"])},
                    "quick": {"text": row["quick"]},
                },
            }
            for row in rows
        ]
        write_text(session / "subtitles.srt", subtitle(subtitles, "final"))
        write_text(session / "subtitles_quick.srt", subtitle(subtitles, "quick"))
        if failure:
            emit({"type": "error", "message": str(failure), "run_id": out.name})
        else:
            emit({"type": "done", "summary": summary, "run_id": out.name})
    if failure:
        raise failure
    return out
