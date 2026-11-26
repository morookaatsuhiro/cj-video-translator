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


CLAUSE_PUNCT = tuple("，,、；;：:")
NATURAL_END_CHARS = set("了呢吗吧啊呀的的是はがをにでてともねよ ")
SOFT_PAUSE_MS = 120


def _choose_soft_split(current: list[dict]) -> int:
    """Choose a natural split index inside `current` instead of chopping mid-word."""
    if len(current) < 2:
        return len(current)
    start = max(1, len(current) // 3)
    # 1. Prefer the rightmost clause punctuation inside the eligible window.
    for idx in range(len(current) - 1, start - 1, -1):
        if current[idx - 1]["text"].rstrip().endswith(CLAUSE_PUNCT):
            return idx
    # 2. Prefer the largest acoustic breath/pause >= SOFT_PAUSE_MS between words.
    best_idx, best_gap = None, SOFT_PAUSE_MS - 1
    for idx in range(start, len(current)):
        gap = current[idx]["start_ms"] - current[idx - 1]["end_ms"]
        if gap > best_gap:
            best_gap = gap
            best_idx = idx
    if best_idx is not None:
        return best_idx
    # 3. Prefer a natural clause-ending particle/boundary near the tail to avoid splitting nouns.
    tail_start = max(start, len(current) // 2)
    for idx in range(len(current) - 1, tail_start - 1, -1):
        piece = current[idx - 1]["text"].rstrip()
        if piece and piece[-1] in NATURAL_END_CHARS:
            return idx
    return len(current)


def split_sentences(groups: list[dict], direction: str, settings: Settings, duration_ms: int) -> list[dict]:
    opts = settings.values["segment"]
    lang = settings.values["asr"]["lang"][direction]
    context = settings.values["context"]
    lines: list[dict] = []
    current: list[dict] = []

    def emit(chunk: list[dict]) -> None:
        if not chunk:
            return
        start = min(max(chunk[0]["start_ms"], 0), duration_ms)
        end = min(max(word["end_ms"] for word in chunk), duration_ms)
        text = "".join(word["text"] for word in chunk).strip()
        if text and end > start:
            prior = [line for line in lines if start - line["t_end_abs_ms"] <= context["prev_window_ms"]]
            turns = context["prev_turns"]
            quality = asr_confidence(chunk)
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

    def flush(split_idx: int | None = None) -> None:
        if not current:
            return
        cut = len(current) if split_idx is None else max(1, min(split_idx, len(current)))
        emit(current[:cut])
        del current[:cut]

    for group in groups:
        words = group.get("words") or [group]
        evidence = {key: group[key] for key in ("avg_logprob", "no_speech_prob", "hallucination") if key in group}
        for word in words:
            if current:
                if word["start_ms"] - current[-1]["end_ms"] >= opts["pause_ms"]:
                    flush()
                else:
                    size = len("".join(x["text"] for x in current) + word["text"])
                    too_long = (
                        word["end_ms"] - current[0]["start_ms"] > opts["max_dur_ms"] or size > opts["max_chars"][lang]
                    )
                    if too_long:
                        flush(_choose_soft_split(current))
            current.append({**word, **evidence})
            if word["text"].rstrip().endswith(tuple(opts["final_punct"])):
                flush()
    flush()
    return lines
