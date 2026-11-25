"""Compute line-level WMT26 results with explicit eligibility and stratification."""

from collections import Counter, defaultdict
from itertools import chain
from math import sqrt
from pathlib import Path

import numpy as np
from sacrebleu.metrics import CHRF

from cjvmt.checks import is_question_ja, simplified_leak, style_ja
from cjvmt.config import file_hash
from cjvmt.stats import holm, mcnemar, wilson
from cjvmt.store import read_json, write_jsonl, write_text

PAIRS = (
    ("T_neutral", "T+A"),
    ("T_neutral", "T+V"),
    ("T_neutral", "T+V+A"),
    ("T+A", "T+V+A"),
    ("T+V", "T+V+A"),
    ("T+A", "T+A_shuf"),
    ("T+V", "T+V_shuf"),
)
STRATA = ("main", "japanese_onscreen", "chinese_onscreen", "no_chinese_onscreen", "chinese_question_onscreen")


def strata(ocr: dict) -> list[str]:
    if ocr["japanese"]:
        return ["japanese_onscreen"]
    names = ["main", "chinese_onscreen" if ocr["chinese"] else "no_chinese_onscreen"]
    if ocr["chinese_question"]:
        names.append("chinese_question_onscreen")
    return names


def style_class(value: str) -> str:
    style = style_ja(value)
    return "polite" if style == "honorific" else style


def line_results(documents: list[dict]) -> list[dict]:
    output = []
    for row in documents:
        refs, groups, texts = row["reference_lines"], row["question_group"], row["translation_lines"]
        approximate = row["condition"] == "T_asr"
        if approximate:
            indices = [entry[0] if entry else None for entry in row["alignment"]]
        else:
            if len(refs) != len(texts):
                raise ValueError(f"参考与输出行数不符：{row['doc_id']}")
            indices = range(len(texts))
        for index, (reference, group, position) in enumerate(zip(refs, groups, indices, strict=True)):
            text = texts[position] if position is not None and position < len(texts) else None
            output.append(
                {
                    "doc_id": row["doc_id"],
                    "line": index,
                    "condition": row["condition"],
                    "group": group,
                    "approximate": approximate,
                    "strata": strata(row["ocr"]),
                    "question": is_question_ja(text) if text is not None else None,
                    "style_ref": style_class(reference),
                    "style": style_class(text) if text else "none",
                }
            )
    return output


def measure(condition: str, stratum: str, metric: str, values: list[bool], minimum: int) -> dict:
    n, positive = len(values), sum(values)
    interval = wilson(positive, n)
    return {
        "condition": condition,
        "stratum": stratum,
        "metric": metric,
        "n": n,
        "success": positive,
        "value": round(positive / n, 5) if n else None,
        "ci_low": round(interval[0], 5) if interval else None,
        "ci_high": round(interval[1], 5) if interval else None,
        "small_sample": n < minimum,
        "approximate": condition == "T_asr",
    }


def measures(data: list[dict], condition: str, stratum: str, minimum: int) -> list[dict]:
    known = [row for row in data if row["question"] is not None]
    questions = [row["question"] for row in known if row["group"]]
    statements = [row["question"] for row in known if not row["group"]]
    styles = [row for row in known if "none" not in (row["style"], row["style_ref"])]
    confusion = Counter((row["style_ref"], row["style"]) for row in styles)
    out = [
        measure(condition, stratum, metric, values, minimum)
        for metric, values in (
            ("recall", questions),
            ("false_positive", statements),
            ("style", [r["style"] == r["style_ref"] for r in styles]),
            ("plain_baseline", [r["style_ref"] == "plain" for r in styles]),
            *(
                (group, [r["question"] for r in known if r["group"] == group])
                for group in ("Q1", "Q2", "Q3", "Q4", "Q5")
            ),
        )
    ]
    tp, fp, fn = sum(questions), sum(statements), len(questions) - sum(questions)
    n = len(styles)
    expected = (
        sum(
            sum(confusion[label, guess] for guess in ("plain", "polite"))
            * sum(confusion[truth, label] for truth in ("plain", "polite"))
            for label in ("plain", "polite")
        )
        / n**2
        if n
        else 0
    )
    agreement = sum(confusion[x, x] for x in ("plain", "polite")) / n if n else 0
    for metric, value, count in (
        ("f1", 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None, len(known)),
        ("kappa", (agreement - expected) / (1 - expected) if n and expected < 1 else None, n),
        *((f"{a}_as_{b}", confusion[a, b], n) for a in ("plain", "polite") for b in ("plain", "polite")),
    ):
        out.append(
            {
                **measure(condition, stratum, metric, [], minimum),
                "n": count,
                "small_sample": count < minimum,
                "value": round(value, 5) if value is not None else None,
            }
        )
    return out


