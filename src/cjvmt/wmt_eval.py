"""Run controlled document-level translation of a prepared WMT release."""

from __future__ import annotations

import csv
import random
import re
import shutil
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from sacrebleu import corpus_chrf

from cjvmt.backends import make_backend
from cjvmt.config import Settings, file_hash
from cjvmt.media import ffmpeg
from cjvmt.segment import neutralize
from cjvmt.stats import bootstrap
from cjvmt.store import base_manifest, new_run, now, read_json, read_jsonl, write_json, write_jsonl, write_text
from cjvmt.translate import FOUR, Translator
from cjvmt.wmt import safe_member


def document_media(settings: Settings, source: Path, directory: Path) -> dict:
    wav, silent = directory / "audio.wav", directory / "silent.mp4"
    ffmpeg(settings, ["-i", str(source), "-vn", "-ac", "1", "-ar", str(settings.values["clip"]["audio_sr"]), str(wav)])
    ffmpeg(settings, ["-i", str(source), "-map", "0:v:0", "-an", "-c:v", "copy", str(silent)])
    return {
        "T_neutral": {},
        "T_asr": {},
        "T+A": {"audio": wav},
        "T+V": {"video": silent},
        "T+V+A": {"video": source, "interleaved": True},
    }


def extract_source(media: Path, member: str, output: Path, zip_media: ZipFile | None) -> Path:
    member = safe_member(member)
    if zip_media:
        target = output / "source.mp4"
        with zip_media.open(member) as origin, target.open("wb") as destination:
            shutil.copyfileobj(origin, destination)
        return target
    source = (media / member).resolve()
    if not source.is_relative_to(media.resolve()) or not source.is_file():
        raise ValueError(f"找不到准备记录对应的媒体文件：{member}")
    return source


