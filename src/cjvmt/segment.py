from __future__ import annotations

import re

from cjvmt.asr import asr_confidence
from cjvmt.config import Settings


def neutralize(text: str, settings: Settings) -> str:
    options = settings.values["segment"]
    target = options["neutralize_to"]
    return re.sub(
        re.escape(target) + "+",
        target,
        text.translate(
            str.maketrans(
                dict.fromkeys(
                    options["neutralize_chars"],
                    target,
                )
            )
        ),
    ).strip()


def split_sentences(groups: list[dict], direction: str, settings: Settings, duration_ms: int) -> list[dict]:
    opts = settings.values["segment"]
    lang = settings.values["asr"]["lang"][direction]
    context = settings.values["context"]
    lines: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        start = min(max(current[0]["start_ms"], 0), duration_ms)
        end = min(max(word["end_ms"] for word in current), duration_ms)
        text = "".join(word["text"] for word in current).strip()
        if text and end > start:
            prior = [line for line in lines if start - line["t_end_abs_ms"] <= context["prev_window_ms"]]
            turns = context["prev_turns"]
            quality = asr_confidence(current)
            lines.append(
                {
                    "line_id": f"L{len(lines) + 1:03}",
                    "t_start_abs_ms": start,
                    "t_end_abs_ms": end,
                    "text_asr": text,
                    "text_neutral": neutralize(text, settings),
                    "context": [line["text_neutral"] for line in prior[-turns:]] if turns else [],
                    "asr_warning": quality["warning"],
                    "asr_confidence": quality,
                }
            )
        current.clear()

    for group in groups:
        words = group.get("words") or [group]
        evidence = {key: group[key] for key in ("avg_logprob", "no_speech_prob", "hallucination") if key in group}
        for word in words:
            if current:
                size = len("".join(x["text"] for x in current) + word["text"])
                if (
                    word["start_ms"] - current[-1]["end_ms"] >= opts["pause_ms"]
                    or word["end_ms"] - current[0]["start_ms"] > opts["max_dur_ms"]
                    or size > opts["max_chars"][lang]
                ):
                    flush()
            current.append({**word, **evidence})
            if word["text"].rstrip().endswith(tuple(opts["final_punct"])):
                flush()
    flush()
    return lines
