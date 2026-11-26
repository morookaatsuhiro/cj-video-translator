from __future__ import annotations

import os
import subprocess
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np

from cjvmt.asr import NUMBER, WhisperASR, comparable_text
from cjvmt.config import Settings
from cjvmt.segment import neutralize
from cjvmt.store import read_json, write_json

MODEL = "Qwen/Qwen3-ASR-1.7B-hf"


def runtime() -> tuple[Path, Path]:
    python = os.getenv("CJVMT_QWEN_ASR_PYTHON") or os.getenv("CJVMT_LOCAL_OMNI_PYTHON", "")
    return Path(python).expanduser(), Path(os.getenv("CJVMT_QWEN_ASR_PATH", "")).expanduser()


def available() -> bool:
    python, model = runtime()
    return python.is_file() and all((model / name).is_file() for name in ("model.safetensors", "tokenizer.json"))


def revise(
    run: Path, lines: list[dict], direction: str, settings: Settings, asr: WhisperASR, samples: np.ndarray, rate: int
) -> dict:
    python, snapshot = runtime()
    receipt = snapshot / "revision.txt"
    identity = {"model": MODEL, "revision": receipt.read_text().strip() if receipt.is_file() else snapshot.name}
    recheck = settings.values["asr"]["recheck"]
    requests = [{"start_ms": line["t_start_abs_ms"], "end_ms": line["t_end_abs_ms"], "prompt": None} for line in lines]
    counts = {"selected": len(lines), "total": len(lines)}
    if not requests:
        return {**identity, **counts, "status": "complete", "applied": 0, "elapsed_ms": 0}
    language = settings.values["asr"]["lang"][direction]
    started = perf_counter()
    try:
        with TemporaryDirectory(prefix=".qwen-asr-", dir=run) as directory:
            request = Path(directory) / "request.json"
            output = Path(directory) / "output.json"
            write_json(
                request,
                {
                    "audio": str(run / "media/audio.wav"),
                    "language": language,
                    "lines": requests,
                },
            )
            env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
            env["LD_LIBRARY_PATH"] = env.get("CJVMT_LOCAL_OMNI_LD_LIBRARY_PATH", env.get("LD_LIBRARY_PATH", ""))
            worker = Path(__file__).with_name("qwen_asr_worker.py")
            with (run / "qwen_asr.log").open("w", encoding="utf-8") as log:
                subprocess.run(
                    [str(python), str(worker), str(snapshot), str(request), str(output)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=120 + sum(row["end_ms"] - row["start_ms"] for row in requests) / 1000,
                    check=True,
                )
            candidates = read_json(output)
            if (
                not isinstance(candidates, list)
                or len(candidates) != len(lines)
                or not all(isinstance(value, str) for value in candidates)
            ):
                raise ValueError("Invalid Qwen ASR response")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {**identity, **counts, "applied": 0, "status": "fallback"}

    applied = 0
    context = settings.values["context"]
    for index, line in enumerate(lines):
        previous = lines[max(0, index - context["prev_turns"]) : index]
        line["context"] = [
            row["text_neutral"]
            for row in previous
            if line["t_start_abs_ms"] - row["t_end_abs_ms"] <= context["prev_window_ms"]
        ]
        candidate = candidates[index]
        original = line["text_asr"]
        first, second = map(comparable_text, (original, candidate))
        similarity = SequenceMatcher(None, first, second, autojunk=False).ratio()
        numbers = [NUMBER.findall(unicodedata.normalize("NFKC", t)) for t in (original, candidate)]
        adopted, scores = original, None
        if first == second:
            reason = "两遍识别一致"
        elif numbers[0] != numbers[1]:
            reason = "数值存在分歧"
        elif (
            not first
            or not second
            or similarity < recheck["min_similarity"]
            or not recheck["min_coverage"] <= len(second) / len(first) <= 1 / recheck["min_coverage"]
        ):
            reason = "两遍转写差异过大"
        else:
            start, end = round(rate * line["t_start_abs_ms"] / 1000), round(rate * line["t_end_abs_ms"] / 1000)
            try:
                if rate != 16000 or end <= start:
                    raise ValueError("Expected non-empty 16 kHz audio")
                scores = asr.score_transcripts(samples[start:end], language, (first, second))
            except (RuntimeError, ValueError, OSError):
                reason = "原声评分不可用"
            else:
                if scores[1] >= scores[0] + recheck["min_gain"]:
                    adopted = candidate
                    reason = "原声评分支持复听候选"
                else:
                    reason = "原声评分不足以支持改写"
        if "asr_recheck" in line:
            line["whisper_recheck"] = line["asr_recheck"]
        line["asr_recheck"] = {
            "reasons": [reason],
            **({"audio_logprob_per_char": list(scores)} if scores else {}),
            "model": MODEL,
            "original": original,
            "candidate": candidate,
            "similarity": round(similarity, 3),
            "adopted_text": adopted,
            "applied": adopted != original,
        }
        if adopted != original:
            line["text_asr"] = adopted
            line["text_neutral"] = neutralize(adopted, settings)
            applied += 1
    return {
        **identity,
        "status": "complete",
        "applied": applied,
        **counts,
        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
    }