def comparisons(rows: list[dict]) -> list[dict]:
    by_metric = defaultdict(dict)
    for row in rows:
        if "main" not in row["strata"] or row["approximate"] or row["question"] is None:
            continue
        key = row["doc_id"], row["line"]
        condition = row["condition"]
        question_metric = "recall" if row["group"] else "false_positive"
        by_metric[question_metric].setdefault(condition, {})[key] = row["question"] == bool(row["group"])
        if row["group"] == "Q5":
            by_metric["Q5"].setdefault(condition, {})[key] = row["question"]
        if "none" not in (row["style"], row["style_ref"]):
            by_metric["style"].setdefault(condition, {})[key] = row["style"] == row["style_ref"]
    output = []
    for metric, metrics in sorted(by_metric.items()):
        family = []
        for left, right in PAIRS:
            if left not in metrics or right not in metrics:
                continue
            first, second = metrics[left], metrics[right]
            keys = first.keys() & second.keys()
            if not keys:
                continue
            b = sum(first[k] and not second[k] for k in keys)
            c = sum(not first[k] and second[k] for k in keys)
            family.append(
                {"metric": metric, "left": left, "right": right, "n": len(keys), "b": b, "c": c, "p": mcnemar(b, c)}
            )
        for item, adjusted in zip(family, holm([item["p"] for item in family]), strict=True):
            n = item["n"]
            discordant = (item["b"] + item["c"]) / n
            output.append(
                {
                    **item,
                    "p_holm": adjusted,
                    "detectable_pp_approx": round(100 * 2.8016 * sqrt(discordant / n), 3) if discordant else None,
                }
            )
    return output


def comet_inputs(row: dict) -> list[dict]:
    if row["condition"] == "T_asr" or not row.get("reference_lines"):
        return [{"src": row["source"], "mt": row["text"], "ref": row["reference"]}]
    return [
        {"src": src, "mt": mt, "ref": ref}
        for src, mt, ref in zip(row["source_text_lines"], row["translation_lines"], row["reference_lines"], strict=True)
    ]


