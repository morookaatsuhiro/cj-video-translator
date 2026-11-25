from dataclasses import replace
from pathlib import Path
from queue import Queue
from time import monotonic

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cjvmt.asr import MockASR
from cjvmt.backends.mock import Mock
from cjvmt.config import Settings
from cjvmt.demo import create_app, run_data
from cjvmt.realtime import Ring, Sentence, SilenceDetector, work


def test_ring_and_forced_split():
    ring = Ring(6)
    ring.append(np.array([0, 1, 2, 3]))
    ring.frames.extend(((1, Path("old.jpg")), (6, Path("new.jpg"))))
    ring.append(np.array([4, 5, 6, 7]))
    assert ring.audio(2, 6).tolist() == [2, 3, 4, 5]
    assert ring.images(0, 8, 1) == (Path("new.jpg"),)
    config = Settings.load().values["realtime"]
    detector = SilenceDetector(1000, {**config, "max_utt_sec": 1}, 300)
    assert detector.feed(0, 500, 1) is None
    assert detector.feed(500, 1000, 1) == (0, 1000)
    assert detector.feed(1000, 1500, 1) is None
    assert detector.feed(1500, 1600, 0) is None
    assert detector.feed(1600, 2100, 0) == (1000, 1600)


def test_worker_order_backlog_and_compare_input(tmp_path):
    import copy

    settings = replace(Settings.load(), root=tmp_path)
    settings.values["realtime"]["queue_max"] = 1
    session = tmp_path / "session"
    session.mkdir()
    frame = session / "frame.jpg"
    frame.write_bytes(b"frame")
    queue = Queue()
    for index in range(1, 4):
        queue.put(
            Sentence(
                index,
                2000 + index * 1000,
                2700 + index * 1000,
                np.zeros(3200, dtype="<i2"),
                (frame,),
                2,
                monotonic() - 0.25,
                500,
            )
        )
    queue.put(None)
    events, rows = [], []
    backend = Mock()
    work(
        queue,
        tmp_path,
        settings,
        backend,
        MockASR(),
        "compare",
        "ja2zh",
        1000,
        monotonic() - 8,
        lambda event: events.append(copy.deepcopy(event)),
        rows,
    )
    assert [event["type"] for event in events] == ["original", "quick", "sentence"] * 3
    assert [row["skipped"] for row in rows] == [True, True, False]
    assert backend.calls[-1].source == backend.calls[-2].source == rows[-1]["text_neutral"]
    assert rows[-1]["changed"]
    assert rows[0]["stage_ms"]["queue_wait"] >= 250
    assert rows[0]["stage_ms"]["vad_wait"] == 500
    assert "quick_translation" in rows[0]["stage_ms"] and "multimodal_translation" not in rows[0]["stage_ms"]
    assert "multimodal_translation" in rows[-1]["stage_ms"] and "observation" not in rows[-1]["stage_ms"]


def test_server_configuration_and_media_allowlist(tmp_path):
    settings = replace(Settings.load(), root=tmp_path)
    with TestClient(create_app(settings, panel="live")) as client:
        response = client.get("/")
        assert response.status_code == 200 and 'id="job-form"' in response.text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/api/runs").json() == []
        assert client.get("/api/options").json()["qwenAsr"] is False
        assert client.get("/files/fake/manifest.json").status_code == 404
        assert client.post("/api/jobs", data={"direction": "invalid"}).status_code == 400
        form = {"direction": "zh2ja", "workflow": "offline", "mode": "auto", "qwen_recheck": "1"}
        assert client.post("/api/jobs", data=form).status_code == 503
        assert client.post("/api/jobs", data={**form, "workflow": "live"}).status_code == 400


@pytest.mark.parametrize("direction", ["zh2ja", "auto"])
def test_camera_stream_preserves_frame_timing_and_exports_replay(tmp_path, direction):
    from io import BytesIO
    from threading import Thread

    from PIL import Image

    from cjvmt.media import probe
    from cjvmt.realtime import run_session
    from cjvmt.realtime.webcam import WebcamInput
    from cjvmt.store import read_json, read_jsonl

    settings = replace(Settings.load(), root=tmp_path)
    camera = WebcamInput(settings)
    with pytest.raises(ValueError, match="PCM"):
        camera.push(b"invalid", None)
    with pytest.raises(RuntimeError, match="暂时"):
        camera.push(b"\0" * 1024, None)
    output = BytesIO()
    Image.new("RGB", (320, 180), (64, 100, 140)).save(output, format="JPEG")
    frame = output.getvalue()
    events = []
    run = tmp_path / "camera"
    errors = []

    def process():
        try:
            run_session(
                settings,
                direction,
                "perceive",
                "mock",
                events.append,
                lambda out, manifest: camera.prepare(
                    out, manifest, lambda message: events.append({"type": "progress", "message": message})
                ),
                {"input_name": "摄像头直播", "source_kind": "webcam"},
                run=run,
            )
        except Exception as error:
            errors.append(error)

    thread = Thread(target=process, daemon=True)
    thread.start()
    assert camera.ready.wait(3)
    camera.push(np.full(8192, 2000, dtype="<i2").tobytes(), frame)
    camera.push(np.full(2048, 2000, dtype="<i2").tobytes(), None)
    camera.push(np.zeros(10240, dtype="<i2").tobytes(), None)
    camera.stop()
    thread.join(10)
    assert not thread.is_alive() and not errors
    manifest = read_json(run / "manifest.json")
    assert manifest["media"]["duration_ms"] == 1280
    assert manifest["status"] == "complete"
    assert manifest["direction"] == ("ja2zh" if direction == "auto" else "zh2ja")
    assert probe(run / "media/source.mp4", settings)["has_audio"]
    rows = read_jsonl(run / "session/session.jsonl")
    assert len(rows) == 1 and not rows[0]["skipped"]
    assert rows[0]["media"]["frames"] == ["session/frames/00001.jpg"]
    assert rows[0]["stage_ms"]["vad_wait"] >= 500
    assert manifest["summary"]["stages"]["observation"]["n"] == 1
    assert "stage_observation,1," in (run / "session/latency_summary.csv").read_text()
    assert "startup_media,1," in (run / "session/latency_summary.csv").read_text()
    assert rows[0]["quick"] in (run / "session/subtitles_quick.srt").read_text()
    assert rows[0]["multimodal"] in (run / "session/subtitles.srt").read_text()
    assert run_data(run, manifest, ".")["download"]["quick"] == "./session/subtitles_quick.srt"
    auto_events = ["language"] if direction == "auto" else []
    progress = [event for event in events if event["type"] == "progress"]
    assert any(event["message"] == "正在保存录音并生成回放视频" for event in progress)
    assert any(event.get("stage") == "remaining" and event["total"] == 1 for event in progress)
    assert progress[-1]["message"] == "正在导出字幕"
    assert [event["type"] for event in events if event["type"] != "progress"] == [
        "status",
        "ready",
        *auto_events,
        "original",
        "quick",
        "sentence",
        "done",
    ]
