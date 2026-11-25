import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cjvmt import cli
from cjvmt.backends.local_omni import LocalOmni
from cjvmt.backends.mock import Mock
from cjvmt.clip import read_wav
from cjvmt.config import Settings
from cjvmt.doctor import deployment
from cjvmt.media import ffmpeg, probe
from cjvmt.pipeline import run_pipeline
from cjvmt.translate import (
    FOUR,
    Translator,
    choose_mismatch,
    observation_data,
    parse_document,
    prompts_for,
    select_media,
)


def test_media_isolation_and_cache(tmp_path: Path):
    settings = replace(Settings.load(), root=tmp_path)
    backend = Mock()
    translator = Translator(settings, backend)
    row = {"clip": {"path_v": "silent.mp4", "path_audio_gated": "gated.wav", "path_av": "muxed.mp4"}}
    for path in row["clip"].values():
        (tmp_path / path).write_text(path)
    for retry in range(2):
        for condition in FOUR:
            _, logs = translator.line(
                item_id="video:L001",
                direction="ja2zh",
                mode="compare",
                condition=condition,
                source="いいよ。",
                context=[],
                media=select_media(condition, tmp_path, row),
            )
            assert logs[0]["cache_hit"] is bool(retry)
    assert len(backend.calls) == 4
    assert [
        (request.audio is not None, request.video is not None, request.interleaved) for request in backend.calls
    ] == [(False, False, False), (True, False, False), (False, True, False), (False, True, True)]


def test_prompts_change_only_modality_and_observation_is_passed(tmp_path: Path):
    settings = replace(Settings.load(), root=tmp_path)
    text = "田中さんも出ます。"
    expected = prompts_for(settings, "ja2zh", "compare", "T_neutral", text, [])
    for condition in FOUR[1:]:
        system, user = prompts_for(settings, "ja2zh", "compare", condition, text, [])
        assert (system, user.replace(settings.prompts["modality_note"][condition], "", 1)) == expected
    assert "请保留说话人的语气" not in expected[1]
    assert "请保留说话人的语气" in prompts_for(settings, "ja2zh", "guided", "T+A", text, [])[1]
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"test audio")
    backend = Mock()
    output, logs = Translator(settings, backend).line(
        item_id="one",
        direction="ja2zh",
        mode="perceive",
        condition="T+A",
        source=text,
        context=[],
        media={"audio": wav},
    )
    assert [log["task"] for log in logs] == ["observe", "translate"]
    assert output["observation"]["tone"] == "怀疑" and output["observation"]["strength"] == "中"
    assert output["observation"]["text"] in backend.calls[1].user


def test_strict_document_parser_and_fallback(tmp_path):
    class BrokenDoc(Mock):
        def generate(self, request):
            if request.task == "document":
                return {"text": "2. 不符合编号", "peak_vram_mib": None, "output_tokens": None}
            return super().generate(request)

    settings = replace(Settings.load(), root=tmp_path)
    assert parse_document("1. 一行\n2．二行", 2) == ["一行", "二行"]
    for malformed in ("2. 一行\n1. 二行", "1. 一行\n1. 二行", "开头\n1. 一行\n2. 二行"):
        assert parse_document(malformed, 2) is None
    outputs, logs, fallback = Translator(settings, BrokenDoc()).document(
        item_id="x", direction="ja2zh", condition="T_neutral", lines=["いいよ。", "本当？"], media={}
    )
    assert outputs == ["好啊。", "真的吗？"] and fallback == 2
    assert [log.get("fallback", False) for log in logs] == [False, False, True, True]


def test_mismatch_excludes_same_source():
    same = (Path("a"), {}, "first")
    other = (Path("b"), {}, "second")
    assert choose_mismatch("first", [same, other], 2026) == other
    with pytest.raises(ValueError):
        choose_mismatch("first", [same], 2026)


