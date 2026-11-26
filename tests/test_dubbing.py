import json
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from cjvmt import dubbing
from cjvmt.asr import MockASR
from cjvmt.clip import read_wav, write_wav
from cjvmt.config import Settings
from cjvmt.media import ffmpeg, media_command, probe
from cjvmt.prosody import transfer_f0
from cjvmt.store import write_json, write_jsonl


@pytest.mark.parametrize("grounding", [True, False])
def test_realtime_dub_retranslation_has_aligned_media(tmp_path, monkeypatch, grounding):
    settings = Settings.load()
    settings.values["prompt_modes"]["object_grounding"] = grounding
    media = tmp_path / "media"
    media.mkdir()
    source = media / "source.mp4"
    ffmpeg(settings, ["-f", "lavfi", "-i", "testsrc2=s=160x90:d=1", "-f", "lavfi", "-i", "sine=d=1", str(source)])
    ffmpeg(settings, ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(media / "audio.wav")])
    write_jsonl(tmp_path / "session/session.jsonl", [])

    class RecordingTranslator:
        def line(self, *, condition, media, **kwargs):
            assert set(media) == ({"video", "audio"} if grounding else {"audio"})
            assert condition == ("T+V+A_sep" if grounding else "T+A")
            wave, sr = read_wav(media["audio"])
            assert 0.95 <= len(wave) / sr <= 1.05
            if grounding:
                info = probe(media["video"], settings)
                assert info["width"] == 160 and not info["has_audio"]
                assert abs(info["duration_ms"] - 1000 * len(wave) / sr) <= 40
            obs = {"tone": "平静", "strength": "弱"}
            if grounding:
                obs["object_evidence"] = "画面无法确定所指"
            return {"text": "好啊。", "observation": obs}, []

    def synthesize(_settings, _work, requests, _log, _on_progress):
        (request,) = requests
        tone = (4000 * np.sin(np.arange(24000) * (2 * np.pi * 440 / 24000))).astype("<i2")
        write_wav(Path(request["output"]), tone, 24000)

    monkeypatch.setattr(dubbing, "synthesize", synthesize)
    result = dubbing.generate_dub(
        tmp_path,
        {"kind": "realtime", "direction": "ja2zh"},
        settings,
        RecordingTranslator(),
        lambda: None,
        lambda _: None,
        asr=MockASR(),
    )
    assert result["lines"][0]["multimodal"] == "好啊。"
    assert result["lines"][0].get("object_evidence") == ("画面无法确定所指" if grounding else None)


def test_intonation_moves_phrase_pitch_and_keeps_the_voicing_mask():
    options = Settings.load().values["tts"]["intonation"]
    source = np.r_[np.zeros(10), np.linspace(125.0, 250.0, 100), np.zeros(10)]
    target = np.full(160, 180.0)
    target[70:90] = 0
    adjusted, details = transfer_f0(source, target, options)
    assert details["status"] == "applied"
    assert 0 < details["max_shift_semitones"] <= options["max_shift_semitones"]
    assert adjusted[15] < target[15] < adjusted[-15]
    np.testing.assert_array_equal(adjusted > 0, target > 0)
    unchanged, details = transfer_f0(np.zeros_like(source), target, options)
    assert details["status"] == "insufficient_voicing"
    np.testing.assert_array_equal(unchanged, target)


def test_dub_progress_counts_rendered_lines(tmp_path, monkeypatch):
    settings = Settings.load()
    outputs = [tmp_path / f"voice-{index}.wav" for index in range(2)]
    seen = Event()
    updates = []

    def progress(completed, total):
        updates.append((completed, total))
        if completed == 1:
            seen.set()

    def render(*args, **kwargs):
        temporary = outputs[0].with_suffix(".tmp.wav")
        temporary.write_bytes(b"rendering")
        temporary.replace(outputs[0])
        assert seen.wait(3)
        outputs[1].write_bytes(b"rendered")

    monkeypatch.setattr(dubbing.subprocess, "run", render)
    dubbing.synthesize(settings, tmp_path, [{"output": str(path)} for path in outputs], tmp_path / "dub.log", progress)
    assert updates[0] == (0, 2) and (1, 2) in updates and updates[-1] == (2, 2)


