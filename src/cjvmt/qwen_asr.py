from __future__ import annotations

import os
import subprocess
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from cjvmt.asr import NUMBER, comparable_text, trusted_context
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


def revise(run: Path, lines: list[dict], direction: str, settings: Settings, hotwords: tuple[str, ...]) -> dict:
    python, snapshot = runtime()
    receipt = snapshot / "revision.txt"
    identity = {"model": MODEL, "revision": receipt.read_text().strip() if receipt.is_file() else snapshot.name}
    recheck = settings.values["asr"]["recheck"]
    requests = []
    for index, line in enumerate(lines):
        start, end = line["t_start_abs_ms"], line["t_end_abs_ms"]
        history = trusted_context(lines[:index], start, "t_end_abs_ms", settings)
        prompt = (" ".join(history)[-recheck["prompt_chars"] :] + " " + "、".join(hotwords)).strip()
        requests.append({"start_ms": start, "end_ms": end, "prompt": prompt or None})

    started = perf_counter()
    try:
        with TemporaryDirectory(prefix=".qwen-asr-", dir=run) as directory:
            request = Path(directory) / "request.json"
            output = Path(directory) / "output.json"
            write_json(
                request,
                {
                    "audio": str(run / "media/audio.wav"),
                    "language": settings.values["asr"]["lang"][direction],
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
                    timeout=120,
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
        return {**identity, "status": "fallback"}

    applied = 0
    context = settings.values["context"]
    for index, (line, candidate) in enumerate(zip(lines, candidates, strict=True)):
        original = line["text_asr"]
        first, second = map(comparable_text, (original, candidate))
        similarity = SequenceMatcher(None, first, second, autojunk=False).ratio()
        old_numbers, new_numbers = (
            NUMBER.findall(unicodedata.normalize("NFKC", text)) for text in (original, candidate)
        )
        use_candidate = (
            first != second
            and similarity >= recheck["glossary_min_similarity"]
            and recheck["min_coverage"] * len(first) <= len(second) <= len(first) / recheck["min_coverage"]
            and old_numbers == new_numbers
        )
        line["asr_recheck"] = {
            "model": MODEL,
            "original": original,
            "candidate": candidate,
            "similarity": round(similarity, 3),
            "applied": use_candidate,
        }
        previous = lines[max(0, index - context["prev_turns"]) : index]
        line["context"] = [
            row["text_neutral"]
            for row in previous
            if line["t_start_abs_ms"] - row["t_end_abs_ms"] <= context["prev_window_ms"]
        ]
        if use_candidate:
            line["text_asr"] = candidate
            line["text_neutral"] = neutralize(candidate, settings)
            applied += 1
    return {
        **identity,
        "status": "complete",
        "applied": applied,
        "total": len(lines),
        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
    }
