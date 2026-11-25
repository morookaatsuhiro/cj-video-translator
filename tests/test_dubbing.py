import json
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from cjvmt import dubbing
from cjvmt.clip import read_wav, write_wav
from cjvmt.config import Settings
from cjvmt.media import ffmpeg, media_command, probe
from cjvmt.prosody import transfer_f0
from cjvmt.store import write_json, write_jsonl


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
