from __future__ import annotations

import math
import os
import re
import subprocess
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from cjvmt.backends import Request
from cjvmt.checks import is_question_ja, is_question_zh, style_ja, uses_nin
from cjvmt.clip import read_wav, write_wav
from cjvmt.config import Settings
from cjvmt.dubbing import runtime, translated_lines
from cjvmt.media import executable
from cjvmt.store import read_json, write_json
from cjvmt.translate import Translator, observation_data

RESULT = "speakers.json"
STATUS = "speakers_status.json"


def available(settings: Settings) -> bool:
    python, _, model = runtime(settings)
    return python.is_file() and (model / "campplus.onnx").is_file()


def eligible(run: Path, manifest: dict) -> bool:
    return (
        manifest.get("status") == "complete"
        and manifest.get("kind") in {"offline", "realtime"}
        and manifest.get("direction") in {"zh2ja", "ja2zh"}
        and manifest.get("model", {}).get("backend") != "mock"
        and manifest.get("line_count", 0) > 0
        and (run / "media/audio.wav").is_file()
        and (run / "media/source.mp4").is_file()
    )


def annotate(data: dict, lines: list[dict], direction: str) -> dict:
    japanese = direction == "ja2zh"
    rows = data["lines"]
    people: dict[int, list[dict]] = {}
    for source in lines:
        row = rows[source["line_id"]]
        text = source["original"]
        use = []
        if japanese:
            ending = re.sub(r"(?:かい|かな|かね|よね|か|ね|よ)$", "", text.rstrip("。？！?! 　"))
            register = {"honorific": "日语尊敬表达", "polite": "日语敬体", "plain": "日语常体"}.get(style_ja(ending))
            if register:
                use.append(register)
        elif uses_nin(text):
            use.append("中文使用「您」")
        if (is_question_ja if japanese else is_question_zh)(text):
            use.append("问句表达")
        row["wording"] = use
        observation = source.get("observation") or {}
        if len(row["speaker_ids"]) == 1:
            if observation.get("tone") not in {None, "不确定"}:
                row["tone"] = {key: observation.get(key) for key in ("tone", "strength")}
            if row.get("speech_ms"):
                row["chars_per_second"] = round(sum(c.isalnum() for c in text) * 1000 / row["speech_ms"], 1)
            people.setdefault(row["speaker_ids"][0], []).append(row)
    summaries = []
    for speaker, utterances in sorted(people.items()):
        rates = [row["chars_per_second"] for row in utterances if row.get("chars_per_second", 0) > 0]
        pitch = [
            row["pitch"]["median_f0_hz"] for row in utterances if row.get("pitch", {}).get("median_f0_hz") is not None
        ]
        report = {
            "id": speaker,
            "lines": len(utterances),
            "face_lines": sum(bool(row.get("face")) for row in utterances),
        }
        if len(rates) >= 3:
            baseline = median(rates)
            report["typical_chars_per_second"] = round(baseline, 1)
            for row in utterances:
                if row.get("chars_per_second"):
                    ratio = row["chars_per_second"] / baseline
                    row["pace"] = "faster" if ratio >= 1.20 else "slower" if ratio <= 0.80 else "typical"
        if len(pitch) >= 3:
            baseline = median(pitch)
            report["typical_f0_hz"] = round(baseline, 1)
            for row in utterances:
                measured = row.get("pitch", {}).get("median_f0_hz")
                if measured:
                    row["pitch"]["relative_to_speaker_st"] = round(12 * math.log2(measured / baseline), 1)
        summaries.append(report)
    return {**data, "speakers": summaries}


