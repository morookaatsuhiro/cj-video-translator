import numpy as np
import pytest

from cjvmt.checks import is_question_ja, is_question_zh, simplified_leak, style_ja, uses_nin
from cjvmt.clip import clip_window, gate_audio
from cjvmt.config import Settings
from cjvmt.segment import neutralize, split_sentences
from cjvmt.stats import bootstrap, holm, mcnemar, wilson


def test_neutralize():
    settings = Settings.load()
    assert neutralize("本当？！", settings) == "本当。"
    assert neutralize("いいよ。", settings) == "いいよ。"


def test_questions_and_styles():
    assert is_question_ja("本当？") and is_question_ja("行くか")
    assert not is_question_ja("本当だ。") and not is_question_ja("行くよ")
    assert is_question_zh("真的吗？") and is_question_zh("你去吗")
    assert not is_question_zh("好啊。") and not is_question_zh("你去呢")
    assert style_ja("お元気ですか？") in {"polite", "honorific"}
    assert style_ja("行くよ。") == "plain"
    assert style_ja("こんにちは。") == "none"
    assert simplified_leak("汉") == 1
    assert simplified_leak("漢") == 0


def test_window_and_stats():
    settings = Settings.load()
    assert clip_window(100, 900, 5000, settings.values["clip"]) == (0, 1900)
    assert mcnemar(10, 2) < 0.1
    assert wilson(5, 10)[0] < 0.5 < wilson(5, 10)[1]


@pytest.mark.parametrize(
    ("function", "positive", "negative"),
    [
        (
            is_question_ja,
            ["行くか。", "いいかい。", "本当かな", "来る？"],
            ["いいよ。", "行った。", "ありがとう", "本当だ"],
        ),
        (is_question_zh, ["你走吗。", "真的么", "下雨了？", "你知道吗"], ["走吧。", "好了。", "多么美丽", "谢谢"]),
        (uses_nin, ["您好。", "您来了吗？", "请您先坐。", "劳驾您看一下。"], ["你好", "你们来吧", "请坐", "劳驾"]),
        (simplified_leak, ["汉", "这", "说", "车"], ["漢", "車", "言う", "今日"]),
    ],
)
def test_rules(function, positive, negative):
    assert all(function(text) for text in positive)
    assert not any(function(text) for text in negative)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("先生はいらっしゃいます。", "honorific"),
        ("何とおっしゃいましたか。", "honorific"),
        ("どうぞ召し上がってください。", "honorific"),
        ("もうご覧になりましたか。", "honorific"),
        ("今日は雨です。", "polite"),
        ("明日行きます。", "polite"),
        ("これは知りません。", "polite"),
        ("昨日でした。", "polite"),
        ("明日行く。", "plain"),
        ("今日は雨だ。", "plain"),
        ("魚を食べる。", "plain"),
        ("楽しかった。", "plain"),
        ("東京駅。", "none"),
        ("こんにちは。", "none"),
        ("来週。", "none"),
        ("黄色。", "none"),
    ],
)
def test_style_cases(text, expected):
    assert style_ja(text) == expected


def test_segment_boundaries_and_context():
    settings = Settings.load()
    words = [(100, 400, "你明天也去？"), (500, 900, "我也去"), (1600, 2000, "一起走。")]
    groups = [{"words": [{"start_ms": a, "end_ms": b, "text": text} for a, b, text in words], "hallucination": True}]
    lines = split_sentences(groups, "zh2ja", settings, 3000)
    assert [line["text_asr"] for line in lines] == [text for _, _, text in words]
    assert lines[-1]["context"] == ["你明天也去。", "我也去"]
    assert lines[-1]["asr_warning"]


def test_segment_prefers_comma_or_pause_instead_of_chopping_words():
    settings = Settings.load()
    # 34 Chinese chars without final punctuation, with a comma after char 14 and a breath pause before "现在"
    tokens = [
        (0, 800, "大家好"),
        (810, 1200, "，"),
        (1220, 2600, "这是我的中日多模态视频翻译系统展示"),
        (2850, 3600, "加上声音和画面"),
        (3620, 4200, "以后"),
        (4250, 5000, "翻译是不是更自然"),
    ]
    groups = [{"words": [{"start_ms": a, "end_ms": b, "text": text} for a, b, text in tokens]}]
    lines = split_sentences(groups, "zh2ja", settings, 6000)
    assert len(lines) >= 2
    # Must NOT split inside "以后" — should break cleanly at the comma or the 250ms pause!
    assert all(not line["text_asr"].endswith("以") and not line["text_asr"].startswith("后") for line in lines)


def test_gate_and_stats():
    gated, (start, end) = gate_audio(np.full(300, 1000), 1000, 0, 400, (100, 200), 10)
    assert len(gated) == 400 and (start, end) == (100, 200)
    assert not gated[:start].any() and not gated[end:].any()
    assert gated[start] == 0 and gated[end - 1] == 0 and gated[150] == 1000
    assert mcnemar(10, 2) == 158 / 4096
    assert holm([0.04, 0.01, 0.03]) == [0.06, 0.03, 0.06]
    assert wilson(0, 0) is None
    assert bootstrap([1, 2, 3], iterations=500, seed=2026) == bootstrap([1, 2, 3], iterations=500, seed=2026)
