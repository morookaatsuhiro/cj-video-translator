from difflib import SequenceMatcher
from pathlib import Path

from cjvmt.store import read_json, read_jsonl, write_json, write_jsonl


def difference(original: str, translation: str) -> list[dict]:
    matcher = SequenceMatcher(a=original, b=translation, autojunk=False)
    return [
        {"text": translation[j:j2], "changed": tag != "equal"} for tag, _, _, j, j2 in matcher.get_opcodes() if j2 > j
    ]


def compare_run(run: Path, manifest: dict | None = None) -> Path:
    update_manifest = manifest is None
    manifest = manifest or read_json(run / "manifest.json")
    rows = read_jsonl(run / "lines.jsonl")
    for row in rows:
        outputs, checks = row["outputs"], row["checks"]
        baseline = outputs.get("T_neutral", {}).get("text")
        reference = checks.get("T_neutral", {})
        row["compare"] = {}
        for condition, result in outputs.items():
            text = result["text"]
            current = checks[condition]
            row["compare"][condition] = {
                "different": baseline is not None and baseline != text,
                "parts": difference(text if baseline is None else baseline, text),
                "question_changed": "question" in reference and current.get("question") != reference["question"],
                "style_changed": "style" in reference and current.get("style") != reference["style"],
            }
    write_jsonl(run / "lines.jsonl", rows)
    manifest["compare_status"] = "complete"
    if update_manifest:
        write_json(run / "manifest.json", manifest)
    return run