def evaluate(
    settings: Settings,
    prepared: Path,
    dataset: str,
    backend_name: str,
    limit: int | None,
    *,
    audit: Path | None = None,
    mismatch: bool = False,
    resume: Path | None = None,
) -> Path:
    meta = read_json(prepared / "manifest.json")
    if meta.get("dataset") != dataset or meta["discrepancies"]:
        raise ValueError("准备记录的语料集不匹配或尚有数字差异。")
    v26 = dataset == "wmt26"
    if v26 and not audit:
        raise ValueError("WMT26 需要以 --audit 提供同一准备目录的 OCR 审计。")
    if mismatch and not v26:
        raise ValueError("错配条件用于 WMT26。")
    audit_data = read_json(audit) if audit else None
    if audit_data and audit_data["prepare_run"] != prepared.name:
        raise ValueError("OCR 审计与准备目录不匹配。")
    ocr = {row["doc_id"]: row for row in audit_data["documents"]} if audit_data else {}
    media = Path(meta["media_path"]) if meta.get("media_path") else None
    population = read_jsonl(prepared / "corpus.jsonl")
    rows = population[:limit]
    if v26 and limit is not None and limit < len(population):
        rows = random.Random(settings.values["seed"]).sample(population, limit)
    if v26 and any(row["doc_id"] not in ocr for row in rows):
        raise ValueError("OCR 审计尚未覆盖所有选定文档。")
    if not media or not media.exists() or not rows or not all(row["has_media"] for row in rows):
        raise ValueError("参与运行的每条记录都需要可读取的媒体。")
    if meta.get("media_sha256") and file_hash(media) != meta["media_sha256"]:
        raise ValueError("媒体压缩包和准备记录的哈希不符。")
    backend = make_backend(settings, backend_name)
    translator = Translator(settings, backend)
    conditions = ("T_neutral", "T_asr", *FOUR[1:]) if v26 else FOUR
    if mismatch:
        conditions += ("T+A_shuf", "T+V_shuf")
    direction = "ja2zh" if dataset == "wmt25" else "zh2ja"
    run = resume or new_run(settings, f"{dataset}-eval")
    manifest = base_manifest(settings, run)
    manifest.update(
        kind="wmt_eval",
        source_kind=dataset,
        model=backend.identity,
        dataset=dataset,
        prepare_run=prepared.name,
        corpus_sha256=file_hash(prepared / "corpus.jsonl"),
        direction=direction,
        conditions=conditions,
        selected_doc_ids=[row["doc_id"] for row in rows],
        ocr_sha256=file_hash(audit) if audit else None,
        ocr_identity=audit_data["model"] if audit_data else None,
    )
    if resume:
        previous = read_json(run / "manifest.json")
        keys = (
            "dataset",
            "model",
            "corpus_sha256",
            "config_sha256",
            "prompts_sha256",
            "conditions",
            "selected_doc_ids",
            "ocr_sha256",
        )
        expected = {key: manifest[key] for key in keys}
        expected["conditions"] = list(conditions)
        if expected != {key: previous.get(key) for key in keys}:
            raise ValueError("恢复参数与原运行不一致。")
    write_json(run / "manifest.json", manifest)
    predictions, output = [], []
    checkpoint = run / "progress.json"
    done = set()
    if resume and checkpoint.is_file():
        saved = read_json(checkpoint)
        output, predictions = saved["documents"], saved["predictions"]
        done = {row["doc_id"] for row in output}
    try:
        zip_media = ZipFile(media) if media.is_file() else None
        try:
            for row in rows:
                if row["doc_id"] in done:
                    continue
                with TemporaryDirectory(dir=run) as temporary:
                    directory = Path(temporary)
                    source = extract_source(media, row["video"], directory, zip_media)
                    if v26 and file_hash(source) != ocr[row["doc_id"]]["media_sha256"]:
                        raise ValueError(f"{row['doc_id']} 的媒体与 OCR 审计哈希不符。")
                    inputs = document_media(settings, source, directory)
                    alternate = None
                    if mismatch:
                        candidates = [
                            item for item in population if item["has_media"] and item["video"] != row["video"]
                        ]
                        if not candidates:
                            raise ValueError("错配条件至少需要两个不同的媒体文件。")
                        alternate = random.Random(f"{settings.values['seed']}:{row['doc_id']}").choice(candidates)
                        other = directory / "mismatch"
                        other.mkdir()
                        other_inputs = document_media(
                            settings, extract_source(media, alternate["video"], other, zip_media), other
                        )
                        for condition in ("T+A", "T+V"):
                            inputs[f"{condition}_shuf"] = other_inputs[condition]
                    asr = row["asr"]
                    base = (
                        [re.sub(r"[？?]+$", "。", text) for text in row["gold"]]
                        if v26
                        else [neutralize(text, settings) for text in asr]
                    )
                    for condition in conditions:
                        source_lines = asr if condition == "T_asr" else base
                        text, logs, fallback = translator.document(
                            item_id=row["doc_id"],
                            direction=direction,
                            condition=condition,
                            lines=source_lines,
                            media=inputs[condition],
                        )
                        output.append(
                            {
                                "doc_id": row["doc_id"],
                                "condition": condition,
                                "source": "\n".join(source_lines),
                                "source_text_lines": source_lines,
                                "translation_lines": text,
                                "text": "\n".join(text),
                                "reference": "\n".join(row["reference"]),
                                "fallback_lines": fallback,
                                "source_lines": len(source_lines),
                                "reference_lines": row["reference"],
                                "question_group": row.get("question_group"),
                                "alignment": row.get("alignment") if condition == "T_asr" else None,
                                "ocr": ocr.get(row["doc_id"]) if v26 else None,
                                "mismatch_doc_id": alternate["doc_id"]
                                if alternate and condition.endswith("_shuf")
                                else None,
                            }
                        )
                        predictions.extend({**item, "doc_id": row["doc_id"], "run_id": run.name} for item in logs)
                write_json(checkpoint, {"documents": output, "predictions": predictions})
                write_jsonl(run / "documents.jsonl", output)
                write_jsonl(run / "predictions.jsonl", predictions)
        finally:
            if zip_media:
                zip_media.close()
        write_jsonl(run / "documents.jsonl", output)
        write_jsonl(run / "predictions.jsonl", predictions)
        manifest.update(
            status="complete",
            document_count=len(rows),
            total_available=meta["counts"]["speech_docs"],
            sentence_aligned_docs=meta["counts"].get(
                "docs_with_sentence_alignment", meta["counts"].get("docs_gold_ref_line_match")
            ),
        )
    except Exception as error:
        manifest.update(status="failed", error=str(error))
        raise
    finally:
        manifest["ended_at"] = now()
        write_json(run / "manifest.json", manifest)
        close = getattr(backend, "close", None)
        if close:
            close()
    score_release(run, settings.path("reports"))
    return run