@pytest.mark.parametrize(
    ("direction", "original", "translation", "track"),
    [("zh2ja", "你好。", "こんにちは。", "jpn"), ("ja2zh", "こんにちは。", "你好。", "zho")],
)
def test_dub_retains_original_and_extends_video_for_long_translation(
    tmp_path, monkeypatch, direction, original, translation, track
):
    settings = Settings.load()
    media = tmp_path / "media"
    media.mkdir()
    source = media / "source.mp4"
    ffmpeg(
        settings,
        ["-f", "lavfi", "-i", "color=c=black:size=160x90:d=1", "-f", "lavfi", "-i", "sine=d=1", str(source)],
    )
    ffmpeg(settings, ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(media / "audio.wav")])
    review = {"original": "首次转写", "candidate": original, "applied": True}
    write_jsonl(
        tmp_path / "session/session.jsonl",
        [
            {
                "line_id": "L001",
                "start_ms": 500,
                "end_ms": 900,
                "text_asr": original,
                "multimodal": translation,
                "observation": {"tone": "自然", "strength": "弱"},
                "asr_recheck": review,
            }
        ],
    )
    manifest = {
        "kind": "realtime",
        "status": "complete",
        "direction": direction,
        "line_count": 1,
        "source_kind": "webcam",
        "model": {"backend": "local_omni"},
    }

    def synthesize(_settings, work, requests, _log, _on_progress):
        (request,) = requests
        assert request["text"] == translation
        assert ("日语" if track == "jpn" else "普通话") in request["instruction"]
        sentence, source_rate = read_wav(Path(request["source_sentence"]))
        reference, _ = read_wav(Path(request["reference"]))
        assert len(sentence) == round(source_rate * 0.4) < len(reference)
        rate = settings.values["tts"]["sample_rate"]
        tone = (4000 * np.sin(np.arange(rate * 2) * (2 * np.pi * 440 / rate))).astype("<i2")
        write_wav(Path(request["output"]), tone, rate)
        write_json(
            work / "prosody.json",
            {"model": "CREPE tiny", "applied_lines": 1, "total_lines": 1, "lines": [{"status": "applied"}]},
        )

    released = []
    monkeypatch.setattr(dubbing, "synthesize", synthesize)
    assert not dubbing.eligible(tmp_path, {**manifest, "source_kind": "commons"})
    assert not dubbing.eligible(tmp_path, {**manifest, "model": {"backend": "mock"}})
    assert dubbing.eligible(tmp_path, manifest)
    result = dubbing.generate_dub(tmp_path, manifest, settings, None, lambda: released.append(True), lambda _: None)
    assert released == [True]
    assert result["status"] == "complete"
    assert result["prosody"]["applied_lines"] == 1
    assert result["lines"][0]["intonation"]["status"] == "applied"
    assert result["lines"][0]["asr_recheck"] == review
    assert 1950 < result["lines"][0]["end_ms"] < 2150
    assert translation in (media / "dub.srt").read_text()
    video = media / "dub.mp4"
    assert probe(video, settings)["duration_ms"] >= result["lines"][0]["end_ms"] - 40
    args = ["-v", "error", "-select_streams", "a", "-show_streams", "-of", "json", str(video)]
    first, second = json.loads(media_command("ffprobe", args, 10))["streams"]
    assert first["tags"]["language"] == track and first["disposition"]["default"] == 1
    assert second["tags"]["handler_name"] == "Original" and second["disposition"]["default"] == 0
    assert not (media / "dub.wav").exists()
    assert result["timing"]["total_lines"] == 1
    assert result["lines"][0]["timing"]["atempo"] > 1.0


