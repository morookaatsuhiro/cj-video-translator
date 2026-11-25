import csv
import io
import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from cjvmt.cli import app
from cjvmt.config import Settings
from cjvmt.demo import export_site, run_data
from cjvmt.media import ffmpeg
from cjvmt.pairs import read_pairs, run_pairs
from cjvmt.store import read_json, read_jsonl, write_json


def test_pairs_use_same_transcript_and_preserve_manual_review(tmp_path):
    settings = replace(Settings.load(), root=tmp_path)
    source = tmp_path / "pairs.csv"
    header = "pair_id,variant,direction,sentence,context,expected_reading\n"
    source.write_text(header + "pair01,A,ja2zh,いいよ。,前の文,接受\npair01,B,ja2zh,違う。,前の文,谢绝\n")
    with pytest.raises(ValueError, match="相同语向、台词和前文"):
        read_pairs(source, settings)
    source.write_text(header + "pair01,A,ja2zh,いいよ。,前の文,接受\npair01,B,ja2zh,いいよ。,前の文,谢绝\n")
    for variant, frequency in (("A", "440"), ("B", "660")):
        ffmpeg(
            settings,
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x64:d=1",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration=1",
                "-shortest",
                str(tmp_path / f"pair01_{variant}.mp4"),
            ],
        )
    batch = run_pairs(settings, source, "mock", "perceive")
    manifest = read_json(batch / "manifest.json")
    assert manifest["status"] == "complete"
    reviews = batch / "pairs.csv"
    rows = list(csv.DictReader(io.StringIO(reviews.read_text())))
    assert [row["expected_reading"] for row in rows] == ["接受", "谢绝"]
    assert {row["T_neutral"] for row in rows} == {"好啊。"}
    for row in rows:
        run = batch.parent / row["run_id"]
        assert read_jsonl(run / "lines.jsonl")[0]["text"]["gold"] == "いいよ。"
        assert run_data(run, read_json(run / "manifest.json"), ".")["pair"]["review"] == "待人工核对"
        assert row["review_T+V+A"] == "待人工核对"
    rows[0]["review"] = "符合"
    rows[0].update({"review_T_neutral": "不符合", "review_T+A": "符合", "review_T+V+A": "不确定"})
    rows[1].update({"review_T_neutral": "符合", "review_T+A": "符合"})
    with reviews.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    first = batch.parent / rows[0]["run_id"]
    review = run_data(first, read_json(first / "manifest.json"), ".")["pair"]
    assert review["review"] == "符合" and review["reviews"]["T_neutral"] == "不符合"
    assert first.name not in export_site(settings).read_text()
    runner = CliRunner()
    args = ["pairs", "--review-run", str(batch)]
    blocked = runner.invoke(app, args)
    assert blocked.exit_code != 0 and "Mock 结果仅供演示" in blocked.output

    manifest["model"]["backend"] = "test-fixture"
    write_json(batch / "manifest.json", manifest)
    report = runner.invoke(app, args)
    assert report.exit_code == 0, report.output
    summary = json.loads(report.output)["conditions"]
    assert summary["T_neutral"]["both_variants_fit"] == 0
    assert summary["T+A"]["paired_with_text"] == summary["T+A"]["improved"] == 1
    assert summary["T+V+A"]["decided_pairs"] == 0 and summary["T+V+A"]["uncertain_variants"] == 1

    (tmp_path / "pair01_B.mp4").write_bytes((tmp_path / "pair01_A.mp4").read_bytes())
    with pytest.raises(ValueError, match="A、B 视频内容相同"):
        run_pairs(settings, source, "mock", "guided")
