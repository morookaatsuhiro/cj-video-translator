from pathlib import Path
from subprocess import CalledProcessError

import pytest

from cjvmt.asr import WhisperASR, parse_hotwords, trusted_context
from cjvmt.config import Settings
from cjvmt.qwen_asr import revise
from cjvmt.store import read_json, write_json


@pytest.mark.parametrize(
    ("original", "candidate", "score", "candidate_score", "terms", "applied"),
    [
        ("我来自河南省南阳市汤鹤县。", "我来自河南省南阳市唐河县。", -0.23, -0.28, ("唐河县",), True),
        ("我来自河南省汤鹤县，欢迎大家。", "唐河县，欢迎大家。", -0.85, -0.23, ("唐河县",), False),
        ("では本日の参加人数は17名です。", "では本日の参加人数は16名です。", -0.90, -0.25, (), False),
        ("では本日の参加人数は十七名です。", "では本日の参加人数は十六名です。", -0.90, -0.25, (), False),
        ("明日の会議は東亰で開かれます。", "明日の会議は東京で開かれます。", -0.85, -0.45, (), True),
        ("明日の会議は東亰で開かれます。", "明日の会議は東京で開かれます。", -0.85, -0.80, (), False),
    ],
)
def test_asr_recheck_guards(monkeypatch, original, candidate, score, candidate_score, terms, applied):
    calls = []

    def decode(audio, language, options, **hints):
        calls.append(hints)
        return [{"text": candidate, "avg_logprob": candidate_score, "no_speech_prob": 0.1}]

    asr = WhisperASR.__new__(WhisperASR)
    asr.options = Settings.load().values["asr"]
    monkeypatch.setattr(asr, "_transcribe", decode)
    source = Path("sentence.wav")
    quality = {"logprob": score, "warning": False, "no_speech": 0.1}
    assert asr.recheck(source, "ja", original, quality, [], ()) is None
    assert calls == []
    result = asr.recheck(source, "ja", original, quality, ["会議の日程です。"], terms)
    prompt = "会議の日程です。"
    if terms:
        prompt += " " + "、".join(terms) + "。"
    assert calls == [{"hotwords": terms, "initial_prompt": prompt}]
    assert result["applied"] is applied and result["original"] == original


def test_asr_only_uses_recent_reliable_context():
    settings = Settings.load()
    at = settings.values["context"]["prev_window_ms"] + 500
    lines = [
        {
            "end_ms": end,
            "text_asr": word,
            "asr_confidence": {"logprob": score, "warning": False, "no_speech": 0.1},
        }
        for end, word, score in ((0, "之前的话", -0.3), (at - 400, "可信前文", -0.3), (at - 200, "低置信度", -0.9))
    ]
    assert trusted_context(lines, at, "end_ms", settings) == ["可信前文"]
    assert parse_hotwords("唐河县、 東京，唐河县", settings) == ("唐河县", "東京")
    with pytest.raises(ValueError, match="最多填写"):
        parse_hotwords("词" * 161, settings)


def test_qwen_asr_candidates_preserve_timing_and_numbers(tmp_path, monkeypatch):
    raw = ("我来自于河南省奈阳市汤鹤县。", "今日の会議は17名です。")
    lines = [
        {
            "t_start_abs_ms": index * 3000,
            "t_end_abs_ms": index * 3000 + 2500,
            "text_asr": text,
            "text_neutral": text,
            "asr_confidence": {"logprob": -0.26, "warning": False, "no_speech": 0.2},
        }
        for index, text in enumerate(raw)
    ]

    def worker(args, **kwargs):
        request = read_json(Path(args[-2]))
        assert request["language"] == "zh" and request["lines"][1]["start_ms"] == 3000
        assert request["lines"][0]["prompt"] == "唐河县"
        write_json(Path(args[-1]), ["我来自于河南省南阳市唐河县。", "今日の会議は16名です。"])

    monkeypatch.setattr("cjvmt.qwen_asr.subprocess.run", worker)
    status = revise(tmp_path, lines, "zh2ja", Settings.load(), ("唐河县",))
    assert status["status"] == "complete" and status["applied"] == 1
    assert lines[0]["text_asr"] == "我来自于河南省南阳市唐河县。"
    assert lines[0]["asr_recheck"]["original"] == raw[0]
    assert lines[1]["text_asr"] == raw[1] and lines[1]["t_end_abs_ms"] == 5500
    assert lines[1]["context"] == [lines[0]["text_neutral"]]

    def failed_worker(args, **kwargs):
        raise CalledProcessError(1, args)

    monkeypatch.setattr("cjvmt.qwen_asr.subprocess.run", failed_worker)
    assert revise(tmp_path, lines, "zh2ja", Settings.load(), ())["status"] == "fallback"
