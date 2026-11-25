from __future__ import annotations

import random
import re
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from cjvmt.backends import Backend, Request
from cjvmt.config import Settings, file_hash, stable_hash
from cjvmt.store import read_json, write_json

CONDITIONS = ("T_neutral", "T_asr", "T+A", "T+V", "T+V+A", "T+A_shuf", "T+V_shuf", "T+V+A_sep")
FOUR = ("T_neutral", "T+A", "T+V", "T+V+A")


def observation_data(text: str, settings: Settings) -> dict:
    last = next((line for line in text.splitlines() if re.match(r"\s*(?:[*`]+\s*)?4[.．、：:)]", line)), "")
    clean = re.sub(r"[*` ]", "", last)
    labels = "|".join(map(re.escape, settings.values["tone"]["labels"]))
    tones = re.findall(
        rf"(?:[.．、：:,，；;]|^)(?:说话人的语气[:：]|语气[:：])?({labels})(?=[，,；;。/、）)（(]|$)", clean
    )
    strengths = re.findall(r"(?:强度[:：]|[，,；;（(])([弱中强])(?=[，,；;。/、）)]|$)", clean)
    tone = tones[0] if len(set(tones)) == 1 else "不确定"
    return {
        "text": text,
        "tone": tone,
        "strength": strengths[0] if len(set(strengths)) == 1 and tone != "不确定" else None,
        "verified": False,
    }


def prompts_for(
    settings: Settings,
    direction: str,
    mode: str,
    condition: str,
    source: str,
    context: list[str],
    observation: dict | None = None,
) -> tuple[str, str]:
    templates = settings.prompts
    template = templates["translate_line"]
    language = templates["lang_names"][direction]
    note = templates["modality_note"][condition]
    if mode == "guided" and condition in settings.values["prompt_modes"]["guided_applies_to"] + ["T+V+A_sep"]:
        note += templates["guide_note"]
    if mode != "compare" and settings.values["tone"]["text_level"]:
        tone = observation and observation["tone"] != "不确定" and observation["tone"]
        strength = observation and observation["strength"]
        template_note = templates["tone_note"]
        if not strength:
            template_note = template_note.replace("，{strength}", "")
        note += template_note.format(tone=tone or "自然", strength=strength or "")
    context_block = template["context_block"].format(context_lines="\n".join(context)) if context else ""
    user = template["user"].format(**language, context_block=context_block, modality_note=note, text=source)
    if observation:
        user = templates["perceive"]["translate_prefix"].format(observation=observation["text"]) + user
    return template["system"].format(**language, data_guard=templates["data_guard"]), user


def select_media(condition: str, clip: Path, line: dict, other: tuple[Path, dict] | None = None) -> dict:
    if condition.endswith("_shuf") and other is None:
        raise ValueError(f"{condition} 需要来自另一部视频的片段。")
    if condition.endswith("_shuf"):
        clip, line = other
    paths = line["clip"]
    if condition == "T+V+A":
        return {"video": clip / paths["path_av"], "interleaved": True}
    media = {}
    if condition in {"T+V", "T+V_shuf", "T+V+A_sep"}:
        media["video"] = clip / paths["path_v"]
    if condition in {"T+A", "T+A_shuf", "T+V+A_sep"}:
        media["audio"] = clip / paths["path_audio_gated"]
    return media


