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


@pytest.mark.parametrize("media_type", ["frames", "video", "audio", "text"])
def test_visual_grounding_is_scoped_to_visible_media(tmp_path, media_type):
    class Observer(Mock):
        def generate(self, request):
            result = super().generate(request)
            if request.task == "observe" and "5." in request.user:
                result["text"] += "\n5. 碗边是一双筷子"
            return result

    settings = replace(Settings.load(), root=tmp_path)
    backend = Observer()
    translator = Translator(settings, backend)
    path = tmp_path / "frame.ppm"
    path.write_bytes(b"P6\n48 32\n255\n" + (bytes([80, 10, 50]) * 24 + bytes([20, 90, 230]) * 24) * 32)
    if media_type == "video":
        video = tmp_path / "scene.mp4"
        ffmpeg(settings, ["-loop", "1", "-i", str(path), "-t", "0.6", "-pix_fmt", "yuv420p", str(video)])
        path = video
    media = {} if media_type == "text" else {media_type: (path,) if media_type == "frames" else path}
    condition = {"video": "T+V", "frames": "T+V+A_sep", "audio": "T+A", "text": "T_neutral"}[media_type]
    args = dict(item_id="ground", direction="ja2zh", condition=condition, source="はし", context=[], media=media)
    visual = media_type in {"video", "frames"}
    for enabled in (True, False):
        settings.values["prompt_modes"]["object_grounding"] = enabled
        for mode in ("guided", "perceive", "compare"):
            result, logs = translator.line(mode=mode, **args)
            assert len(logs) == 1 + int(mode == "perceive" and bool(media))
            final = backend.calls[-1].user
            assert (settings.prompts["visual_object_note"] in final) == (visual and enabled and mode != "compare")
            assert bool((result["observation"] or {}).get("object_evidence")) == (
                visual and enabled and mode == "perceive"
            )


@pytest.mark.parametrize("with_audio", [True, False])
def test_uniform_video_abstains_from_object_observation(tmp_path, with_audio):
    settings = replace(Settings.load(), root=tmp_path)
    frame = tmp_path / "gray.ppm"
    frame.write_bytes(b"P6\n48 32\n255\n" + bytes([120]) * 48 * 32 * 3)
    video = tmp_path / "gray.mp4"
    ffmpeg(settings, ["-loop", "1", "-i", str(frame), "-t", "0.3", "-pix_fmt", "yuv420p", str(video)])
    backend = Mock()
    media = {"video": video, "interleaved": True} if with_audio else {"frames": (frame,)}
    out, logs = Translator(settings, backend).line(
        item_id="blank",
        source="はし",
        context=[],
        direction="ja2zh",
        mode="perceive",
        condition="T+V+A" if with_audio else "T+V",
        media=media,
    )
    assert "无法辨认" in out["observation"]["object_evidence"]
    assert [x["task"] for x in logs] == (["observe", "translate"] if with_audio else ["translate"])
    assert all("5." not in request.user for request in backend.calls)
    assert settings.prompts["blank_visual_note"] in backend.calls[-1].user


def test_scoped_video_glossary_and_joint_subtitle_checks(tmp_path):
    from cjvmt.config import parse_glossary

    glossary = parse_glossary("唐河县 | 唐河県\n方言 | 方言 | ほうげん")
    settings = replace(Settings.load(), root=tmp_path, glossary=glossary)
    for bad in ("没有分隔符", "a | x\na | y", "a | x | invalid", "a | x | | b"):
        with pytest.raises(ValueError):
            parse_glossary(bad)
    assert "唐河県" in settings.terminology_note("来自唐河县") and settings.terminology_note("来自汤鹤县") == ""
    legacy = replace(
        settings, glossary=({"source": "唐河县", "target": "唐河県", "reading": "", "aliases": ["汤鹤县"]},)
    )
    assert not legacy.matched_terms("来自汤鹤县")

    class JointBackend(Mock):
        response = "1. 唐河県は\\n2. 両省の境にあります。"

        def generate(self, request):
            self.calls.append(request)
            if request.task == "joint_translation":
                return {
                    "text": '{"action":"join","translation":"唐河県は両省の境にあります。"}',
                    "peak_vram_mib": None,
                    "output_tokens": None,
                }
            return {"text": self.response.replace("\\n", "\n"), "peak_vram_mib": None, "output_tokens": None}

    backend = JointBackend()
    tr = Translator(settings, backend)
    for gap, response, adopt in [
        (10, "1. 唐河県は\n2. 両省の境にあります。", True),
        (20, "唐河県は境にあります。", False),
        (30, "1. あの県は\n2. 境です。", False),
        (900, "", False),
    ]:
        backend.response = response
        a, b = {"text": "唐河県は境にあります。"}, {"text": "交差点です。"}
        pred = tr.refine_boundary(
            a,
            b,
            left_source="唐河县位于两省的",
            right_source="交界处",
            gap_ms=gap,
            direction="zh2ja",
            condition="T+V+A_sep",
            item_id=str(gap),
        )
        assert ("joint_with" in a) == ("joint_with" in b) == adopt
        assert bool(pred) == (gap < 650)
        if adopt:
            assert (a["text"], b["text"]) == ("唐河県は", "両省の境にあります。")


@pytest.mark.parametrize(
    ("action", "left", "right", "adopt"),
    [
        ('{"action":"join","translation":"記録を消します。"}', "现在请把所有记录。", "全部删除。", True),
        ('{"action":"keep"}', "门锁好了。", "月亮升起来了。", False),
        ("格式错误", "月が出ました。", "鍵を探しています。", False),
    ],
)
def test_boundary_uses_both_utterances_without_registered_terms(tmp_path, action, left, right, adopt):
    class Judge(Mock):
        def generate(self, req):
            if req.task == "joint_translation":
                assert left in req.user and right in req.user
                return {"text": action}
            assert req.task == "joint_captions"
            return {"text": "1. 記録を\n2. 消します。"}

    tr = Translator(replace(Settings.load(), root=tmp_path), Judge())
    a, b = {"text": "A"}, {"text": "B"}
    calls = tr.refine_boundary(
        a, b, left_source=left, right_source=right, gap_ms=0, direction="zh2ja", condition="T_asr", item_id="blind"
    )
    assert len(calls) == (2 if adopt else 1)
    assert bool(a.get("joint_with")) == adopt
    if not adopt:
        assert (a["text"], b["text"]) == ("A", "B")
