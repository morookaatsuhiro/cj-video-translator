from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import perf_counter

from cjvmt.asr import LANGUAGE_UNCERTAIN, detect_direction, make_asr, revise_lines
from cjvmt.backends import Backend, make_backend
from cjvmt.checks import inspect_text
from cjvmt.clip import make_clip, read_wav
from cjvmt.config import Settings, file_hash
from cjvmt.media import prepare, probe
from cjvmt.qwen_asr import revise as revise_with_qwen
from cjvmt.segment import neutralize, split_sentences
from cjvmt.store import base_manifest, new_run, now, read_json, read_jsonl, write_json, write_jsonl, write_text
from cjvmt.translate import CONDITIONS, Translator, choose_mismatch


def stamp(milliseconds: int, separator: str = ",") -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"


def subtitle(lines: list[dict], condition: str, *, vtt: bool = False) -> str:
    result = ["WEBVTT\n"] if vtt else []
    separator = "." if vtt else ","
    for index, line in enumerate(lines, 1):
        start, end = line["utterance"]["t_start_abs_ms"], line["utterance"]["t_end_abs_ms"]
        original = line["text"]["text_asr"]
        translated = line["outputs"][condition]["text"]
        result.append(f"{index}\n{stamp(start, separator)} --> {stamp(end, separator)}\n{original}\n{translated}\n")
    return "\n".join(result)


def subtitle_name(condition: str, extension: str = "srt") -> str:
    return f"subtitles_{condition.replace('+', '_')}.{extension}"


