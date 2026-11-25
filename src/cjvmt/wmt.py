"""Load official WMT releases and reproduce the source-side audit."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from cjvmt.config import Settings, file_hash
from cjvmt.store import base_manifest, new_run, now, read_jsonl, write_json, write_jsonl


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def safe_member(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not value or "\\" in value:
        raise ValueError(f"无效的 WMT 媒体路径 {value!r}")
    return path.as_posix()


def letter_positions(text: str) -> list[int]:
    return [i for i, char in enumerate(text) if not char.isspace() and not unicodedata.category(char).startswith("P")]


def align_endings(gold: list[str], asr: str) -> list[tuple[int, bool] | None]:
    positions = letter_positions(asr)
    source = "".join(asr[index] for index in positions)
    parts = ["".join(line[index] for index in letter_positions(line)) for line in gold]
    matches = SequenceMatcher(None, "".join(parts), source, autojunk=False).get_matching_blocks()
    lookup = {left + i: right + i for left, right, length in matches for i in range(length)}
    end = 0
    result = []
    for part in parts:
        end += len(part)
        if not part or end - 1 not in lookup:
            result.append(None)
            continue
        right = lookup[end - 1]
        char_index = positions[right]
        next_index = positions[right + 1] if right + 1 < len(positions) else len(asr)
        suffix = asr[char_index + 1 : next_index]
        result.append(
            (
                sum(bool(line.strip()) for line in asr[:char_index].split("\n")[:-1]),
                any(mark in suffix.split("\n", 1)[0] for mark in "？?"),
            )
        )
    return result


def question_group(text: str, config: dict) -> str:
    text = text.rstrip("？?。！! ")
    if text.endswith(tuple(config["q1_tag_endings"])):
        return "Q1"
    if text.endswith(tuple(config["q2_final_particles"])):
        return "Q2"
    if re.search(config["q3_a_not_a_regex"], text):
        return "Q3"
    if any(marker in text for marker in config["q4_wh_words"]):
        return "Q4"
    return "Q5"


@dataclass(frozen=True)
class Release:
    name: str
    rows: list[dict]
    summary: dict
    schema: dict


def load_release(settings: Settings, dataset: str, source: Path, media: Path | None = None) -> Release:
    config = settings.values["eval"][dataset]
    raw = read_jsonl(source)
    pair = [row for row in raw if (row["src_lang"], row["tgt_lang"]) == (config["src_lang"], config["tgt_lang"])]
    speech = [row for row in pair if row["domain"] == config["domain"]]
    if not speech:
        raise ValueError("该文件没有匹配指定翻译方向的 speech 记录。")
    if len({row["doc_id"] for row in speech}) != len(speech):
        raise ValueError("WMT speech 记录含重复 doc_id。")
    counts = Counter(row["domain"] for row in pair)
    metrics = {"speech_docs": len(speech)}
    sections = Counter()
    align = Counter()
    mapped = 0
    rows = []
    members = None
    if media and media.is_file():
        with ZipFile(media) as archive:
            members = set(archive.namelist())
    for raw_row in speech:
        v26 = dataset == "wmt26"
        goals = lines(raw_row["gold_transcript"]) if v26 else []
        path = safe_member(raw_row["video"]["path"] if v26 else raw_row["video"])
        reference = lines(raw_row["refs"]["refPE"] if v26 else raw_row["refs"]["refA"]["ref"])
        asr = lines(raw_row["src_text"])
        if not asr or not reference or (v26 and not goals):
            raise ValueError(f"{raw_row['doc_id']} 缺少转写或参考译文。")
        row = {
            "doc_id": raw_row["doc_id"],
            "video": path,
            "asr": asr,
            "gold": goals,
            "reference": reference,
            "has_media": path in members if members is not None else bool(media and (media / path).is_file()),
        }
        if v26:
            raw_align = align_endings(goals, raw_row["src_text"])
            group = [
                question_group(text, settings.values["eval"]["zh_question_markers"])
                if text.endswith(("？", "?"))
                else None
                for text in goals
            ]
            sections.update(value for value in group if value is not None)
            for label, entry in zip(group, raw_align, strict=True):
                if label is not None:
                    align["misaligned" if entry is None else ("same_pos" if entry[1] else "missing")] += 1
            row.update(question_group=group, alignment=raw_align)
        else:
            mapped += len(asr) == len(reference) and len(asr) > 1
        rows.append(row)
    if dataset == "wmt26":
        metrics.update(
            docs_zh_ja=len(pair),
            **counts,
            social_with_screenshot=sum(bool(row.get("screenshot")) for row in pair if row["domain"] == "social"),
            speech_gold_lines=sum(len(row["gold"]) for row in rows),
            docs_gold_ref_line_match=sum(len(row["gold"]) == len(row["reference"]) for row in rows),
            gold_question_lines=sum(sections.values()),
            **{f"asr_qmark_{key}": align[key] for key in ("same_pos", "missing", "misaligned")},
        )
    else:
        metrics.update(
            docs_with_sentence_alignment=mapped,
            docs_with_one_line_source_and_ref=sum(len(row["asr"]) == len(row["reference"]) == 1 for row in rows),
        )
    discrepancies = {
        key: {"expected": value, "actual": metrics.get(key)}
        for key, value in config["expect"].items()
        if metrics.get(key) != value
    }
    summary = {
        "counts": metrics,
        "question_groups": dict(sorted(sections.items())),
        "media_available": sum(row["has_media"] for row in rows),
        "discrepancies": discrepancies,
    }
    schema = {
        "fields": sorted(speech[0]),
        "reference_keys": sorted(speech[0]["refs"]),
        "video_type": type(speech[0]["video"]).__name__,
    }
    return Release(dataset, rows, summary, schema)


def record_release(settings: Settings, release: Release, source: Path, media: Path | None) -> Path:
    path = new_run(settings, f"{release.name}-prepare")
    manifest = base_manifest(settings, path)
    manifest.update(
        kind="wmt_prepare",
        dataset=release.name,
        source_sha256=file_hash(source),
        schema=release.schema,
        model={"backend": "metadata", "model": None},
        status="complete",
        ended_at=now(),
        media_path=str(media.resolve()) if media else None,
        **release.summary,
    )
    if media and media.is_file():
        manifest["media_sha256"] = file_hash(media)
    write_jsonl(path / "corpus.jsonl", release.rows)
    write_json(path / "manifest.json", manifest)
    return path
