import re
from functools import lru_cache

HONORIFIC = re.compile(r"いらっしゃ|おっしゃ|なさ[るりっ]|くださ|召し上が|ご覧にな|[おご][^。？！]{1,12}にな[りるっ]")
POLITE = re.compile(r"です|でした|でしょう|でございます|ます(?:[。？?！!、]|$)|ました|ません|ましょう")
TONE_ASIDE = re.compile(
    r"[（(][^）)]*(?:生气|开心|愤怒|惊讶|讽刺|犹豫|不耐烦|撒娇|兴奋|平静|悲伤|怒り|驚き|皮肉|嬉しそう)[^）)]*[）)]"
)


def is_question_ja(text: str) -> bool:
    return text.rstrip().rstrip("。 ").endswith(("か", "かい", "かな", "かしら", "？", "?"))


def is_question_zh(text: str) -> bool:
    return text.rstrip().rstrip("。 ").endswith(("吗", "么", "？", "?"))


def uses_nin(text: str) -> bool:
    return "您" in text


@lru_cache(maxsize=1)
def tagger():
    try:
        import unidic_lite
        from fugashi import Tagger

        return Tagger(f'-d "{unidic_lite.DICDIR}"')
    except ImportError:
        return None


def style_ja(text: str) -> str:
    if HONORIFIC.search(text):
        return "honorific"
    if POLITE.search(text):
        return "polite"
    analyzer = tagger()
    if analyzer is not None:
        return (
            "plain"
            if any(word.feature.pos1 in {"動詞", "形容詞", "形状詞", "助動詞"} for word in analyzer(text))
            else "none"
        )
    return "plain" if re.search(r"[だるくすたいいう]([。！？?!]+)?$", text) else "none"


def simplified_leak(text: str) -> int:
    count = 0
    for character in text:
        if not "\u3400" <= character <= "\u9fff":
            continue
        try:
            character.encode("gb2312")
        except UnicodeEncodeError:
            continue
        try:
            character.encode("cp932")
        except UnicodeEncodeError:
            count += 1
    return count


def inspect_text(text: str, direction: str, observation: dict | None = None) -> dict:
    result = {"emotion_aside": bool(TONE_ASIDE.search(text))}
    if direction == "zh2ja":
        result.update(question=is_question_ja(text), style=style_ja(text), simplified_leak=simplified_leak(text))
    else:
        result.update(question=is_question_zh(text), uses_nin=uses_nin(text))
    if observation:
        tone = observation.get("tone")
        result["tone_punctuation_warning"] = bool(
            (result["question"] and not text.rstrip().endswith(("?", "？")))
            or (tone in {"惊讶", "兴奋"} and not text.rstrip().endswith(("?", "？", "!", "！")))
        )
    return result
