from pathlib import Path
from subprocess import CalledProcessError
from types import SimpleNamespace

import numpy as np
import pytest

from cjvmt.asr import WhisperASR, parse_hotwords, trusted_context
from cjvmt.config import Settings
from cjvmt.qwen_asr import revise
from cjvmt.store import read_json, write_json


@pytest.mark.parametrize(
    ("original", "candidate", "score", "candidate_score", "terms", "applied"),
    [
        ("我来自河南省南阳市汤鹤县。", "我来自河南省南阳市唐河县。", -0.23, -0.28, ("唐河县",), False),
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


@pytest.mark.parametrize("score_gain,applied", [(0.3, True), (0.04, False)])
def test_blind_asr_adoption_uses_same_audio_not_terms(tmp_path, monkeypatch, score_gain, applied):
    from dataclasses import replace

    from cjvmt.config import parse_glossary

    settings = replace(Settings.load(), glossary=parse_glossary("纸箱 | 段ボール"))
    raw = ["请把货架旁边的纸相先搬走。", "这里有17个箱子。", "明天照常出发。", "请把纸相送到楼下。"]
    alternatives = ["请把货架旁边的纸箱先搬走。", "这里有16个箱子。", raw[2], "前面无端补入一段话。请把纸箱送到楼下。"]
    rows = [
        dict(text_asr=t, text_neutral=t, t_start_abs_ms=i * 1000, t_end_abs_ms=(i + 1) * 1000)
        for i, t in enumerate(raw)
    ]
    samples = np.arange(64000, dtype=np.int16)

    def worker(args, **kwargs):
        request = read_json(Path(args[-2]))
        assert all(line["prompt"] is None for line in request["lines"])
        assert len(request["lines"]) == 4
        write_json(Path(args[-1]), alternatives)

    def score(audio, language, texts):
        np.testing.assert_array_equal(audio, samples[:16000])
        assert language == "zh" and texts[0] != texts[1]
        return -0.6, -0.6 + score_gain

    asr = SimpleNamespace(score_transcripts=score)
    monkeypatch.setattr("cjvmt.qwen_asr.subprocess.run", worker)
    status = revise(tmp_path, rows, "zh2ja", settings, asr, samples, 16000)
    assert status["applied"] == int(applied) and status["selected"] == 4
    assert [r["text_asr"] for r in rows] == [alternatives[0] if applied else raw[0], *raw[1:]]
    assert rows[0]["asr_recheck"]["original"] == raw[0]
    assert rows[1]["context"] == [rows[0]["text_asr"]]

    def fail(*args, **kwargs):
        raise CalledProcessError(1, args)

    monkeypatch.setattr("cjvmt.qwen_asr.subprocess.run", fail)
    assert revise(tmp_path, rows, "zh2ja", settings, asr, samples, 16000)["status"] == "fallback"
