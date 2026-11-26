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
        assert client.get("/api/storage").json()["runs_count"] == 0


def test_run_rename_delete_and_cleanup_api(tmp_path):
    from cjvmt.store import write_json, write_jsonl

    settings = replace(Settings.load(), root=tmp_path)
    results = settings.path("results")
    # 1. Valid completed run with redundant input + dub.wav
    good = results / "20261127-0100_job_aaaa1111"
    (good / "media").mkdir(parents=True)
    (good / "input").mkdir()
    (good / "input/raw.mp4").write_bytes(b"x" * 4096)
    (good / "media/source.mp4").write_bytes(b"v" * 1024)
    (good / "media/dub.mp4").write_bytes(b"d" * 1024)
    (good / "media/dub.wav").write_bytes(b"w" * 2048)
    (good / "media/silent.mp4").write_bytes(b"s" * 1024)
    write_jsonl(
        good / "session/session.jsonl",
        [{"line_id": "L001", "start_ms": 0, "end_ms": 1000, "text_asr": "hi", "quick": "你好", "multimodal": "你好"}],
    )
    write_json(
        good / "manifest.json",
        {
            "run_id": good.name,
            "started_at": "2026-11-27T01:00:00Z",
            "status": "complete",
            "kind": "realtime",
            "direction": "zh2ja",
            "input_name": "a5c4837b8b5f7f54d9053270b7e65bbe.mp4",
            "prompt_mode": "perceive",
            "conditions": ["final"],
            "line_count": 1,
            "model": {"backend": "mock", "model": "mock"},
        },
    )
    # 2. Failed run & protected WMT eval run
    failed = results / "20261127-0105_job_bbbb2222"
    write_json(failed / "manifest.json", {"run_id": failed.name, "status": "failed", "kind": "realtime"})
    wmt = results / "20261125-0247_wmt26-eval_bb2b92a1"
    write_json(wmt / "manifest.json", {"run_id": wmt.name, "status": "complete", "kind": "wmt_eval"})

    with TestClient(create_app(settings, panel="live")) as client:
        st = client.get("/api/storage").json()
        assert st["runs_count"] == 1 and st["reclaimable_runs"] == 1 and st["reclaimable_bytes"] >= 7168

        # Rename run
        renamed = client.patch(f"/api/runs/{good.name}", json={"title": "结课演示自录版"})
        assert renamed.status_code == 200
        assert renamed.json()["custom_title"] == "结课演示自录版"

        # Cleanup removes failed run & slims redundant files from good run, leaving WMT untouched
        cleaned = client.post("/api/runs/cleanup").json()
        assert failed.name in cleaned["removed_runs"]
        assert cleaned["slimmed_runs"] == 1
        assert not failed.exists()
        assert (
            not (good / "input").exists()
            and not (good / "media/dub.wav").exists()
            and not (good / "media/silent.mp4").exists()
        )
        assert (good / "media/source.mp4").is_file() and (good / "media/dub.mp4").is_file()
        assert wmt.exists()

        # Protected WMT run cannot be deleted
        assert client.delete(f"/api/runs/{wmt.name}").status_code == 400

        # Delete good run
        deleted = client.delete(f"/api/runs/{good.name}")
        assert deleted.status_code == 200 and not good.exists()
        assert client.get("/api/runs").json() == []


def test_fast_file_run_skips_playback_sleep_and_marks_dub_ready(tmp_path):
    from cjvmt.media import ffmpeg
    from cjvmt.realtime import fast_file_run
    from cjvmt.store import read_json, read_jsonl

    settings = replace(Settings.load(), root=tmp_path)
    video = tmp_path / "clip.mp4"
    ffmpeg(
        settings,
        ["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3", str(video)],
    )
    events = []
    out = fast_file_run(
        settings,
        video,
        "ja2zh",
        "perceive",
        "mock",
        events.append,
        for_dub=True,
    )
    manifest = read_json(out / "manifest.json")
    assert manifest["status"] == "complete"
    assert manifest["segmented_for_dub"] is True
    rows = read_jsonl(out / "session/session.jsonl")
    assert len(rows) >= 1 and rows[0]["multimodal"] and rows[0]["observation"] is not None
    assert any(event["type"] == "done" for event in events)
    subtitles_only = fast_file_run(settings, video, "ja2zh", "perceive", "mock", lambda _: None, for_dub=False)
    assert (out / "session/subtitles.srt").read_bytes() == (subtitles_only / "session/subtitles.srt").read_bytes()

    def hashes(run):
        return [row["prompt_sha256"] for row in read_jsonl(run / "predictions.jsonl")]

    assert hashes(out) == hashes(subtitles_only)


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


def test_uploaded_glossary_stays_with_its_video(tmp_path):
    from time import sleep

    from cjvmt.media import ffmpeg
    from cjvmt.store import read_json

    settings = replace(Settings.load(), root=tmp_path)
    video = tmp_path / "source.mp4"
    ffmpeg(settings, ["-f", "lavfi", "-i", "testsrc2=s=128x96:r=8:d=1", "-f", "lavfi", "-i", "sine=d=1", str(video)])
    fields = {"workflow": "fast", "direction": "zh2ja", "mode": "auto"}
    with TestClient(create_app(settings, panel="live")) as client:
        bad = client.post("/api/jobs", data={**fields, "glossary": "wrong format"})
        assert bad.status_code == 400
        for raw, expected in [("唐河县 | 唐河県", ["唐河県"]), ("", [])]:
            response = client.post(
                "/api/jobs",
                data={**fields, "glossary": raw},
                files={"video": ("source.mp4", video.read_bytes(), "video/mp4")},
            )
            assert response.status_code == 200, response.text
            job = response.json()["job"]
            for _ in range(150):
                if client.get(f"/api/jobs/{job}").json()["finished"]:
                    break
                sleep(0.05)
            manifest = read_json(settings.path("results") / job / "manifest.json")
            assert manifest["status"] == "complete" and manifest["prompt_mode"] == "perceive"
            assert [t["target"] for t in manifest["glossary"]] == expected
            assert not manifest.get("asr_hotwords")