def analyze(run: Path, manifest: dict, settings: Settings) -> dict:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    started = perf_counter()
    audio = run / "media/audio.wav"
    waveform, rate = read_wav(audio)
    if rate != 16000:
        raise ValueError("Expected mono 16 kHz source audio")
    speech = get_speech_timestamps(waveform.astype("float32") / 32768, VadOptions(min_silence_duration_ms=250))
    duration = len(waveform) * 1000 // rate
    lines = translated_lines(run, manifest)
    lines = [
        {**row, "end_ms": min(row["end_ms"], duration)}
        for row in lines
        if 0 <= row["start_ms"] < min(row["end_ms"], duration)
    ]
    if not lines:
        raise ValueError("No time-aligned source subtitles found")
    python, _, model = runtime(settings)
    face_model = Path(
        os.getenv("CJVMT_FACE_LANDMARKER", settings.root / ".cache/speaker-model/face_landmarker_v1.task")
    )
    face_detector = Path(
        os.getenv("CJVMT_FACE_DETECTOR", settings.root / ".cache/speaker-model/face_detection_yunet_2023mar.onnx")
    )
    with TemporaryDirectory(prefix=".speaker-analysis-", dir=run) as directory:
        request = Path(directory) / "request.json"
        output = request.with_name("result.json")
        write_json(
            request,
            {
                "audio": str(audio),
                "video": str(run / "media/source.mp4"),
                "ffmpeg": executable("ffmpeg"),
                "embedding_model": str(model / "campplus.onnx"),
                "face_model": str(face_model) if face_model.is_file() else None,
                "face_detector": str(face_detector) if face_detector.is_file() else None,
                "speech_ms": [[s["start"] // 16, s["end"] // 16] for s in speech],
                "lines": [{k: row[k] for k in ("line_id", "start_ms", "end_ms")} for row in lines],
                "status_file": str(run / STATUS),
            },
        )
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1", "OPENBLAS_NUM_THREADS": "4"}
        env["LD_LIBRARY_PATH"] = env.get("CJVMT_LOCAL_OMNI_LD_LIBRARY_PATH", env.get("LD_LIBRARY_PATH", ""))
        worker = Path(__file__).with_name("speaker_worker.py")
        with (run / "speakers.log").open("w", encoding="utf-8") as log:
            subprocess.run(
                [str(python), str(worker), str(request), str(output)],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                check=True,
                timeout=240,
            )
        result = annotate(read_json(output), lines, manifest["direction"])
    result.update(
        status="complete",
        elapsed_ms=round((perf_counter() - started) * 1000, 1),
        devices={"voice": "CAMPPlus / CPU", "pitch": "WORLD / CPU", "video": "YuNet + MediaPipe Face Landmarker / CPU"},
    )
    return result


def add_model_tone(run: Path, direction: str, result: dict, translator: Translator) -> None:
    lines = result["lines"]
    eligible_lines = [(key, row) for key, row in lines.items() if len(row["speaker_ids"]) == 1 and row.get("speech_ms")]
    pending = [(key, row) for key, row in eligible_lines if not row.get("tone")]
    audio, rate = read_wav(run / "media/audio.wav")
    labels = " / ".join(translator.settings.values["tone"]["labels"])
    status = "complete"
    with TemporaryDirectory(prefix=".speaker-tone-", dir=run) as directory:
        for index, (line, row) in enumerate(pending):
            write_json(
                run / STATUS,
                {
                    "status": "running",
                    "message": "正在分析原声语气",
                    "completed": index,
                    "total": len(pending),
                    "unit": "句",
                },
            )
            wav = Path(directory) / f"{index}.wav"
            start, end = (round(row[key] * rate / 1000) for key in ("start_ms", "end_ms"))
            write_wav(wav, audio[start:end], rate)
            request = Request(
                system="你是语音分析助手，只标注提供的原声语气。不确定时直接写不确定。",
                user=f"根据原声选择最接近的听感语气。可选：{labels}。只输出一行：4. 语气：标签；强度：弱/中/强。",
                condition="T+A",
                direction=direction,
                source="",
                max_tokens=80,
                audio=wav,
                task="observe",
            )
            try:
                prediction = translator.infer(request, f"{run.name}:{line}:speaker-tone")
            except (OSError, RuntimeError, ValueError):
                status = "partial"
                break
            parsed = observation_data(prediction["text"], translator.settings)
            if parsed["tone"] != "不确定":
                row["tone"] = {key: parsed[key] for key in ("tone", "strength")}
    result["model_tone"] = {
        "status": status,
        "labeled": sum(bool(row.get("tone")) for _, row in eligible_lines),
        "eligible": len(eligible_lines),
    }
