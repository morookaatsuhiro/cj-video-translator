from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from cjvmt.backends import make_backend
from cjvmt.config import Settings, file_hash
from cjvmt.pipeline import run_pipeline, validate_run
from cjvmt.store import base_manifest, new_run, now, read_json, read_jsonl, write_json, write_text
from cjvmt.translate import FOUR

REVIEW_PENDING = "待人工核对"
REVIEW_CHOICES = ("符合", "不符合", "不确定")


def read_pairs(source: Path, settings: Settings) -> list[dict[str, str]]:
    required = {"pair_id", "variant", "direction", "sentence", "context", "expected_reading"}
    grouped = {}
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"录制清单需要列：{', '.join(sorted(required))}。")
        for entry in reader:
            row = {key: (entry.get(key) or "").strip() for key in required}
            ident, variant = row["pair_id"], row["variant"]
            if not re.fullmatch(r"pair[0-9]+", ident) or variant not in {"A", "B"}:
                raise ValueError(f"无效的最小对编号或版本：{ident} {variant}。")
            if (
                row["direction"] not in settings.values["asr"]["lang"]
                or not row["sentence"]
                or not row["expected_reading"]
            ):
                raise ValueError(f"{ident} {variant} 需要翻译方向、单句台词和预期读法。")
            if len(row["sentence"].splitlines()) != 1:
                raise ValueError(f"{ident} {variant} 的台词须为单行。")
            versions = grouped.setdefault(ident, {})
            if variant in versions:
                raise ValueError(f"重复的最小对版本：{ident} {variant}。")
            versions[variant] = row
    if not grouped:
        raise ValueError("录制清单为空。")
    for ident, versions in grouped.items():
        if set(versions) != {"A", "B"}:
            raise ValueError(f"{ident} 需要 A、B 两版。")
        if any(versions["A"][key] != versions["B"][key] for key in ("direction", "sentence", "context")):
            raise ValueError(f"{ident} 的 A、B 两版须使用相同语向、台词和前文。")
    return [versions[variant] for versions in grouped.values() for variant in ("A", "B")]


def run_pairs(settings: Settings, source: Path, backend_name: str, mode: str) -> Path:
    from cjvmt.demo import export_comparison

    if mode not in settings.values["prompt_modes"]["available"]:
        raise ValueError("最小对提示词模式必须是 compare、guided 或 perceive。")
    entries = read_pairs(source, settings)
    media = [source.parent / f"{row['pair_id']}_{row['variant']}.mp4" for row in entries]
    missing = [path.name for path in media if not path.is_file()]
    if missing:
        raise ValueError(f"录制清单缺少对应视频：{', '.join(missing)}。")
    for index in range(0, len(media), 2):
        if file_hash(media[index]) == file_hash(media[index + 1]):
            raise ValueError(f"{entries[index]['pair_id']} 的 A、B 视频内容相同。")
    backend = make_backend(settings, backend_name)
    run = new_run(settings, "pairs")
    manifest = base_manifest(settings, run)
    manifest.update(
        kind="pairs",
        source_kind="self_recorded",
        model=backend.identity,
        prompt_mode=mode,
        source_sha256=file_hash(source),
        conditions=FOUR,
        video_runs=[],
    )
    write_json(run / "manifest.json", manifest)
    outputs = []
    try:
        for entry, path in zip(entries, media, strict=True):
            pair = {key: entry[key] for key in ("pair_id", "variant", "expected_reading")}
            pair["batch"] = run.name
            result = run_pipeline(
                settings,
                path,
                entry["direction"],
                backend_name,
                mode,
                list(FOUR),
                None,
                backend=backend,
                source_kind="synthetic" if backend_name == "mock" else "self_recorded",
                license_name=None if backend_name == "mock" else "研究者提供的课程录制素材",
                controlled_sentence=entry["sentence"],
                controlled_context=entry["context"].splitlines(),
                pair=pair,
            )
            validate_run(result, settings)
            export_comparison(result)
            row = read_jsonl(result / "lines.jsonl")[0]
            outputs.append(
                {
                    **entry,
                    **{key: row["outputs"][key]["text"] for key in FOUR},
                    "review": REVIEW_PENDING,
                    **{f"review_{condition}": REVIEW_PENDING for condition in FOUR},
                    "run_id": result.name,
                }
            )
            manifest["video_runs"].append(result.name)
            write_json(run / "manifest.json", manifest)
        table = io.StringIO()
        writer = csv.DictWriter(table, fieldnames=list(outputs[0]))
        writer.writeheader()
        writer.writerows(outputs)
        write_text(run / "pairs.csv", table.getvalue())
        manifest.update(status="complete")
    except Exception as error:
        manifest.update(status="failed", error=str(error))
        raise
    finally:
        manifest["ended_at"] = now()
        write_json(run / "manifest.json", manifest)
        if close := getattr(backend, "close", None):
            close()
    return run


def summarize_reviews(run: Path) -> dict:
    manifest = read_json(run / "manifest.json")
    if manifest.get("kind") != "pairs" or manifest.get("status") != "complete":
        raise ValueError("请提供已完成的最小对运行目录。")
    if manifest["model"]["backend"] == "mock":
        raise ValueError("Mock 结果仅供演示，人工统计需要真实模型运行。")
    with (run / "pairs.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"pair_id", "variant", "run_id"}.issubset(reader.fieldnames or []):
            raise ValueError("人工核对表缺少最小对编号、版本或运行编号。")
        rows = list(reader)
    run_ids = [row.get("run_id") for row in rows]
    pairs = {}
    for row in rows:
        variants = pairs.setdefault(row["pair_id"], {})
        if row["variant"] in variants:
            raise ValueError(f"人工核对表包含重复版本 {row['pair_id']} {row['variant']}。")
        variants[row["variant"]] = row
    if (
        not all(run_ids)
        or sorted(run_ids) != sorted(manifest["video_runs"])
        or any(set(variants) != {"A", "B"} for variants in pairs.values())
    ):
        raise ValueError("人工核对表中的运行编号或 A、B 对应关系不完整。")

    report = {}
    results = {}
    for condition in manifest["conditions"]:
        results[condition] = decided = {}
        pending = uncertain = 0
        for pair_id, variants in pairs.items():
            reviews = [(row.get(f"review_{condition}") or "").strip() or REVIEW_PENDING for row in variants.values()]
            if set(reviews) - {*REVIEW_CHOICES, REVIEW_PENDING}:
                raise ValueError(f"{pair_id} 的 {condition} 判定须为「符合」「不符合」或「不确定」。")
            pending += reviews.count(REVIEW_PENDING)
            uncertain += reviews.count("不确定")
            if not {REVIEW_PENDING, "不确定"}.intersection(reviews):
                decided[pair_id] = all(review == "符合" for review in reviews)
        report[condition] = {
            "decided_pairs": len(decided),
            "both_variants_fit": sum(decided.values()),
            "pending_variants": pending,
            "uncertain_variants": uncertain,
        }
    baseline = results["T_neutral"]
    for condition, decided in results.items():
        if condition != "T_neutral":
            paired = baseline.keys() & decided.keys()
            report[condition].update(
                paired_with_text=len(paired),
                improved=sum(not baseline[key] and decided[key] for key in paired),
                regressed=sum(baseline[key] and not decided[key] for key in paired),
            )
    return {"run_id": run.name, "pair_count": len(pairs), "conditions": report}