def score_release(run: Path, reports: Path) -> Path:
    manifest = read_json(run / "manifest.json")
    if manifest["status"] != "complete" or manifest.get("kind") != "wmt_eval":
        raise ValueError("需要完整的 WMT 评测运行。")
    documents = run / "documents.jsonl"
    output = read_jsonl(documents)
    if manifest["dataset"] == "wmt26":
        from cjvmt.wmt26_score import score_wmt26

        return score_wmt26(run, reports, manifest, output)
    comet_path = run / "comet.json"
    comet = read_json(comet_path) if comet_path.is_file() else None
    if comet and comet["documents_sha256"] != file_hash(documents):
        raise ValueError("COMET 分数与原始文档输出不匹配。")
    scores = {(item["condition"], item["doc_id"]): item["comet"] for item in comet["scores"]} if comet else {}
    results = []
    for condition in manifest["conditions"]:
        rows = [row for row in output if row["condition"] == condition]
        if len(rows) != manifest["document_count"] or len({row["doc_id"] for row in rows}) != len(rows):
            raise ValueError(f"{condition} 的逐文档输出不完整。")
        score = corpus_chrf([row["text"] for row in rows], [[row["reference"] for row in rows]])
        results.append(
            {
                "condition": condition,
                "chrf": round(score.score, 3),
                "fallback_lines": sum(row["fallback_lines"] for row in rows),
                "blank_outputs": sum(not row["text"].strip() for row in rows),
            }
        )
    comparisons = []
    if comet:
        baseline = manifest["conditions"][0]
        docs = manifest["selected_doc_ids"]
        for item in results:
            condition = item["condition"]
            item["comet"] = round(mean(scores[condition, doc] for doc in docs), 5)
            if condition == baseline:
                continue
            diffs = [scores[condition, doc] - scores[baseline, doc] for doc in docs]
            low, high = bootstrap(diffs, iterations=manifest["config"]["eval"]["bootstrap_n"], seed=manifest["seed"])
            comparisons.append(
                {
                    "condition": condition,
                    "baseline": baseline,
                    "comet_delta": round(mean(diffs), 5),
                    "ci_low": round(low, 5),
                    "ci_high": round(high, 5),
                }
            )
        write_csv(run / "comparisons.csv", comparisons)
    path = run / "summary.csv"
    write_csv(path, results)
    if manifest["model"]["backend"] == "mock":
        return path
    report = reports / f"eval_{manifest['dataset']}.md"
    run_id = manifest["run_id"]
    n = manifest["document_count"]
    body = [
        "# WMT25 日语 → 简体中文评测",
        "",
        f"运行 `{run_id}`，准备记录 `{manifest['prepare_run']}`。",
        f"本次评测 {n} 段文档。<!-- run={run_id} file=manifest.json key=document_count value={n} -->",
        "",
        f"模型 `{manifest['model']['model']}`，revision `{manifest['model']['revision']}`。",
        "同模型、同解码，依次比较文本、加声音、加静音视频与音画交错。源转写与参考按整段提供，逐句问句命中率未测。",
        f"COMET 使用 `{comet['model']}`，版本 `{comet['checkpoint_revision']}`。" if comet else "COMET 未运行。",
        "",
        "| 条件 | 文档级 chrF | 回退行 | 空输出 |" + (" COMET |" if comet else ""),
        "|---|---:|---:|---:|" + ("---:|" if comet else ""),
    ]
    for row in results:
        key = row["condition"]
        notes = " ".join(
            f"<!-- run={run_id} file=summary.csv key=condition:{key}:{name} value={row[name]} -->"
            for name in row
            if name != "condition"
        )
        body.append(
            "| "
            + " | ".join([f"`{key}`", *(str(value) for name, value in row.items() if name != "condition")])
            + " | "
            + notes
        )
    if comparisons:
        body.extend(
            (
                "",
                "COMET 差值以同一文档配对重采样，基线为仅文字。",
                "",
                "| 条件 | COMET 差值 | 95% CI 下界 | 95% CI 上界 |",
                "|---|---:|---:|---:|",
            )
        )
        for row in comparisons:
            key = row["condition"]
            metrics = ("comet_delta", "ci_low", "ci_high")
            notes = " ".join(
                f"<!-- run={run_id} file=comparisons.csv key=condition:{key}:{name} value={row[name]} -->"
                for name in metrics
            )
            body.append("| " + " | ".join([f"`{key}`", *(str(row[name]) for name in metrics)]) + " | " + notes)
    body.extend(("", "该结果用于课程研究。评测媒体保留在本机。", ""))
    write_text(report, "\n".join(body))
    return path


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def audit_release(
    settings: Settings, prepared: Path, detector: Path, recognizer: Path, frame_limit: int | None
) -> Path:
    from cjvmt.wmt_ocr import SubtitleAudit

    meta = read_json(prepared / "manifest.json")
    if meta.get("dataset") != "wmt26" or meta["discrepancies"] or not meta.get("media_path"):
        raise ValueError("需要一份核对通过且包含媒体的 WMT26 准备记录。")
    media = Path(meta["media_path"])
    if not media.exists() or (meta.get("media_sha256") and file_hash(media) != meta["media_sha256"]):
        raise ValueError("无法读取准备记录中的媒体，或压缩包哈希不符。")
    model = SubtitleAudit(settings, detector, recognizer, frame_limit)
    path = prepared / "ocr.json"
    documents = []
    zip_media = ZipFile(media) if media.is_file() else None
    try:
        for row in read_jsonl(prepared / "corpus.jsonl"):
            if not row["has_media"]:
                continue
            with TemporaryDirectory(dir=prepared) as temporary:
                source = extract_source(media, row["video"], Path(temporary), zip_media)
                documents.append({"doc_id": row["doc_id"], "media_sha256": file_hash(source), **model(source)})
    finally:
        if zip_media:
            zip_media.close()
    write_json(path, {"prepare_run": prepared.name, "model": model.identity, "documents": documents})
    return path
