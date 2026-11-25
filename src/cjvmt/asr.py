from __future__ import annotations

import os
import re
import unicodedata
from difflib import SequenceMatcher
from logging import getLogger
from pathlib import Path

import numpy as np

from cjvmt.config import Settings

LANGUAGE_DIRECTIONS = {"ja": "ja2zh", "zh": "zh2ja"}
LANGUAGE_UNCERTAIN = "未能确认中文或日语。请使用包含清晰人声的片段，或在高级设置中指定语向。"
NUMBER = re.compile(
    r"[0-9]+(?:[.,][0-9]+)*|[〇零一壱壹二弐贰貳两兩三参參叁四肆五伍六陸陆七柒八捌九玖十拾百佰千仟万萬亿億]+"
)
logger = getLogger(__name__)


def comparable_text(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def parse_hotwords(value: str, settings: Settings) -> tuple[str, ...]:
    limit = settings.values["asr"].get("recheck", {}).get("max_hotwords_chars", 160)
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"识别热词最多填写 {limit} 个字符。")
    return tuple(dict.fromkeys(word.strip() for word in re.split(r"[,，、;\n；]", value) if word.strip()))


def asr_confidence(groups: list[dict]) -> dict:
    return {
        "logprob": min((g["avg_logprob"] for g in groups if "avg_logprob" in g), default=None),
        "no_speech": max((g.get("no_speech_prob", 0) for g in groups), default=0),
        "warning": any(g.get("hallucination", False) for g in groups),
    }


def trusted_context(lines: list[dict], start_ms: int, end_key: str, settings: Settings) -> list[str]:
    context = settings.values["context"]
    options = settings.values["asr"]
    recheck = options.get("recheck")
    if not recheck:
        return []
    output = []
    for line in reversed(lines):
        if start_ms - line[end_key] > context["prev_window_ms"] or len(output) >= context["prev_turns"]:
            break
        quality = line.get("asr_confidence") or {}
        score = quality.get("logprob")
        if (
            score is not None
            and score >= recheck["trusted_logprob"]
            and not quality.get("warning")
            and quality["no_speech"] < options["halluc_no_speech_prob_max"]
        ):
            output.append(line["text_asr"])
    return output[::-1]


def revise_lines(
    asr,
    lines: list[dict],
    samples: np.ndarray,
    rate: int,
    direction: str,
    settings: Settings,
    hotwords: tuple[str, ...],
) -> None:
    from cjvmt.segment import neutralize

    recheck = getattr(asr, "recheck", None)
    if not recheck or rate != 16000:
        return
    context = settings.values["context"]
    language = settings.values["asr"]["lang"][direction]
    for index, line in enumerate(lines):
        start, end = line["t_start_abs_ms"], line["t_end_abs_ms"]
        previous = lines[max(0, index - context["prev_turns"]) : index]
        line["context"] = [
            row["text_neutral"] for row in previous if start - row["t_end_abs_ms"] <= context["prev_window_ms"]
        ]
        history = trusted_context(lines[:index], start, "t_end_abs_ms", settings)
        audio = samples[round(start * rate / 1000) : round(end * rate / 1000)].astype(np.float32) / 32768
        result = recheck(audio, language, line["text_asr"], line["asr_confidence"], history, hotwords)
        if result:
            line["asr_recheck"] = result
            if result["applied"]:
                line["text_asr"] = result["candidate"]
                line["text_neutral"] = neutralize(result["candidate"], settings)