def test_dub_slows_down_short_translation_to_match_speaker_window(tmp_path, monkeypatch):
    settings = Settings.load()
    media = tmp_path / "media"
    media.mkdir()
    source = media / "source.mp4"
    ffmpeg(
        settings,
        ["-f", "lavfi", "-i", "color=c=black:size=160x90:d=8", "-f", "lavfi", "-i", "sine=d=8", str(source)],
    )
    ffmpeg(settings, ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(media / "audio.wav")])
    write_jsonl(
        tmp_path / "session/session.jsonl",
        [
            {
                "line_id": "L001",
                "start_ms": 1000,
                "end_ms": 6000,
                "text_asr": "こちらは私の中国語と日本語のマルチモーダル翻訳システムのデモです。",
                "multimodal": "这是我的中日多模态翻译演示。",
                "observation": {"tone": "平静", "strength": "弱"},
            }
        ],
    )
    manifest = {
        "kind": "realtime",
        "status": "complete",
        "direction": "ja2zh",
        "line_count": 1,
        "source_kind": "webcam",
        "model": {"backend": "local_omni"},
    }

    def synthesize(_settings, _work, requests, _log, _on_progress):
        (request,) = requests
        # CosyVoice2 receives a slower native speed hint for a short Chinese line in a 5s Japanese slot
        assert request["speed"] < 1.0
        assert "语速稍慢" in request["instruction"]
        rate = settings.values["tts"]["sample_rate"]
        # Simulate a brisk 2.5s TTS output inside a 5.0s speaker window
        tone = (4000 * np.sin(np.arange(round(rate * 2.5)) * (2 * np.pi * 440 / rate))).astype("<i2")
        write_wav(Path(request["output"]), tone, rate)

    monkeypatch.setattr(dubbing, "synthesize", synthesize)
    result = dubbing.generate_dub(tmp_path, manifest, settings, None, lambda: None, lambda _: None)
    timing = result["lines"][0]["timing"]
    # Post-synthesis atempo slows down gently (0.78x) so 2.5s stretches to ~3.2s instead of finishing at 2.5s
    assert timing["atempo"] < 0.85
    assert timing["aligned_dur_ms"] > 3000
    assert result["timing"]["slowed_lines"] == 1
    assert not (media / "dub.wav").exists()


def test_reference_uses_an_aligned_single_speaker_and_never_borrows_neighbours():
    opts = Settings.load().values["tts"]
    rate = 1000
    # Short line, a different person's turn, then a longer clean turn of the first speaker.
    wave = np.r_[np.full(400, 200), np.full(4000, 800), np.full(4000, 300)].astype("<i2")
    row = {"start_ms": 0, "end_ms": 400, "line_id": "L1"}
    labels = {
        "L1": {**row, "speaker_ids": [1], "speech_ms": 400},
        "L2": {"start_ms": 400, "end_ms": 4400, "speaker_ids": [2], "speech_ms": 4000},
        "L3": {"start_ms": 4400, "end_ms": 8400, "speaker_ids": [1], "speech_ms": 3900},
    }
    ref, evidence = dubbing.voice_reference(wave, rate, row, labels, opts)
    assert evidence["line_id"] == "L3" and np.all(ref == 300)
    for stale in ({"end_ms": 450}, {"speaker_ids": [1, 2]}):
        ref, evidence = dubbing.voice_reference(wave, rate, row, {**labels, "L1": {**labels["L1"], **stale}}, opts)
        assert evidence["line_id"] == "L1" and set(ref) == {0, 200}
    long = np.arange(15000, dtype="<i2")
    assert len(dubbing.sentence_audio(long, rate, 0, 15000)) == 15000
    ref, _ = dubbing.voice_reference(long, rate, {**row, "end_ms": 15000}, {}, opts)
    assert len(ref) == 12000


def test_phrase_pitch_alignment_ignores_pause_duration():
    options = {**Settings.load().values["tts"]["intonation"], "smooth_ms": 30}
    a = np.r_[np.full(50, 120.0), np.zeros(20), np.full(50, 200.0)]
    b = np.r_[np.full(50, 120.0), np.zeros(200), np.full(50, 200.0)]
    target = np.r_[np.full(50, 160.0), np.zeros(60), np.full(50, 160.0)]
    first, _ = transfer_f0(a, target, options)
    second, _ = transfer_f0(b, target, options)
    np.testing.assert_allclose(first, second)
    assert first[20] < target[20] and first[-20] > target[-20]