def score_wmt26(run: Path, reports: Path, manifest: dict, documents: list[dict]) -> Path:
    from cjvmt.wmt_eval import write_csv

    line_rows = line_results(documents)
    conditions = manifest["conditions"]
    minimum = manifest["config"]["eval"]["min_cell_n"]
    tested = comparisons(line_rows)
    coma = run / "comet.json"
    comet = read_json(coma) if coma.is_file() else None
    if comet and comet["documents_sha256"] != file_hash(run / "documents.jsonl"):
        raise ValueError("COMET 分数与 WMT26 文档输出不匹配。")
    comet_scores = {(x["doc_id"], x["condition"]): x for x in comet["scores"]} if comet else {}
    chrf = CHRF()
    bootstrap_n = manifest["config"]["eval"]["bootstrap_n"]
    summary, comet_deltas = [], []
    for condition in conditions:
        subset = [doc for doc in documents if doc["condition"] == condition]
        if {doc["doc_id"] for doc in subset} != set(manifest["selected_doc_ids"]) or len(subset) != manifest[
            "document_count"
        ]:
            raise ValueError(f"{condition} 的 WMT26 文档输出不完整。")
        for stratum in STRATA:
            docs = [doc for doc in subset if stratum in strata(doc["ocr"])]
            if not docs:
                continue
            selected = [row for row in line_rows if row["condition"] == condition and stratum in row["strata"]]
            summary.extend(measures(selected, condition, stratum, minimum))
            doc_entries = [comet_inputs(doc) for doc in docs]
            entries = list(chain.from_iterable(doc_entries))
            rng = np.random.default_rng(manifest["seed"])
            weights = rng.multinomial(len(docs), np.full(len(docs), 1 / len(docs)), size=bootstrap_n)
            doc_stats = np.array(
                [
                    np.sum(
                        chrf._extract_corpus_statistics([r["mt"] for r in group], [[r["ref"] for r in group]]), axis=0
                    )
                    for group in doc_entries
                ]
            )
            score = chrf._compute_score_from_stats(doc_stats.sum(axis=0)).score
            sampled_scores = [chrf._compute_score_from_stats(counts).score for counts in weights @ doc_stats]
            low, high = np.quantile(sampled_scores, (0.025, 0.975))
            summary.append(
                {
                    **measure(condition, stratum, "chrf", [], minimum),
                    "value": round(score, 3),
                    "ci_low": round(float(low), 3),
                    "ci_high": round(float(high), 3),
                    "n": len(entries),
                    "small_sample": len(entries) < minimum,
                }
            )
            fallback = [i < doc["fallback_lines"] for doc in docs for i in range(doc["source_lines"])]
            summary.append(measure(condition, stratum, "fallback", fallback, minimum))
            summary.append(
                measure(
                    condition, stratum, "simplified_leak", [bool(simplified_leak(x["mt"])) for x in entries], minimum
                )
            )
            if comet:
                scored = [comet_scores[doc["doc_id"], condition]["line_scores"] for doc in docs]
                lengths = np.array([len(group) for group in scored])
                totals = np.array([sum(group) for group in scored])
                if lengths.tolist() != [len(group) for group in doc_entries]:
                    raise ValueError(f"{condition} 的 COMET 分数数量与文档不匹配。")
                counts = weights @ lengths
                n_scores = int(lengths.sum())
                low, high = np.quantile((weights @ totals) / counts, (0.025, 0.975))
                summary.append(
                    {
                        **measure(condition, stratum, "comet", [], minimum),
                        "value": round(float(totals.sum() / n_scores), 5),
                        "ci_low": round(float(low), 5),
                        "ci_high": round(float(high), 5),
                        "n": n_scores,
                        "small_sample": n_scores < minimum,
                    }
                )
                if stratum == "main" and condition not in {"T_neutral", "T_asr"}:
                    baseline = np.array([sum(comet_scores[doc["doc_id"], "T_neutral"]["line_scores"]) for doc in docs])
                    diffs = totals - baseline
                    low, high = np.quantile((weights @ diffs) / counts, (0.025, 0.975))
                    comet_deltas.append(
                        {
                            "condition": condition,
                            "n": n_scores,
                            "delta": round(float(diffs.sum() / n_scores), 5),
                            "ci_low": round(float(low), 5),
                            "ci_high": round(float(high), 5),
                        }
                    )
    for source, alternate in (("T+A", "T+A_shuf"), ("T+V", "T+V_shuf")):
        if alternate not in conditions:
            continue
        main = {
            (row["doc_id"], row["line"]): row
            for row in line_rows
            if row["condition"] == source and "main" in row["strata"]
        }
        changes = []
        for row in line_rows:
            if row["condition"] != alternate or "main" not in row["strata"]:
                continue
            base = main[row["doc_id"], row["line"]]
            changes.append((row["question"], row["style"]) != (base["question"], base["style"]))
        summary.append(measure(alternate, "main", "mismatch_follow", changes, minimum))
    for row in summary:
        row["id"] = f"{row['stratum']}/{row['condition']}/{row['metric']}"
    for row in tested:
        row["id"] = f"{row['metric']}/{row['left']}/{row['right']}"
    path = run / "summary.csv"
    write_csv(path, summary)
    if tested:
        write_csv(run / "comparisons.csv", tested)
    if comet_deltas:
        write_csv(run / "comet_comparisons.csv", comet_deltas)
    write_jsonl(run / "line_scores.jsonl", line_rows)
    if manifest["model"]["backend"] != "mock":
        report_wmt26(run, reports, manifest, summary, tested, comet_deltas)
    return path