class Translator:
    def __init__(self, settings: Settings, backend: Backend):
        self.settings = settings
        self.backend = backend
        self.decode = settings.values["decode"]
        self.video = settings.values["video_input"]

    def translate_clip(
        self,
        line: dict,
        clip_root: Path,
        *,
        item_id: str,
        direction: str,
        mode: str,
        condition: str,
        mismatch: tuple[Path, dict] | None = None,
    ) -> tuple[dict, list[dict]]:
        return self.line(
            item_id=item_id,
            direction=direction,
            mode=mode,
            condition=condition,
            source=line["text_asr"] if condition == "T_asr" else line["text_neutral"],
            context=line["context"],
            media=select_media(condition, clip_root, line, mismatch),
        )

    def infer(self, request: Request, item_id: str) -> dict:
        prompt_sha256 = stable_hash([request.system, request.user])
        media = {
            "audio": file_hash(request.audio) if request.audio else None,
            "video": file_hash(request.video) if request.video else None,
            "frames": [file_hash(frame) for frame in request.frames],
        }
        decode = {"max_new_tokens": request.max_tokens, "do_sample": self.decode["do_sample"]}
        key = stable_hash(
            {
                "backend": self.backend.identity,
                "condition": request.condition,
                "item": item_id,
                "task": request.task,
                "prompt": prompt_sha256,
                "templates": stable_hash(self.settings.prompts),
                "media": media,
                "decode": decode,
                "fps": request.fps,
                "max_pixels": request.max_pixels,
                "interleaved": request.interleaved,
            }
        )
        cache = self.settings.path("cache") / "translations" / f"{key}.json"
        start = perf_counter()
        cached = cache.is_file()
        if cached:
            result = read_json(cache)
        else:
            result = self.backend.generate(request)
            elapsed = round((perf_counter() - start) * 1000, 3)
            result = {**result, "inference_ms": elapsed}
            write_json(cache, result)
        return {
            **result,
            "cache_hit": cached,
            "elapsed_ms": round((perf_counter() - start) * 1000, 3),
            "cache_key": key,
            "prompt_sha256": prompt_sha256,
            "condition": request.condition,
            "task": request.task,
            "use_audio_in_video": request.interleaved,
            "media_sha256": media,
            "decode": decode,
            "media_types": [kind for kind, value in media.items() if value],
            "video_input": {"fps": request.fps, "max_pixels": request.max_pixels},
        }

    def observe(self, item_id: str, **inputs) -> tuple[dict, dict]:
        template = self.settings.prompts["perceive"]
        request = Request(
            system=template["system"].format(data_guard=self.settings.prompts["data_guard"]),
            user=template["user"],
            task="observe",
            max_tokens=self.settings.values["prompt_modes"]["perceive_max_new_tokens"],
            **inputs,
        )
        prediction = self.infer(request, item_id)
        return observation_data(prediction["text"], self.settings), prediction

    def line(
        self,
        *,
        item_id: str,
        direction: str,
        mode: str,
        condition: str,
        source: str,
        context: list[str],
        media: dict,
        fps: float | None = None,
    ) -> tuple[dict, list[dict]]:
        observation = None
        predictions = []
        common = {
            "condition": condition,
            "source": source,
            "direction": direction,
            "fps": fps or self.video["fps"],
            "max_pixels": self.video["max_pixels"],
            **media,
        }
        if mode == "perceive" and media:
            observation, prediction = self.observe(item_id, **common)
            predictions.append(prediction)
        system, user = prompts_for(self.settings, direction, mode, condition, source, context, observation)
        prediction = self.infer(
            Request(system=system, user=user, max_tokens=self.decode["max_new_tokens"]["line"], **common), item_id
        )
        predictions.append(prediction)
        text = next((line.strip() for line in prediction["text"].splitlines() if line.strip()), "")
        if not text:
            raise RuntimeError(f"{condition} 返回了空译文。")
        return {"text": text, "observation": observation}, predictions

    def document(
        self, *, item_id: str, direction: str, condition: str, lines: list[str], media: dict
    ) -> tuple[list[str], list[dict], int]:
        templates = self.settings.prompts
        doc = templates["translate_doc"]
        language = templates["lang_names"][direction]
        replacements = {
            **language,
            "modality_note": templates["modality_note"][condition],
            "n": len(lines),
            "numbered_lines": "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1)),
        }
        request = Request(
            system=doc["system"].format(**language, data_guard=templates["data_guard"]),
            user="",
            condition=condition,
            direction=direction,
            source="\n".join(lines),
            max_tokens=self.decode["max_new_tokens"]["doc"],
            fps=self.video["doc_fps"],
            max_pixels=self.video["doc_max_pixels"],
            task="document",
            **media,
        )
        logs = []
        for attempt in range(self.decode["doc_retry"] + 1):
            template = doc["retry_user"] if attempt else doc["user"]
            result = self.infer(Request(**{**asdict(request), "user": template.format(**replacements)}), item_id)
            logs.append(result)
            parsed = parse_document(result["text"], len(lines))
            if parsed is not None:
                return parsed, logs, 0
        outputs = []
        for index, source in enumerate(lines, 1):
            fallback = {
                **asdict(request),
                "user": doc["line_fallback_user"].format(**replacements, k=index),
                "max_tokens": (
                    self.decode["max_new_tokens"]["doc"] if len(lines) == 1 else self.decode["max_new_tokens"]["line"]
                ),
                "task": "fallback",
                "source": source,
            }
            result = self.infer(Request(**fallback), f"{item_id}:{index}")
            logs.append({**result, "fallback": True})
            outputs.append(next((line.strip() for line in result["text"].splitlines() if line.strip()), ""))
        return outputs, logs, len(outputs)


def parse_document(text: str, count: int) -> list[str] | None:
    matches = [re.fullmatch(r"\s*(\d+)[.．、)]\s*(\S.*?)\s*", line) for line in text.splitlines() if line.strip()]
    if len(matches) != count or any(match is None or int(match[1]) != i for i, match in enumerate(matches, 1)):
        return None
    return [match[2] for match in matches]


def choose_mismatch(source_hash: str, candidates: list[tuple[Path, dict, str]], seed: int) -> tuple[Path, dict, str]:
    allowed = [item for item in candidates if item[2] != source_hash]
    if not allowed:
        raise ValueError("错配条件至少需要两部不同视频。")
    return random.Random(f"{seed}:{source_hash}").choice(allowed)