def test_observation_labels_without_field_names_and_tone_rules():
    from cjvmt.checks import inspect_text

    settings = Settings.load()
    result = observation_data("1. 下降\n2. 无\n3. 不确定\n4. **平静，弱**", settings)
    assert result["tone"] == "平静" and result["strength"] == "弱"
    assert inspect_text("（生气地）行了。", "ja2zh")["emotion_aside"]
    assert not inspect_text("这是真的（昨天确认）。", "ja2zh")["emotion_aside"]
    assert inspect_text("真的吗", "ja2zh", {"tone": "惊讶"})["tone_punctuation_warning"]


def test_local_omni_deployment_preserves_venv_symlink(tmp_path, monkeypatch):
    settings = Settings.load()
    monkeypatch.chdir(tmp_path)
    python = tmp_path / "bin" / "python"
    python.parent.mkdir()
    python.symlink_to(sys.executable)
    weights = tmp_path / "test-revision"
    weights.mkdir()
    (weights / "config.json").write_text("{}")
    (weights / "receipt.json").write_text('{"model": "fixture-qwen3", "revision": "test-revision"}')
    monkeypatch.setenv("CJVMT_LOCAL_OMNI_PYTHON", "bin/python")
    monkeypatch.setenv("CJVMT_LOCAL_OMNI_PATH", str(weights))
    assert LocalOmni(settings).helper == python
    assert any("fixture-qwen3" in line for line in deployment(settings))

    monkeypatch.setenv("CJVMT_LOCAL_OMNI_PYTHON", str(tmp_path / "missing-python"))
    with pytest.raises(ValueError, match="找不到可执行的 vLLM-Omni Python"):
        LocalOmni(settings)


def test_translation_cli_stages(tmp_path, monkeypatch):
    settings = replace(Settings.load(), root=tmp_path)
    monkeypatch.setattr(cli, "settings", lambda: settings)
    source = tmp_path / "synthetic_cli.mp4"
    ffmpeg(
        settings,
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=25:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo:d=4",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
            "-disposition:a:0",
            "0",
            "-disposition:a:1",
            "default",
            "-shortest",
            str(source),
        ],
    )
    runner = CliRunner()
    media = tmp_path / "media"
    clips = tmp_path / "clips"
    asr_path = tmp_path / "asr.jsonl"
    segments = tmp_path / "segments.jsonl"
    result = runner.invoke(cli.app, ["media", "--input", str(source), "--output", str(media)])
    assert result.exit_code == 0, result.output
    # The first track carries speech input and the replay audio, even with a default stereo track.
    assert read_wav(media / "audio.wav")[0].any()
    duration = str(json.loads(result.output)["duration_ms"])
    for args in (
        ["asr", "--input", str(media / "audio.wav"), "--output", str(asr_path)],
        ["segment", "--input", str(asr_path), "--duration-ms", duration, "--output", str(segments)],
        ["clip", "--input", str(segments), "--media", str(media), "--output", str(clips)],
    ):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 0, (result.output, result.exception)

    result = runner.invoke(
        cli.app, ["translate", "--input", str(clips / "clips.jsonl"), "--conditions", ",".join(FOUR)]
    )
    assert result.exit_code == 0, (result.output, result.exception)
    first, second = [json.loads(line) for line in result.output.splitlines()]
    assert first["model"]["backend"] == "mock"
    assert first["text_asr"] == "いいよ。" and first["outputs"]["T_neutral"]["text"] == "好啊。"
    assert second["text_asr"].endswith("？") and second["text_neutral"].endswith("。")
    assert list(first["outputs"]) == list(FOUR)
    assert [first["outputs"][key]["calls"][-1]["media_types"] for key in FOUR] == [[], ["audio"], ["video"], ["video"]]
    assert first["outputs"]["T+V+A"]["calls"][-1]["use_audio_in_video"]
    assert not probe(clips / first["clip"]["path_v"], settings)["has_audio"]
    assert probe(clips / first["clip"]["path_av"], settings)["has_audio"]

    progress = []
    run_pipeline(
        settings,
        source,
        "auto",
        "mock",
        "compare",
        list(FOUR),
        2,
        on_progress=lambda message, done, total: progress.append((message, done, total)),
    )
    assert progress[0][0] == "正在读取视频和音轨" and progress[-1][0] == "正在导出字幕和回放"
    assert [(done, total) for _, done, total in progress if total is not None] == [(i, 8) for i in range(9)]