@pytest.mark.parametrize(
    ("text", "reading"), [("切手を貼ります。", "きってをはります。"), ("氷が溶けます。", "こおりがとけます。")]
)
def test_japanese_tts_dictionary_readings(text, reading):
    assert dubbing.japanese_to_kana(text) == reading


def test_japanese_unknown_character_is_not_silently_deleted(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(dubbing, "_get_fugashi_tagger", lambda: lambda t: [SimpleNamespace(surface=t, feature=None)])
    assert dubbing.japanese_to_kana("龘") == "龘"


def test_dub_shortens_only_measured_overruns_and_preserves_subtitle(tmp_path, monkeypatch):
    from dataclasses import replace

    from cjvmt.config import parse_glossary

    settings = replace(Settings.load(), glossary=parse_glossary("東京 | 东京"))
    original = "如果明天还在下雨的话，东京的活动就可能延期。"
    compact = "如果明天下雨，东京活动可能延期。"
    rows = [
        dict(line_id=f"L{i}", start_ms=i * 1000, end_ms=(i + 1) * 1000, original="東京の予定", translation=original)
        for i in range(3)
    ]
    requests = []
    rate = settings.values["tts"]["sample_rate"]
    for i, seconds in enumerate((1, 3, 3)):
        path = tmp_path / f"{i}.wav"
        write_wav(path, np.ones(rate * seconds, dtype="<i2"), rate)
        requests.append({"output": str(path), "text": original})

    class Editor:
        def infer_batch(self, items):
            if items[0][0].task == "dub_compact":
                assert len(items) == 2
                return [{"text": compact}, {"text": "东京活动延期。"}]
            assert len(items) == 4
            assert all(req.task == "dub_entailment" for req, _ in items)
            a, b = [json.loads(req.user) for req, _ in items[:2]]
            assert a["第一段"] == b["第二段"] and a["第二段"] == b["第一段"]
            accept = {"text": '{"certain":true,"unsupported":[]}'}
            return [accept, accept, accept, {"text": '{"certain":true,"unsupported":["删掉了条件"]}'}]

    def render(_settings, _work, reqs, _log):
        assert len(reqs) == 1 and reqs[0]["text"] == compact
        write_wav(Path(reqs[0]["output"]), np.ones(2 * rate, dtype="<i2"), rate)

    monkeypatch.setattr(dubbing, "synthesize", render)
    dubbing.shorten_overruns(
        settings, Editor(), rows, requests, tmp_path, tmp_path, "ja2zh", "zh", None, lambda: None, lambda _: None
    )
    assert [r["translation"] for r in rows] == [original] * 3
    assert [r.get("dub_script", original) for r in rows] == [original, compact, original]
    assert Path(requests[1]["output"]).parent.name == "compact"


def test_voice_reads_only_matched_video_terms():
    from dataclasses import replace

    from cjvmt.config import parse_glossary

    s = replace(Settings.load(), glossary=parse_glossary("方言 | 方言 | ほうげん"))
    assert dubbing.spoken_draft("方言です。", "介绍方言", "ja", s).startswith("ほうげん")


@pytest.mark.parametrize(
    "reply",
    ["YES", "{}", "[]", '{"certain":false,"unsupported":[]}', '{"certain":true,"unsupported":["说话对象改变"]}'],
)
def test_uncertain_or_incomplete_voice_verdict_keeps_original(reply):
    assert not dubbing.entailment_confirmed(reply)


def test_entailment_accepts_single_fenced_object_only():
    reply = '```json\n{"certain":true,"unsupported":[]}\n```'
    assert dubbing.entailment_confirmed(reply)
    assert not dubbing.entailment_confirmed("YES! " + reply)
    assert not dubbing.entailment_confirmed(reply + "\n{}")