def report_wmt26(run, reports, manifest, summary, tested, comet_deltas):
    run_id = run.name
    n = manifest["document_count"]
    population = manifest["total_available"]
    prepare_id = manifest["prepare_run"]
    body = [
        "# WMT26 中文 → 日语语音翻译",
        "",
        f"评测运行 `{run_id}`，准备记录 `{prepare_id}`。",
        f"参与评测 {n} 段。<!-- run={run_id} file=manifest.json key=document_count value={n} -->",
        f"模型 `{manifest['model']['model']}`，修订 `{manifest['model']['revision']}`。",
        f"发布集共 {population} 段。<!-- run={run_id} file=manifest.json key=total_available value={population} -->",
        "固定种子无放回抽样，抽样 ID 见 manifest。" if n < population else "运行覆盖准备记录中的全部语音文档。",
        "文本核对、标点对齐方法和问句分层见准备记录。",
        "主表剔除有假名的画面。OCR 把带汉字且无假名的识别片段计作可能的中文字幕。",
        "`T_asr` 的规则判断用字对齐近似映射到人工行，chrF 与 COMET 按文档。其余条件按行评测。",
        "chrF 和 COMET 的置信区间按视频文档重采样，保留同一文档内部的各行。",
        "数据量少的单元格用 † 标出。功效列是未校正双侧检验设计下的近似最小差异，仅作量级参考。",
    ]
    prepared = read_json(run.parent / prepare_id / "manifest.json")
    body.extend(("", "## 发布语料问句分层", "", "| 分组 | 行数 |", "|---|---:|"))
    for group, count in prepared.get("question_groups", {}).items():
        body.append(
            f"| {group} | {count} | <!-- run={prepare_id} file=manifest.json key=question_groups.{group} value={count} -->"
        )
    header = (
        "| 分层 | 条件 | 指标 | 样本数 | 数值 | 95% CI 下界 | 95% CI 上界 |",
        "|---|---|---|---:|---:|---:|---:|",
    )
    body.extend(("", "## 各条件结果", "", *header))
    q5 = []
    for row in summary:
        if row["value"] is None:
            continue
        metrics = [name for name in ("n", "value", "ci_low", "ci_high") if row[name] is not None]
        notes = " ".join(
            f"<!-- run={run_id} file=summary.csv key=id:{row['id']}:{name} value={row[name]} -->" for name in metrics
        )
        values = [row[name] if row[name] is not None else "—" for name in ("value", "ci_low", "ci_high")]
        target = q5 if row["metric"] == "Q5" else body
        target.append(
            "| "
            + " | ".join(
                map(
                    str,
                    (
                        row["stratum"],
                        row["condition"],
                        row["metric"] + (" †" if row["small_sample"] else ""),
                        row["n"],
                        *values,
                    ),
                )
            )
            + " | "
            + notes
        )
    if q5:
        body.extend(("", "## Q5 无文字疑问标记", "", *header, *q5))
    if tested:
        body.extend(
            (
                "",
                "## 配对检验",
                "",
                "| 指标 | 条件 | 样本数 | b | c | p | Holm p | 可检测差异（百分点） |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
            )
        )
        for row in tested:
            names = ("n", "b", "c", "p", "p_holm", "detectable_pp_approx")
            notes = " ".join(
                f"<!-- run={run_id} file=comparisons.csv key=id:{row['id']}:{name} value={row[name]} -->"
                for name in names
                if row[name] is not None
            )
            values = [row[name] if row[name] is not None else "—" for name in names]
            body.append(
                "| " + " | ".join(map(str, (row["metric"], f"{row['left']} / {row['right']}", *values))) + " | " + notes
            )
    if comet_deltas:
        body.extend(
            (
                "",
                "## COMET 配对比较",
                "",
                "| 条件 | 样本数 | 差值 | 95% CI 下界 | 95% CI 上界 |",
                "|---|---:|---:|---:|---:|",
            )
        )
        for row in comet_deltas:
            names = ("n", "delta", "ci_low", "ci_high")
            notes = " ".join(
                f"<!-- run={run_id} file=comet_comparisons.csv key=condition:{row['condition']}:{name} value={row[name]} -->"
                for name in names
            )
            body.append("| " + " | ".join([row["condition"], *(str(row[name]) for name in names)]) + " | " + notes)
    elif not any(row["metric"] == "comet" for row in summary):
        body.extend(("", "COMET 未运行。"))
    figures = reports / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    for metric, label in (("recall", "问句判定召回率"), ("style", "文体一致率")):
        values = [
            row for row in summary if row["metric"] == metric and row["stratum"] == "main" and row["value"] is not None
        ]
        if values:
            filename = f"{run_id}-{metric}.png"
            plot_proportion(values, label, figures / filename)
            body.extend(("", f"![{label}](figures/{filename})"))
    write_text(reports / "eval_wmt26.md", "\n".join(body) + "\n")


def plot_proportion(rows, title, path):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    values = [row["value"] for row in rows]
    lower = [max(0, row["value"] - row["ci_low"]) for row in rows]
    upper = [max(0, row["ci_high"] - row["value"]) for row in rows]
    with matplotlib.rc_context({"font.family": "Noto Sans CJK SC"}):
        figure, axis = plt.subplots(figsize=(16, 9), layout="constrained")
        axis.bar([row["condition"] for row in rows], values, yerr=[lower, upper], capsize=5)
        axis.set(title=title, ylim=(0, 1.06), ylabel="比例与 95% 置信区间")
        figure.savefig(path, dpi=100)
        plt.close(figure)
