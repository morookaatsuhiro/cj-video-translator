from dataclasses import replace
from pathlib import Path
from time import monotonic, sleep

import numpy as np
from fastapi.testclient import TestClient

from cjvmt import speakers
from cjvmt.clip import write_wav
from cjvmt.config import Settings
from cjvmt.demo import create_app
from cjvmt.speaker_worker import FPS, RATE, match_face, measurements, voice_windows
from cjvmt.store import write_json


def test_pitch_and_short_tail():
    assert measurements(np.zeros(40)) == {"voiced_ms": 0}
    rising = measurements(np.linspace(120, 180, 80))
    falling = measurements(np.linspace(180, 120, 80))
    assert rising["pitch_trend"] == "rising" and falling["pitch_trend"] == "falling"
    assert rising["pitch_change_st"] == -falling["pitch_change_st"]
    assert voice_windows(0, 2500) == [(0, 2500)]
    assert voice_windows(0, 3200) == [(0, 2200), (2200, 3200)]


def test_lip_motion_allows_unknown_when_static_or_ambiguous():
    amplitude = np.array([0.03, 0.15, 0.08, 0.23, 0.04, 0.29] * 6)
    times = np.round(np.arange(1, len(amplitude) + 1) * 1000 / FPS).astype(int)
    audio = np.zeros((len(times) + 3) * RATE // FPS)
    half = RATE // FPS // 2
    left, right = [], []
    for moment, value in zip(times, amplitude, strict=True):
        center = round(moment * RATE / 1000)
        audio[center - half : center + half] = value
        left.append((int(moment), value, (0.1, 0.2, 0.3, 0.6)))
        right.append((int(moment), 0.06, (0.7, 0.2, 0.9, 0.6)))
    assert match_face(audio, [left, right], 0, 4000)["side"] == "left"
    assert match_face(audio, [right], 0, 4000) is None
    assert match_face(audio, [left, left], 0, 4000) is None


def test_style_and_comparisons_use_each_speakers_own_baseline():
    durations = [2400, 2400, 1200, 1800]
    pitch = [120, 130, 155, 230]
    ids = ["L001", "L002", "L003", "L004"]
    lines = {
        key: {"speaker_ids": [1 if index < 3 else 2], "speech_ms": lengths, "pitch": {"median_f0_hz": hz}}
        for index, (key, lengths, hz) in enumerate(zip(ids, durations, pitch, strict=True))
    }
    source = [
        {"line_id": key, "original": "明日はどこに行きますか？", "observation": {"tone": "亲切" if index else "不确定"}}
        for index, key in enumerate(ids)
    ]
    result = speakers.annotate({"lines": lines}, source, "ja2zh")
    assert lines["L003"]["pace"] == "faster" and lines["L003"]["pitch"]["relative_to_speaker_st"] > 2
    assert "pace" not in lines["L004"] and "typical_f0_hz" not in result["speakers"][1]
    assert lines["L001"]["wording"] == ["日语敬体", "问句表达"] and "tone" not in lines["L001"]


def test_model_tone_only_needs_missing_single_speaker_lines(tmp_path):
    write_wav(tmp_path / "media/audio.wav", np.zeros(RATE, dtype="<i2"), RATE)
    source = {"speaker_ids": [1], "speech_ms": 700, "start_ms": 0, "end_ms": 850}
    result = {
        "lines": {
            "L001": source.copy(),
            "L002": {**source, "tone": {"tone": "平静"}},
            "L003": {**source, "speaker_ids": [1, 2]},
        }
    }

    class Fake:
        settings = Settings.load()

        def infer(self, request, item_id):
            assert isinstance(request.audio, Path) and request.audio.is_file() and item_id.endswith("L001:speaker-tone")
            return {"text": "4. 语气：亲切；强度：中。"}

    speakers.add_model_tone(tmp_path, "zh2ja", result, Fake())
    assert result["lines"]["L001"]["tone"] == {"tone": "亲切", "strength": "中"}
    assert "tone" not in result["lines"]["L003"]
    assert result["model_tone"] == {"status": "complete", "labeled": 2, "eligible": 2}


def test_speaker_result_is_available_after_processing_and_reused(tmp_path, monkeypatch):
    settings = replace(Settings.load(), root=tmp_path)
    run = settings.path("results") / "sample"
    media = run / "media"
    media.mkdir(parents=True)
    (media / "source.mp4").touch()
    (media / "audio.wav").touch()
    manifest = {
        "status": "complete",
        "kind": "offline",
        "direction": "ja2zh",
        "model": {"backend": "local_omni"},
        "line_count": 1,
    }
    write_json(run / "manifest.json", manifest)
    result = {"status": "complete", "speakers": [], "lines": {}}
    calls = []

    def analyze(*args):
        calls.append(args)
        return result

    monkeypatch.setattr(speakers, "available", lambda _settings: True)
    monkeypatch.setattr(speakers, "analyze", analyze)
    with TestClient(create_app(settings, panel="live", backend_name="local_omni")) as client:
        url = "/api/runs/sample/speakers"
        assert client.get(url).json()["status"] == "idle"
        assert client.post(url).json()["status"] == "running"
        deadline = monotonic() + 3
        while client.get(url).json()["status"] == "running" and monotonic() < deadline:
            sleep(0.02)
        assert client.get("/files/sample/speakers.json").json() == result
        assert client.post(url).json() == result and len(calls) == 1
