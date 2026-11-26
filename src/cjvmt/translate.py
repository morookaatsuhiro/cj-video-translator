from __future__ import annotations

import json
import random
import re
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from cjvmt.backends import Backend, Request
from cjvmt.config import Settings, file_hash, stable_hash
from cjvmt.media import has_visual_detail
from cjvmt.store import read_json, write_json

CONDITIONS = ("T_neutral", "T_asr", "T+A", "T+V", "T+V+A", "T+A_shuf", "T+V_shuf", "T+V+A_sep")
FOUR = ("T_neutral", "T+A", "T+V", "T+V+A")


def model_json_object(text: str) -> dict:
    """Accept a single JSON object, optionally inside one Markdown code block."""
    if not isinstance(text, str):
        return {}
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, re.DOTALL | re.IGNORECASE)
    try:
        obj = json.loads(fence[1] if fence else text)
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


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
    extra_note: str = "",
    visual_objects: bool = False,
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
    if visual_objects and mode in {"guided", "perceive"}:
        note += templates["visual_object_note"]
    note += settings.terminology_note(source)
    if mode in {"guided", "perceive"}:
        note += templates["unfinished_note"]
    if extra_note:
        note += extra_note
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

    def _cache_entry(self, request: Request, item_id: str) -> tuple[str, str, dict, dict, Path]:
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
        return key, prompt_sha256, media, decode, self.settings.path("cache") / "translations" / f"{key}.json"

    def _wrap_prediction(
        self,
        request: Request,
        result: dict,
        cached: bool,
        elapsed_ms: float,
        key: str,
        prompt_sha256: str,
        media: dict,
        decode: dict,
    ) -> dict:
        return {
            **result,
            "cache_hit": cached,
            "elapsed_ms": elapsed_ms,
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

    def infer(self, request: Request, item_id: str) -> dict:
        key, prompt_sha256, media, decode, cache = self._cache_entry(request, item_id)
        start = perf_counter()
        cached = cache.is_file()
        if cached:
            result = read_json(cache)
        else:
            result = self.backend.generate(request)
            elapsed = round((perf_counter() - start) * 1000, 3)
            result = {**result, "inference_ms": elapsed}
            write_json(cache, result)
        return self._wrap_prediction(
            request, result, cached, round((perf_counter() - start) * 1000, 3), key, prompt_sha256, media, decode
        )

    def infer_batch(self, items: list[tuple[Request, str]]) -> list[dict]:
        if not items:
            return []
        if len(items) == 1:
            return [self.infer(*items[0])]
        entries = [self._cache_entry(request, item_id) for request, item_id in items]
        results: list[dict | None] = [None] * len(items)
        miss_indices: list[int] = []
        miss_requests: list[Request] = []
        for idx, ((request, _), (key, prompt_sha256, media, decode, cache)) in enumerate(
            zip(items, entries, strict=True)
        ):
            if cache.is_file():
                start = perf_counter()
                cached_result = read_json(cache)
                results[idx] = self._wrap_prediction(
                    request,
                    cached_result,
                    True,
                    round((perf_counter() - start) * 1000, 3),
                    key,
                    prompt_sha256,
                    media,
                    decode,
                )
            else:
                miss_indices.append(idx)
                miss_requests.append(request)
        if miss_requests:
            start = perf_counter()
            if hasattr(self.backend, "generate_batch"):
                generated = self.backend.generate_batch(miss_requests)
            else:
                generated = [self.backend.generate(req) for req in miss_requests]
            batch_elapsed = round((perf_counter() - start) * 1000, 3)
            per_item_ms = round(batch_elapsed / max(1, len(miss_requests)), 3)
            for idx, raw_result in zip(miss_indices, generated, strict=True):
                request = items[idx][0]
                key, prompt_sha256, media, decode, cache = entries[idx]
                stored = {**raw_result, "inference_ms": per_item_ms, "batch_elapsed_ms": batch_elapsed}
                write_json(cache, stored)
                results[idx] = self._wrap_prediction(
                    request, stored, False, per_item_ms, key, prompt_sha256, media, decode
                )
        return [item for item in results if item is not None]

    def observe(
        self, item_id: str, *, visual_objects: bool = False, blank_visual: bool = False, **inputs
    ) -> tuple[dict, dict]:
        template = self.settings.prompts["perceive"]
        modes = self.settings.values["prompt_modes"]
        user = template["user"]
        if blank_visual:
            user += self.settings.prompts["blank_visual_note"]
        if visual_objects:
            if not inputs.get("audio") and not inputs.get("interleaved"):
                user += "随附画面无声，第 1 项写“不确定”。\n"
            user += template["object_question"].format(source=inputs["source"])
        request = Request(
            system=template["system"].format(data_guard=self.settings.prompts["data_guard"]),
            user=user,
            task="observe",
            max_tokens=modes["object_observation_max_tokens"] if visual_objects else modes["perceive_max_new_tokens"],
            **inputs,
        )
        prediction = self.infer(request, item_id)
        observation = observation_data(prediction["text"], self.settings)
        if visual_objects:
            evidence = re.findall(r"^\s*5[.．、：:)]\s*(.+)$", prediction["text"], re.MULTILINE)
            if len(evidence) == 1:
                observation["object_evidence"] = evidence[0].strip()
        return observation, prediction

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
        extra_note: str = "",
    ) -> tuple[dict, list[dict]]:
        observation = None
        predictions = []
        inspect_objects = (
            mode in {"guided", "perceive"}
            and self.settings.values["prompt_modes"].get("object_grounding", False)
            and bool(media.get("video") or media.get("frames"))
        )
        frame_rate = fps or self.video["fps"]
        usable_visual = inspect_objects and has_visual_detail(
            self.settings, media.get("video"), media.get("frames", ()), frame_rate
        )
        blank_visual = inspect_objects and not usable_visual
        if blank_visual:
            extra_note += self.settings.prompts["blank_visual_note"]
        common = {
            "condition": condition,
            "source": source,
            "direction": direction,
            "fps": frame_rate,
            "max_pixels": self.video["max_pixels"],
            **media,
        }
        if mode == "perceive" and media:
            if blank_visual and not media.get("audio") and not media.get("interleaved"):
                observation = observation_data("", self.settings)
            else:
                observation, prediction = self.observe(
                    item_id, visual_objects=usable_visual, blank_visual=blank_visual, **common
                )
                predictions.append(prediction)
            if blank_visual:
                observation["object_evidence"] = "取样画面近乎纯色，无法辨认物体。"
        system, user = prompts_for(
            self.settings,
            direction,
            mode,
            condition,
            source,
            context,
            observation,
            extra_note,
            visual_objects=usable_visual,
        )
        prediction = self.infer(
            Request(system=system, user=user, max_tokens=self.decode["max_new_tokens"]["line"], **common), item_id
        )
        predictions.append(prediction)
        text = next((line.strip() for line in prediction["text"].splitlines() if line.strip()), "")
        if not text:
            raise RuntimeError(f"{condition} 返回了空译文。")
        return {"text": text, "observation": observation}, predictions

    def _observe_request(self, *, visual_objects: bool = False, blank_visual: bool = False, **inputs) -> Request:
        template = self.settings.prompts["perceive"]
        modes = self.settings.values["prompt_modes"]
        user = template["user"]
        if blank_visual:
            user += self.settings.prompts["blank_visual_note"]
        if visual_objects:
            if not inputs.get("audio") and not inputs.get("interleaved"):
                user += "随附画面无声，第 1 项写“不确定”。\n"
            user += template["object_question"].format(source=inputs["source"])
        return Request(
            system=template["system"].format(data_guard=self.settings.prompts["data_guard"]),
            user=user,
            task="observe",
            max_tokens=modes["object_observation_max_tokens"] if visual_objects else modes["perceive_max_new_tokens"],
            **inputs,
        )

    def _parse_observation(self, text: str, *, visual_objects: bool = False) -> dict:
        observation = observation_data(text, self.settings)
        if visual_objects:
            evidence = re.findall(r"^\s*5[.．、：:)]\s*(.+)$", text, re.MULTILINE)
            if len(evidence) == 1:
                observation["object_evidence"] = evidence[0].strip()
        return observation

    def lines_batch(self, jobs: list[dict]) -> list[tuple[dict, list[dict]]]:
        """Batch multiple line translations across vLLM-Omni (observe wave -> translate wave)."""
        if not jobs:
            return []
        if len(jobs) == 1:
            return [self.line(**jobs[0])]
        prepared = []
        obs_batch_items: list[tuple[Request, str]] = []
        obs_targets: list[int] = []
        for idx, job in enumerate(jobs):
            mode = job["mode"]
            media = job["media"]
            frame_rate = job.get("fps") or self.video["fps"]
            inspect_objects = (
                mode in {"guided", "perceive"}
                and self.settings.values["prompt_modes"].get("object_grounding", False)
                and bool(media.get("video") or media.get("frames"))
            )
            usable_visual = inspect_objects and has_visual_detail(
                self.settings, media.get("video"), media.get("frames", ()), frame_rate
            )
            blank_visual = inspect_objects and not usable_visual
            extra_note = job.get("extra_note", "")
            if blank_visual:
                extra_note += self.settings.prompts["blank_visual_note"]
            common = {
                "condition": job["condition"],
                "source": job["source"],
                "direction": job["direction"],
                "fps": frame_rate,
                "max_pixels": self.video["max_pixels"],
                **media,
            }
            observation = None
            if mode == "perceive" and media:
                if blank_visual and not media.get("audio") and not media.get("interleaved"):
                    observation = observation_data("", self.settings)
                    observation["object_evidence"] = "取样画面近乎纯色，无法辨认物体。"
                else:
                    req = self._observe_request(visual_objects=usable_visual, blank_visual=blank_visual, **common)
                    obs_batch_items.append((req, job["item_id"]))
                    obs_targets.append(idx)
            prepared.append(
                {
                    "job": job,
                    "common": common,
                    "usable_visual": usable_visual,
                    "blank_visual": blank_visual,
                    "extra_note": extra_note,
                    "observation": observation,
                    "predictions": [],
                }
            )
        if obs_batch_items:
            obs_preds = self.infer_batch(obs_batch_items)
            for idx, pred in zip(obs_targets, obs_preds, strict=True):
                state = prepared[idx]
                obs = self._parse_observation(pred["text"], visual_objects=state["usable_visual"])
                if state["blank_visual"]:
                    obs["object_evidence"] = "取样画面近乎纯色，无法辨认物体。"
                state["observation"] = obs
                state["predictions"].append(pred)
        trans_batch_items: list[tuple[Request, str]] = []
        for state in prepared:
            job = state["job"]
            system, user = prompts_for(
                self.settings,
                job["direction"],
                job["mode"],
                job["condition"],
                job["source"],
                job["context"],
                state["observation"],
                state["extra_note"],
                visual_objects=state["usable_visual"],
            )
            req = Request(system=system, user=user, max_tokens=self.decode["max_new_tokens"]["line"], **state["common"])
            trans_batch_items.append((req, job["item_id"]))
        trans_preds = self.infer_batch(trans_batch_items)
        outputs: list[tuple[dict, list[dict]]] = []
        for state, pred in zip(prepared, trans_preds, strict=True):
            state["predictions"].append(pred)
            text = next((line.strip() for line in pred["text"].splitlines() if line.strip()), "")
            if not text:
                raise RuntimeError(f"{state['job']['condition']} 返回了空译文。")
            outputs.append(({"text": text, "observation": state["observation"]}, state["predictions"]))
        return outputs

    def refine_boundary(
        self,
        left: dict,
        right: dict,
        *,
        left_source: str,
        right_source: str,
        gap_ms: int,
        direction: str,
        condition: str,
        item_id: str,
    ) -> list[dict] | None:
        """Revise a contiguous pair while keeping its two display slots and media-informed drafts."""
        if (
            left.get("joint_with")
            or right.get("joint_with")
            or not 0 <= gap_ms <= self.settings.values["segment"]["joint_max_gap_ms"]
            or not left_source.strip()
            or not right_source.strip()
        ):
            return None
        source = left_source + right_source
        templates = self.settings.prompts["joint_captions"]
        drafts = [left["text"], right["text"]]
        evidence = [(out.get("observation") or {}).get("text", "") for out in (left, right)]
        user = json.dumps(
            {"原字幕": [left_source, right_source], "初译": drafts, "逐句观察": evidence}, ensure_ascii=False
        )
        req = Request(
            system=templates["translate"].format(**self.settings.prompts["lang_names"][direction]),
            user=user + self.settings.terminology_note(source),
            condition=condition,
            direction=direction,
            source=source,
            max_tokens=self.decode["max_new_tokens"]["line"] * 2 + 32,
            task="joint_translation",
        )
        try:
            whole = self.infer(req, item_id)
        except (RuntimeError, TimeoutError, OSError):
            return None
        decision = model_json_object(whole["text"])
        if decision.get("action") != "join":
            return [whole]
        target = decision.get("translation")
        if not isinstance(target, str) or not target.strip():
            return [whole]
        protected = [t["target"] for t in self.settings.matched_terms(source)]
        protected += [n for n in re.findall(r"[0-9]+", source) if n in "".join(drafts)]
        if not target or any(term not in target for term in protected):
            return [whole]
        split_req = Request(
            system=templates["split"],
            user=json.dumps({"原字幕": [left_source, right_source], "目标语定稿": target}, ensure_ascii=False),
            source=target,
            condition=condition,
            direction=direction,
            max_tokens=req.max_tokens + 16,
            task="joint_captions",
        )
        try:
            split = self.infer(split_req, item_id)
        except (RuntimeError, TimeoutError, OSError):
            return [whole]
        candidate = parse_document(split["text"], 2)
        if candidate and "".join("".join(candidate).split()) == "".join(target.split()):
            for output, before, after in zip((left, right), drafts, candidate, strict=True):
                output.update(text=after, before_joint=before, joint_with=item_id)
        return [whole, split]

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