class MockASR:
    model_id = "mock-asr"
    revision = "1"

    def detect_language(self, audio: Path | np.ndarray) -> tuple[str, float]:
        return "ja", 1.0

    def transcribe(self, audio: Path, language: str, *, duration_ms: int, offset_ms: int = 0) -> list[dict]:
        texts = (
            ("いいよ。", "田中さんも出ます？", "本当？！")
            if language == "ja"
            else ("好啊。", "你明天也去？", "真的吗？！")
        )
        if offset_ms or duration_ms < 3000:
            index = min(len(texts) - 1, offset_ms // 2700)
            intervals = [(0, duration_ms, texts[index])]
        else:
            intervals = [(600, 1900, texts[0]), (3300, 4800, texts[1]), (6000, 7000, texts[2])]
        return [
            {
                "start_ms": start,
                "end_ms": min(end, duration_ms),
                "text": text,
                "words": [{"start_ms": start, "end_ms": min(end, duration_ms), "text": text}],
                "avg_logprob": 0.0,
                "no_speech_prob": 0.0,
                "compression_ratio": 1.0,
                "hallucination": False,
            }
            for start, end, text in intervals
            if start < duration_ms
        ]


class WhisperASR:
    def __init__(self, settings: Settings):
        from faster_whisper import WhisperModel
        from faster_whisper.utils import _MODELS

        self.options = settings.values["asr"]
        self.model_id = _MODELS.get(self.options["model"], self.options["model"])
        self.revision = os.getenv("CJVMT_ASR_REVISION") or self.options.get("revision")
        local = os.getenv("CJVMT_ASR_PATH")
        if local:
            model_path = Path(local).expanduser()
            if not (model_path / "model.bin").is_file():
                raise ValueError("CJVMT_ASR_PATH 必须指向已有的 faster-whisper 权重目录。")
        else:
            from huggingface_hub import snapshot_download

            model_path = Path(snapshot_download(self.model_id, revision=self.revision, local_files_only=True))
            self.revision = model_path.name
        device = os.getenv("CJVMT_ASR_DEVICE", self.options["device"])
        compute = "int8" if device == "cpu" else self.options["compute_type"]
        self.device = device
        self.compute_type = compute
        self.warmed = False
        self.engine = WhisperModel(
            str(model_path), device=device, compute_type=compute, cpu_threads=self.options.get("cpu_threads", 4)
        )

    def warm(self, language: str) -> None:
        if self.warmed or self.device == "cpu":
            return
        audio = (0.05 * np.sin(np.linspace(0, 440 * 2 * np.pi, 16000))).astype(np.float32)
        groups, _ = self.engine.transcribe(
            audio,
            language=language,
            vad_filter=False,
            word_timestamps=True,
            beam_size=self.options["beam_size"],
            max_new_tokens=4,
        )
        list(groups)
        self.warmed = True

    def detect_language(self, audio: Path | np.ndarray) -> tuple[str, float]:
        if isinstance(audio, Path):
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import collect_chunks, get_speech_timestamps

            samples = decode_audio(str(audio))
            speech = get_speech_timestamps(samples)
            if not speech:
                return "", 0.0
            voiced, _ = collect_chunks(samples, speech)
            samples = np.concatenate(voiced)
        else:
            samples = audio.astype(np.float32) / 32768
        language, confidence, _ = self.engine.detect_language(audio=samples)
        return language, float(confidence)

    def transcribe(self, audio: Path, language: str, *, duration_ms: int, offset_ms: int = 0) -> list[dict]:
        del duration_ms, offset_ms
        return self._transcribe(audio, language, self.options)

    def transcribe_sentence(self, audio: Path, language: str, *, beam_size: int) -> list[dict]:
        return self._transcribe(
            audio,
            language,
            {**self.options, "beam_size": beam_size, "vad_filter": False, "word_timestamps": False, "temperature": 0.0},
        )

    def recheck(
        self,
        audio: Path | np.ndarray,
        language: str,
        original: str,
        quality: dict,
        context: list[str],
        hotwords: tuple[str, ...],
    ) -> dict | None:
        options = self.options.get("recheck")
        if not options or not (context or hotwords):
            return None
        score = quality["logprob"]
        silence = self.options["halluc_no_speech_prob_max"]
        if score is None or (quality["no_speech"] >= silence and score < self.options["halluc_avg_logprob_min"]):
            return None
        a = comparable_text(original)
        missing_terms = tuple(term for term in map(comparable_text, hotwords) if len(term) >= 2 and term not in a)
        if not a or (score >= options["low_logprob"] and not quality["warning"] and not missing_terms):
            return None
        prompt_parts = [*context, "、".join(hotwords) + "。"] if hotwords else context
        prompt = " ".join(prompt_parts)[-options["prompt_chars"] :]
        try:
            groups = self._transcribe(
                audio,
                language,
                {
                    **self.options,
                    "beam_size": options["beam_size"],
                    "vad_filter": False,
                    "word_timestamps": False,
                    "temperature": 0.0,
                },
                hotwords=hotwords,
                initial_prompt=prompt,
            )
        except RuntimeError as error:
            logger.warning("ASR recheck skipped after %s", type(error).__name__)
            return None
        candidate = "".join(group["text"] for group in groups).strip()
        alternative = asr_confidence(groups)
        original_nfkc, candidate_nfkc = (unicodedata.normalize("NFKC", text) for text in (original, candidate))
        b = comparable_text(candidate)
        comparison = SequenceMatcher(None, a, b, autojunk=False)
        similarity = comparison.ratio()
        candidate_score = alternative["logprob"]
        applied = False
        if (
            a != b
            and candidate_score is not None
            and alternative["no_speech"] < silence
            and not alternative["warning"]
            and options["min_coverage"] <= len(b) / len(a) <= 1 / options["min_coverage"]
            and NUMBER.findall(original_nfkc) == NUMBER.findall(candidate_nfkc)
        ):
            spans = [match.span() for term in missing_terms for match in re.finditer(re.escape(term), b)]
            changes = [edit for edit in comparison.get_opcodes() if edit[0] != "equal"]
            glossary_only = bool(spans) and all(
                operation == "replace"
                and old_end - old_start == new_end - new_start
                and any(start <= new_start < new_end <= end for start, end in spans)
                for operation, old_start, old_end, new_start, new_end in changes
            )
            applied = (
                similarity >= options["glossary_min_similarity"]
                and glossary_only
                and candidate_score >= score - options["hotword_score_drop"]
            ) or (similarity >= options["min_similarity"] and candidate_score >= score + options["min_gain"])
        return {
            "applied": applied,
            "original": original,
            "candidate": candidate,
            "initial_logprob": score,
            "candidate_logprob": candidate_score,
            "similarity": round(similarity, 3),
        }

    def _transcribe(
        self,
        audio: Path | np.ndarray,
        language: str,
        options: dict,
        *,
        hotwords: tuple[str, ...] = (),
        initial_prompt: str = "",
    ) -> list[dict]:
        keys = ("beam_size", "vad_filter", "word_timestamps", "condition_on_previous_text", "temperature")
        groups, _ = self.engine.transcribe(
            str(audio) if isinstance(audio, Path) else audio,
            language=language,
            hotwords="、".join(hotwords) or None,
            initial_prompt=initial_prompt or None,
            **{key: options[key] for key in keys if key in options},
        )
        rows = []
        for group in groups:
            rows.append(
                {
                    "start_ms": round(group.start * 1000),
                    "end_ms": round(group.end * 1000),
                    "text": group.text.strip(),
                    "words": [
                        {
                            "start_ms": round(word.start * 1000),
                            "end_ms": round(word.end * 1000),
                            "text": word.word,
                            "probability": float(word.probability),
                        }
                        for word in (group.words or ())
                    ],
                    "avg_logprob": float(group.avg_logprob),
                    "no_speech_prob": float(group.no_speech_prob),
                    "compression_ratio": float(group.compression_ratio),
                    "hallucination": group.compression_ratio > self.options["halluc_compression_ratio_max"]
                    or (
                        group.avg_logprob < self.options["halluc_avg_logprob_min"]
                        and group.no_speech_prob > self.options["halluc_no_speech_prob_max"]
                    ),
                }
            )
        return rows


def detect_direction(asr, audio: Path | np.ndarray, settings: Settings) -> dict | None:
    language, confidence = asr.detect_language(audio)
    if language in LANGUAGE_DIRECTIONS and confidence >= settings.values["asr"]["language_min_probability"]:
        return {"direction": LANGUAGE_DIRECTIONS[language], "language": language, "confidence": confidence}
    return None


def make_asr(settings: Settings, backend: str) -> MockASR | WhisperASR:
    if backend == "mock":
        return MockASR()
    if settings.values["asr"]["backend"] != "faster-whisper":
        raise ValueError("当前实现支持 faster-whisper 语音识别。")
    return WhisperASR(settings)
