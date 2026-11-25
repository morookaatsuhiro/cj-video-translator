from pathlib import Path
from zipfile import ZipFile

from cjvmt.config import Settings
from cjvmt.store import write_jsonl
from cjvmt.wmt import align_endings, load_release


def test_wmt26_alignment_and_question_groups(tmp_path: Path):
    source = tmp_path / "mini.jsonl"
    write_jsonl(
        source,
        [
            {
                "src_lang": "zho_Hans",
                "tgt_lang": "jpn_Jpan",
                "domain": "speech",
                "doc_id": "demo",
                "src_text": "你也去？\n她好吗。\n山有。",
                "gold_transcript": "你也去？\n她好吗？\n山有雪？",
                "refs": {"refPE": "行くの？\n元気？\n雪がある？"},
                "video": {"path": "example.mp4"},
            }
        ],
    )
    pack = tmp_path / "media.zip"
    with ZipFile(pack, "w") as archive:
        archive.writestr("example.mp4", "test media name")
    result = load_release(Settings.load(), "wmt26", source, pack)
    assert result.rows[0]["alignment"] == [(0, True), (1, False), None]
    assert result.summary["media_available"] == 1
    assert result.summary["counts"]["gold_question_lines"] == 3
    assert result.summary["question_groups"] == {"Q1": 1, "Q5": 2}
    assert align_endings(["一。", "二？"], "一。\n二？") == [(0, False), (1, True)]


def test_wmt25_one_record_is_not_sentence_alignment(tmp_path: Path):
    source = tmp_path / "mini.jsonl"
    write_jsonl(
        source,
        [
            {
                "src_lang": "ja",
                "tgt_lang": "zh_CN",
                "domain": "speech",
                "doc_id": "j",
                "src_text": "最初。次。",
                "refs": {"refA": {"ref": "第一句话。第二句话。"}},
                "video": "assets/j.mp4",
            }
        ],
    )
    report = load_release(Settings.load(), "wmt25", source)
    assert report.summary["counts"] == {
        "speech_docs": 1,
        "docs_with_sentence_alignment": 0,
        "docs_with_one_line_source_and_ref": 1,
    }


def test_wmt26_controlled_run_resume_and_subtitle_strata(tmp_path):
    import csv
    from dataclasses import replace

    from cjvmt.config import file_hash
    from cjvmt.media import ffmpeg
    from cjvmt.store import read_json, read_jsonl, write_json
    from cjvmt.wmt import record_release
    from cjvmt.wmt26_score import report_wmt26
    from cjvmt.wmt_eval import evaluate

    settings = replace(Settings.load(), root=tmp_path)
    settings.values["eval"]["wmt26"]["expect"] = {}
    settings.values["eval"]["bootstrap_n"] = 100
    media = tmp_path / "media"
    media.mkdir()
    for index in (1, 2):
        ffmpeg(
            settings,
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=80x80:d=2",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={index * 400}:duration=2",
                "-shortest",
                str(media / f"doc{index}.mp4"),
            ],
        )
    source = tmp_path / "synthetic.jsonl"
    write_jsonl(
        source,
        [
            {
                "src_lang": "zho_Hans",
                "tgt_lang": "jpn_Jpan",
                "domain": "speech",
                "doc_id": str(index),
                "src_text": "\n你明天去？\n\n我也去。",
                "gold_transcript": "你明天去？\n我也去。",
                "refs": {"refPE": "明日行く？\n私も行きます。"},
                "video": {"path": f"doc{index}.mp4"},
            }
            for index in (1, 2)
        ],
    )
    release = load_release(settings, "wmt26", source, media)
    prepared = record_release(settings, release, source, media)
    audit = prepared / "ocr.json"
    write_json(
        audit,
        {
            "prepare_run": prepared.name,
            "model": "synthetic",
            "documents": [
                {
                    "doc_id": str(index),
                    "media_sha256": file_hash(media / f"doc{index}.mp4"),
                    "japanese": index == 2,
                    "chinese": index == 1,
                    "chinese_question": index == 1,
                }
                for index in (1, 2)
            ],
        },
    )
    run = evaluate(settings, prepared, "wmt26", "mock", None, audit=audit, mismatch=True)
    predictions = read_jsonl(run / "predictions.jsonl")
    assert {row["condition"]: row["media_types"] for row in predictions if row["doc_id"] == "1"} == {
        "T_neutral": [],
        "T_asr": [],
        "T+A": ["audio"],
        "T+V": ["video"],
        "T+V+A": ["video"],
        "T+A_shuf": ["audio"],
        "T+V_shuf": ["video"],
    }
    rows = read_jsonl(run / "documents.jsonl")
    assert len(rows) == 14
    assert all(row["mismatch_doc_id"] != row["doc_id"] for row in rows if row["condition"].endswith("_shuf"))
    assert all("？" in row["source"] for row in rows if row["condition"] == "T_asr")
    assert all("？" not in row["source"] for row in rows if row["condition"] != "T_asr")
    scored = read_jsonl(run / "line_scores.jsonl")
    assert all("main" in row["strata"] for row in scored if row["doc_id"] == "1")
    assert all(row["strata"] == ["japanese_onscreen"] for row in scored if row["doc_id"] == "2")
    assert any(row["approximate"] and row["question"] for row in scored)
    assert "p_holm" in (run / "comparisons.csv").read_text()
    assert evaluate(settings, prepared, "wmt26", "mock", None, audit=audit, mismatch=True, resume=run) == run
    assert read_jsonl(run / "documents.jsonl") == rows
    manifest = read_json(run / "manifest.json")
    assert not (settings.path("reports") / "eval_wmt26.md").exists()
    core = {
        "n": 2,
        "value": 0.5,
        "ci_low": 0.09453,
        "ci_high": 0.90547,
        "small_sample": True,
        "stratum": "main",
        "condition": "T_neutral",
    }
    report_wmt26(
        run,
        settings.path("reports"),
        manifest,
        [{**core, "metric": metric, "id": f"main/T_neutral/{metric}"} for metric in ("recall", "style")],
        [],
        [{"condition": "T+A", "n": 2, "delta": 0.01, "ci_low": -0.01, "ci_high": 0.03}],
    )
    report = settings.path("reports") / "eval_wmt26.md"
    assert "COMET 配对比较" in report.read_text()
    assert len(list((report.parent / "figures").glob("*.png"))) == 2
    with (run / "summary.csv").open() as stream:
        summary = list(csv.DictReader(stream))
        assert {item["stratum"] for item in summary} >= {"main", "japanese_onscreen"}
        chrf = next(row for row in summary if row["stratum"] == "main" and row["metric"] == "chrf")
        assert chrf["value"] == chrf["ci_low"] == chrf["ci_high"]