def run_pipeline(
    settings: Settings,
    input_path: Path,
    direction: str,
    backend_name: str,
    mode: str,
    conditions: list[str],
    limit: int | None,
    *,
    backend: Backend | None = None,
    asr=None,
    source_kind: str | None = None,
    license_name: str | None = None,
    mismatch_run: Path | None = None,
    max_seconds: float | None = None,
    controlled_sentence: str | None = None,
    controlled_context: list[str] | None = None,
    pair: dict | None = None,
    on_direction: Callable[[dict], None] | None = None,
    on_progress: Callable[[str, int | None, int | None], None] | None = None,
    hotwords: tuple[str, ...] = (),
    qwen_recheck: bool = False,
) -> Path:
    if direction not in settings.values["asr"]["lang"] and (direction != "auto" or controlled_sentence is not None):
        raise ValueError("方向必须是 auto、ja2zh 或 zh2ja。")
    if mode not in settings.values["prompt_modes"]["available"]:
        raise ValueError("提示词模式必须是 compare、guided 或 perceive。")
    if not conditions or len(set(conditions)) != len(conditions) or set(conditions) - set(CONDITIONS):
        raise ValueError("请选择有效且不重复的翻译条件。")
    if limit is not None and limit < 1:
        raise ValueError("limit 至少为 1。")
    source = input_path.resolve(strict=True)
    source_hash = file_hash(source)
    backend = backend or make_backend(settings, backend_name)
    if controlled_sentence is None:
        asr = asr or make_asr(settings, backend_name)
    run = new_run(settings, "mock" if backend_name == "mock" else direction)
    manifest = base_manifest(settings, run)
    manifest.update(
        input_name=source.name,
        input_sha256=source_hash,
        direction=direction,
        model=backend.identity,
        prompt_mode=mode,
        conditions=conditions,
        asr={
            "model": asr.model_id,
            "revision": asr.revision,
            "device": getattr(asr, "device", None),
            "compute_type": getattr(asr, "compute_type", None),
        }
        if controlled_sentence is None
        else None,
        asr_hotwords=hotwords,
        transcript_kind="controlled" if controlled_sentence is not None else "asr",
        pair=pair,
        kind="offline",
        source_kind=source_kind or ("synthetic" if backend_name == "mock" else "provided"),
        source_license=license_name,
        condition_media={},
    )
    write_json(run / "manifest.json", manifest)
    progress = on_progress or (lambda message, completed, total: None)
    try:
        progress("正在读取视频和音轨", None, None)
        info = prepare(source, run / "media", settings, max_seconds=max_seconds)
        manifest["media"] = info
        if controlled_sentence is None:
            if direction == "auto":
                progress("正在识别源语言", None, None)
                detection = detect_direction(asr, run / "media/audio.wav", settings)
                if not detection:
                    raise ValueError(LANGUAGE_UNCERTAIN)
                direction = detection["direction"]
                manifest.update(direction=direction, language_detection=detection)
                if on_direction:
                    on_direction(detection)
            progress("正在识别语音和切分字幕", None, None)
            started = perf_counter()
            groups = asr.transcribe(
                run / "media/audio.wav",
                settings.values["asr"]["lang"][direction],
                duration_ms=info["duration_ms"],
            )
            manifest["asr"]["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
            write_jsonl(run / "asr.jsonl", groups)
            lines = split_sentences(groups, direction, settings, info["duration_ms"])[:limit]
        else:
            if info["duration_ms"] > settings.values["clip"]["max_window_ms"]:
                raise ValueError("自录最小对的每段视频须在单句片段时长上限内。")
            lines = [
                {
                    "line_id": "L001",
                    "t_start_abs_ms": 0,
                    "t_end_abs_ms": info["duration_ms"],
                    "text_asr": controlled_sentence,
                    "text_neutral": neutralize(controlled_sentence, settings),
                    "context": controlled_context or [],
                    "asr_warning": False,
                }
            ]
        if not lines:
            raise ValueError("没有识别到可翻译的语句，请检查音轨和翻译方向。")
        samples = read_wav(run / "media/audio.wav")
        if qwen_recheck and controlled_sentence is None:
            progress("正在用 Qwen3-ASR 复听原声", None, None)
            manifest["asr"]["qwen_recheck"] = revise_with_qwen(run, lines, direction, settings, hotwords)
        qwen_status = (manifest["asr"] or {}).get("qwen_recheck", {}).get("status")
        if controlled_sentence is None and hasattr(asr, "recheck") and qwen_status != "complete":
            progress("正在结合前文和热词复核语音识别", None, None)
            revise_lines(asr, lines, *samples, direction, settings, hotwords)
        translator = Translator(settings, backend)
        total = len(lines) * len(conditions)
        message = "正在生成字幕，首句可能需要加载翻译模型"
        progress(message, 0, total)
        predictions = []
        rows = []
        candidates = []
        if mismatch_run:
            other_manifest = read_json(mismatch_run / "manifest.json")
            candidates = [
                (mismatch_run, row, other_manifest["input_sha256"]) for row in read_jsonl(mismatch_run / "lines.jsonl")
            ]
        for index, line in enumerate(lines, 1):
            clip = make_clip(run / "media", run, line, info["duration_ms"], settings, audio=samples)
            line["clip"] = clip
            item = f"{source_hash}:{line['line_id']}"
            outputs, checks = {}, {}
            for condition_index, condition in enumerate(conditions, 1):
                mismatch = None
                provenance = None
                if condition.endswith("_shuf"):
                    other_run, other_line, other_hash = choose_mismatch(
                        f"{source_hash}",
                        candidates,
                        settings.values["seed"] + index,
                    )
                    mismatch = (other_run, other_line)
                    provenance = {"run_id": other_run.name, "sample_id": other_line["sample_id"], "sha256": other_hash}
                output, logs = translator.translate_clip(
                    line,
                    run,
                    item_id=item,
                    direction=direction,
                    mode=mode,
                    condition=condition,
                    mismatch=mismatch,
                )
                outputs[condition] = {**output, "run_id": run.name}
                checks[condition] = inspect_text(output["text"], direction, output.get("observation"))
                predictions.extend(
                    {
                        **prediction,
                        "item_id": item,
                        "line_id": line["line_id"],
                        "run_id": run.name,
                        "mismatch": provenance,
                    }
                    for prediction in logs
                )
                manifest["condition_media"][condition] = {
                    "media_types": logs[-1]["media_types"],
                    "use_audio_in_video": logs[-1]["use_audio_in_video"],
                    "decode": logs[-1]["decode"],
                    "video_input": logs[-1]["video_input"],
                }
                progress("正在生成字幕", (index - 1) * len(conditions) + condition_index, total)
            rows.append(
                {
                    "schema_version": settings.values["version"],
                    "sample_id": f"{source_hash[:12]}-{line['line_id']}",
                    "source": {
                        "kind": manifest["source_kind"],
                        "video_id": source.stem,
                        "doc_id": None,
                        "line_idx": index,
                        "license": license_name,
                    },
                    "direction": direction,
                    "utterance": {
                        "t_start_abs_ms": line["t_start_abs_ms"],
                        "t_end_abs_ms": line["t_end_abs_ms"],
                        "speaker_id": "unknown",
                    },
                    "clip": clip,
                    "text": {
                        "text_asr": line["text_asr"],
                        "text_neutral": line["text_neutral"],
                        "gold": controlled_sentence,
                    },
                    "context": line["context"],
                    "outputs": outputs,
                    "checks": checks,
                    "reference": {"text": None, "question": None, "style": None},
                    "provenance": {
                        "pipeline_version": settings.values["version"],
                        "git_commit": manifest["git_commit"],
                        "created_at": now(),
                    },
                    "asr_warning": line["asr_warning"],
                    **({"asr_recheck": line["asr_recheck"]} if "asr_recheck" in line else {}),
                }
            )
            write_jsonl(run / "lines.jsonl", rows)
            write_jsonl(run / "predictions.jsonl", predictions)
        write_jsonl(run / "lines.jsonl", rows)
        write_jsonl(run / "predictions.jsonl", predictions)
        progress("正在导出字幕和回放", None, None)
        for condition in conditions:
            write_text(run / subtitle_name(condition), subtitle(rows, condition))
            write_text(run / subtitle_name(condition, "vtt"), subtitle(rows, condition, vtt=True))
        manifest.update(
            status="complete",
            line_count=len(rows),
            prediction_count=len(predictions),
            cache_hits=sum(row["cache_hit"] for row in predictions),
            call_metrics=[
                {
                    key: row[key]
                    for key in (
                        "line_id",
                        "condition",
                        "task",
                        "prompt_sha256",
                        "elapsed_ms",
                        "peak_vram_mib",
                        "cache_hit",
                    )
                }
                for row in predictions
            ],
            peak_vram_mib=max(
                (row["peak_vram_mib"] for row in predictions if row["peak_vram_mib"] is not None), default=None
            ),
        )
        from cjvmt.compare import compare_run

        compare_run(run, manifest)
    except Exception as error:
        manifest.update(status="failed", error=str(error))
        raise
    finally:
        manifest["ended_at"] = now()
        write_json(run / "manifest.json", manifest)
    return run


def validate_run(run: Path, settings: Settings) -> int:
    manifest = read_json(run / "manifest.json")
    rows = read_jsonl(run / "lines.jsonl")
    predictions = read_jsonl(run / "predictions.jsonl")
    if (
        manifest["status"] != "complete"
        or manifest["line_count"] != len(rows)
        or manifest["prediction_count"] != len(predictions)
    ):
        raise ValueError("运行状态或行数与 manifest 不符。")
    unique = set()
    for row in rows:
        start, end = (row["utterance"][f"t_{key}_abs_ms"] for key in ("start", "end"))
        if row["sample_id"] in unique or not 0 <= start < end <= manifest["media"]["duration_ms"]:
            raise ValueError(f"无效条目时间或重复编号：{row['sample_id']}")
        unique.add(row["sample_id"])
        if set(row["outputs"]) != set(manifest["conditions"]):
            raise ValueError(f"译文条件不完整：{row['sample_id']}")
        clip = row["clip"]
        video = probe(run / clip["path_v"], settings)
        audio, rate = read_wav(run / clip["path_audio_gated"])
        if (
            video["has_audio"]
            or abs(video["duration_ms"] * rate / 1000 - len(audio)) > 1
            or audio[: clip["gate_start_sample"]].any()
            or audio[clip["gate_end_sample"] :].any()
        ):
            raise ValueError(f"片段模态隔离或时长校验失败：{row['sample_id']}")
        if not probe(run / clip["path_av"], settings)["has_audio"] or not (run / clip["path_preview"]).is_file():
            raise ValueError(f"缺少封装音轨或预览：{row['sample_id']}")
    for condition in manifest["conditions"]:
        for extension in ("srt", "vtt"):
            if not (run / subtitle_name(condition, extension)).is_file():
                raise ValueError(f"字幕文件缺失：{condition} {extension}")
    return len(rows)
