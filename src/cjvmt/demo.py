import csv
import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Lock, Thread

from cjvmt import speakers
from cjvmt.config import Settings, file_hash, parse_glossary
from cjvmt.dubbing import can_keep_resident, eligible, enabled, generate_dub
from cjvmt.media import browser_replay
from cjvmt.pairs import REVIEW_CHOICES, REVIEW_PENDING
from cjvmt.pipeline import run_pipeline, subtitle_name
from cjvmt.store import new_run, read_json, read_jsonl, write_json, write_text
from cjvmt.translate import FOUR, Translator

WEB = Path(__file__).with_name("web")
CLASSROOM_DOCS = {
    "reports/final_report.md": "课程报告",
    "reports/eval_wmt25.md": "WMT25 日→中评测",
    "reports/eval_wmt26.md": "WMT26 中→日评测记录",
    "docs/demo_script.md": "演示脚本",
    "docs/demo_media.md": "素材署名",
}
CLASSROOM_ASSETS = {
    "figures/system_overview.png": "系统架构图",
    "figures/realtime_latency.png": "延迟实测图",
    "demo_playback.mp4": "演示录屏",
}
COMMONS = {
    "japanese_mcdc_later.mp4": (
        "社区会议片段 · 日语",
        "ja2zh",
        "TCappelletto (WMF) · CC BY-SA 4.0",
        "MCDC_Onboarding_%22Ask_me_Anything%22_session_-_Japanese_language.webm",
    ),
    "japanese_mcdc.mp4": (
        "社区会议开场片段 · 日语",
        "ja2zh",
        "TCappelletto (WMF) · CC BY-SA 4.0",
        "MCDC_Onboarding_%22Ask_me_Anything%22_session_-_Japanese_language.webm",
    ),
    "chinese_wikitongues.mp4": (
        "Wikitongues 片段 · 中文",
        "zh2ja",
        "Wikitongues 与 Ying Li · CC BY-SA 4.0",
        "WIKITONGUES-_Ying_speaking_Henan_Chinese.webm",
    ),
    "japanese_spoken_wikinews.mp4": (
        "维基新闻片段 · 日语合成旁白",
        "ja2zh",
        "Akaniji · CC BY 4.0",
        "Spoken_Wikinews_-_Japanese_001_170103_Hakone_Ekiden.webm",
    ),
}


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def is_protected_run(run: Path, manifest: dict | None = None) -> bool:
    kind = (manifest or {}).get("kind") or ""
    return kind.startswith("wmt") or "_wmt25-" in run.name or "_wmt26-" in run.name


def _redundant_paths(run: Path, manifest: dict | None = None) -> list[Path]:
    paths: list[Path] = []
    if (run / "media/source.mp4").is_file() and (run / "input").is_dir():
        paths.append(run / "input")
    if (run / "media/dub.mp4").is_file() and (run / "media/dub.wav").is_file():
        paths.append(run / "media/dub.wav")
    for name in ("media/dub.webm", "media/source.webm"):
        if (run / name).is_file():
            paths.append(run / name)
    if (manifest or {}).get("kind") == "realtime" and (manifest or {}).get("status") == "complete":
        if (run / "media/silent.mp4").is_file():
            paths.append(run / "media/silent.mp4")
    paths.extend(run.glob("*.capture"))
    return paths


def slim_run(run: Path, manifest: dict | None = None) -> int:
    """Remove redundant intermediate files while keeping all playable/analyzable outputs intact."""
    if is_protected_run(run, manifest):
        return 0
    freed = 0
    for target in _redundant_paths(run, manifest):
        if target.is_dir():
            freed += dir_size_bytes(target)
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            try:
                freed += target.stat().st_size
                target.unlink(missing_ok=True)
            except OSError:
                continue
    return freed


def _is_reclaimable_run(run: Path, manifest: dict | None) -> bool:
    if is_protected_run(run, manifest):
        return False
    if manifest is None:
        return True
    if manifest.get("status") != "complete":
        return True
    return (
        manifest.get("kind") in {"offline", "realtime"}
        and manifest.get("line_count", 0) == 0
        and not (run / "media/dub.mp4").is_file()
    )


def storage_summary(folder: Path, active_ids: set[str] | None = None) -> dict:
    busy = active_ids or set()
    runs_count = 0
    total_bytes = 0
    reclaimable_runs = 0
    reclaimable_bytes = 0
    paths = [folder] if (folder / "manifest.json").is_file() else (folder.iterdir() if folder.is_dir() else [])
    for path in paths:
        if not path.is_dir():
            continue
        manifest_file = path / "manifest.json"
        manifest = None
        if manifest_file.is_file():
            try:
                manifest = read_json(manifest_file)
            except (OSError, ValueError):
                manifest = None
        if is_protected_run(path, manifest):
            continue
        size = dir_size_bytes(path)
        total_bytes += size
        if path.name in busy:
            runs_count += 1
            continue
        if _is_reclaimable_run(path, manifest):
            reclaimable_runs += 1
            reclaimable_bytes += size
        elif manifest and manifest.get("kind") in {"offline", "realtime"}:
            runs_count += 1
            for extra in _redundant_paths(path, manifest):
                reclaimable_bytes += dir_size_bytes(extra) if extra.is_dir() else extra.stat().st_size
    return {
        "runs_count": runs_count,
        "total_bytes": total_bytes,
        "reclaimable_runs": reclaimable_runs,
        "reclaimable_bytes": reclaimable_bytes,
    }


def speaker_data(run: Path) -> dict | None:
    for name in (speakers.STATUS, speakers.RESULT):
        path = run / name
        if path.is_file():
            return read_json(path)
    return None


def run_list(folder: Path, *, include_dubbable: bool = False) -> list[tuple[Path, dict]]:
    paths = [folder] if (folder / "manifest.json").is_file() else folder.glob("*")
    found = []
    for path in paths:
        if not (path / "manifest.json").is_file():
            continue
        manifest = read_json(path / "manifest.json")
        if (
            manifest.get("status") == "complete"
            and manifest.get("kind") in {"offline", "realtime"}
            and (manifest.get("line_count", 0) > 0 or (include_dubbable and eligible(path, manifest)))
        ):
            found.append((path, manifest))
    return sorted(found, key=lambda pair: (pair[1]["model"]["backend"] != "mock", pair[1]["started_at"]), reverse=True)


def run_data(run: Path, manifest: dict, base: str) -> dict:
    realtime = manifest["kind"] == "realtime"
    rows = run / ("session/session.jsonl" if realtime else "lines.jsonl")
    credits = COMMONS.get(manifest["input_name"])
    pair = manifest.get("pair")
    if pair:
        pair = {**pair, "review": REVIEW_PENDING, "reviews": {}}
        table = run.parent / pair["batch"] / "pairs.csv"
        if table.is_file():
            with table.open(encoding="utf-8-sig", newline="") as stream:
                review = next((row for row in csv.DictReader(stream) if row.get("run_id") == run.name), {})
            general = (review.get("review") or "").strip()
            if general in REVIEW_CHOICES:
                pair["review"] = general
            for condition in manifest["conditions"]:
                key = f"review_{condition}"
                if key in review:
                    verdict = (review[key] or "").strip()
                    pair["reviews"][condition] = verdict if verdict in REVIEW_CHOICES else REVIEW_PENDING
    dub = dub_data(run, base) if base.startswith("/files/") else None
    if realtime:
        download = {"final": f"{base}/session/subtitles.srt"}
        if (run / "session/subtitles_quick.srt").is_file():
            download["quick"] = f"{base}/session/subtitles_quick.srt"
    else:
        download = {condition: f"{base}/{subtitle_name(condition)}" for condition in manifest["conditions"]}
    return {
        "id": run.name,
        "kind": manifest["kind"],
        "direction": manifest["direction"],
        "name": credits[0] if credits else manifest["input_name"],
        "custom_title": manifest.get("custom_title"),
        "started_at": manifest.get("started_at"),
        "size_bytes": dir_size_bytes(run),
        "deletable": base.startswith("/files/") and not is_protected_run(run, manifest),
        "attribution": {"text": credits[2], "url": "https://commons.wikimedia.org/wiki/File:" + credits[3]}
        if credits
        else None,
        "mode": manifest["prompt_mode"],
        "model": manifest["model"],
        "mock": manifest["model"]["backend"] == "mock",
        "conditions": manifest["conditions"],
        "source_kind": manifest.get("source_kind"),
        "replay_quality": (manifest.get("media") or {}).get("replay"),
        "source_license": manifest.get("source_license"),
        "transcript_kind": manifest.get("transcript_kind"),
        "glossary": manifest.get("glossary", []),
        "asr_qwen": (manifest.get("asr") or {}).get("qwen_recheck"),
        "pair": pair,
        "summary": manifest.get("summary"),
        "latency_csv": f"{base}/session/latency_summary.csv" if realtime else None,
        "lines": read_jsonl(rows),
        "video": f"{base}/media/source.mp4" if (run / "media/source.mp4").is_file() else None,
        "dub": dub,
        "can_dub": base.startswith("/files/") and eligible(run, manifest),
        "can_analyze_speakers": base.startswith("/files/") and speakers.eligible(run, manifest),
        "speaker_analysis": speaker_data(run) if base.startswith("/files/") else None,
        "base": base,
        "download": download,
    }


def dub_data(run: Path, base: str) -> dict | None:
    path = run / "dubbing.json"
    if not path.is_file():
        return None
    state = read_json(path)
    if state["status"] == "complete":
        state.update(video=f"{base}/media/dub.mp4", subtitles=f"{base}/media/dub.srt")
    return state


EXPERIMENT_FIGURES = {"recall": "问句判定召回率", "style": "文体一致率"}


def experiment_data(settings: Settings, figure_prefix: str = "figures") -> dict | None:
    candidates = []
    for run in settings.path("results").glob("*_wmt26-eval_*"):
        if not (run / "summary.csv").is_file() or not (run / "manifest.json").is_file():
            continue
        manifest = read_json(run / "manifest.json")
        if (
            manifest.get("kind") == "wmt_eval"
            and manifest.get("dataset") == "wmt26"
            and manifest.get("status") == "complete"
            and manifest.get("model", {}).get("backend") != "mock"
        ):
            candidates.append((manifest["document_count"], manifest["started_at"], run))
    if not candidates:
        return None
    count, _, run = max(candidates)
    with (run / "summary.csv").open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["value"]]
    figures = []
    for metric, label in EXPERIMENT_FIGURES.items():
        name = f"{run.name}-{metric}.png"
        if (settings.path("reports") / "figures" / name).is_file():
            figures.append({"path": f"{figure_prefix}/{name}", "label": label})
    return {"run": run.name, "count": count, "rows": rows, "figures": figures}


def render_page(
    *,
    interactive: bool,
    runs: list[dict] | None = None,
    default_panel: str = "replay",
    webcam: bool = False,
    materials: list[dict] | None = None,
    experiment: dict | None = None,
) -> str:
    payload = json.dumps(
        {
            "live": interactive,
            "runs": runs,
            "defaultPanel": default_panel,
            "webcam": webcam,
            "materials": materials,
            "experiment": experiment,
        },
        ensure_ascii=False,
    )
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = (WEB / "index.html").read_text().replace("__CJVMT_DATA__", payload)
    return (
        page.replace("./app.", "/assets/app.").replace("./webcam.js", "/assets/webcam.js").replace("./index.html", "/")
        if runs is None
        else re.sub(r'^  <script defer src="\./webcam\.js[^"]*"></script>\n', "", page, flags=re.MULTILINE)
    )


def export_site(settings: Settings, selected: Path | None = None) -> Path:
    folder = selected or settings.path("results")
    output = settings.path("reports") / "html"
    output.mkdir(parents=True, exist_ok=True)
    for asset in ("app.js", "app.css"):
        shutil.copyfile(WEB / asset, output / asset)
    materials = []
    for doc, label in CLASSROOM_DOCS.items():
        if (path := settings.root / doc).is_file():
            shutil.copyfile(path, output / path.name)
            materials.append({"path": path.name, "label": label})
    figures = settings.path("reports") / "figures"
    if figures.is_dir():
        shutil.copytree(figures, output / "figures", dirs_exist_ok=True)
    materials.extend(
        {"path": name, "label": label} for name, label in CLASSROOM_ASSETS.items() if (output / name).is_file()
    )
    public_hashes = {
        file_hash(path) for name in COMMONS if (path := settings.path("external") / "commons" / name).is_file()
    }
    runs = []
    for run, manifest in run_list(folder):
        kind = manifest.get("source_kind") or ""
        if (
            kind.startswith("wmt")
            or kind == "webcam"
            or manifest.get("asr_hotwords")
            or manifest.get("glossary")
            or manifest.get("pair")
            or not (run / "media/source.mp4").is_file()
        ):
            continue
        if (
            selected is None
            and manifest["model"]["backend"] != "mock"
            and manifest.get("input_sha256") not in public_hashes
        ):
            continue
        relative = f"runs/{run.name}"
        data = run_data(run, manifest, relative)
        assets = {"media/source.mp4", *(url.removeprefix(relative + "/") for url in data["download"].values())}
        assets.update(row["clip"]["path_preview"] for row in data["lines"] if "clip" in row)
        if data["summary"]:
            assets.add("session/latency_summary.csv")
        for name in assets:
            path = output / relative / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run / name, path)
        runs.append(data)
    write_text(
        output / "index.html",
        render_page(interactive=False, runs=runs, materials=materials, experiment=experiment_data(settings)),
    )
    return output / "index.html"


def export_comparison(run: Path) -> Path:
    data = run_data(run, read_json(run / "manifest.json"), ".")
    for asset in ("app.js", "app.css"):
        shutil.copyfile(WEB / asset, run / asset)
    write_text(run / "compare.html", render_page(interactive=False, runs=[data]))
    return run / "compare.html"


def create_app(settings: Settings, *, results: Path | None = None, panel: str = "replay", backend_name: str = "mock"):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, HTMLResponse
    from fastapi.staticfiles import StaticFiles

    from cjvmt.asr import make_asr, parse_hotwords
    from cjvmt.backends import make_backend
    from cjvmt.qwen_asr import available as qwen_asr_available
    from cjvmt.realtime import fast_file_run, file_sim, run_session
    from cjvmt.realtime.webcam import WebcamInput

    app = FastAPI(title="CJ-VMT", docs_url=None, redoc_url=None, openapi_url=None)
    folder = results or settings.path("results")
    jobs: dict[str, dict] = {}
    guard = Lock()
    inference = Lock()
    replay_encoding = Lock()
    dubbing_runs: set[str] = set()
    speaker_runs: set[str] = set()
    engine = recognizer = None

    def find_run(run_id: str) -> Path:
        base = (
            folder
            if (folder / "manifest.json").is_file() and folder.name == run_id
            else settings.path("results") / run_id
        )
        if not re.fullmatch(r"[\w-]+", run_id) or not (base / "manifest.json").is_file():
            raise HTTPException(404)
        return base

    def make_dub(run: Path, manifest: dict) -> None:
        nonlocal engine, recognizer
        path = run / "dubbing.json"

        def update(message: str, completed: int | None = None, total: int | None = None) -> None:
            state = {"status": "running", "message": message}
            if total is not None:
                state.update(completed=completed, total=total)
            write_json(path, state)

        def release_translation() -> None:
            # Keep Qwen3-Omni warm in VRAM on >= 36 GB GPUs (e.g., RTX 3090 48 GB) so subsequent runs skip 100s reload.
            if not can_keep_resident(settings) and hasattr(engine, "close"):
                engine.close()

        dubbing_runs.add(run.name)
        try:
            update("正在准备配音")
            if not enabled(settings) or not eligible(run, manifest):
                raise ValueError("此会话没有可配音的自录或授权素材。")
            engine = engine or make_backend(settings, backend_name)
            recognizer = recognizer or make_asr(settings, backend_name)
            dub_settings = replace(settings, glossary=tuple(manifest.get("glossary", ())))
            result = generate_dub(
                run,
                manifest,
                dub_settings,
                Translator(dub_settings, engine),
                release_translation,
                update,
                asr=recognizer,
                on_synthesis_progress=lambda completed, total: update("正在合成配音", completed, total),
            )
            write_json(path, result)
        except Exception as error:
            write_json(path, {"status": "failed", "message": str(error)})
        finally:
            dubbing_runs.discard(run.name)

    @app.get("/api/runs/{run_id}/dub")
    def dub_status(run_id: str):
        state = dub_data(find_run(run_id), f"/files/{run_id}") or {"status": "idle"}
        if state["status"] == "running" and run_id not in dubbing_runs:
            return {"status": "failed", "message": "配音任务已中断，可以重新生成。"}
        return state

    @app.post("/api/runs/{run_id}/dub")
    async def start_dub(run_id: str, request: Request):
        run = find_run(run_id)
        manifest = read_json(run / "manifest.json")
        form = await request.form(max_files=0, max_fields=2)
        if form.get("consent") != "1" or not eligible(run, manifest):
            raise HTTPException(400, "请选择自录或已获配音许可的视频，并确认说话人同意。")
        if not enabled(settings) or backend_name == "mock":
            raise HTTPException(503, "本地配音模型尚未就绪。")
        state = dub_data(run, f"/files/{run_id}")
        if state and state["status"] == "complete" and form.get("force") != "1":
            return state
        if not inference.acquire(blocking=False):
            raise HTTPException(409, "正在处理另一项任务，请等当前任务结束。")

        def work() -> None:
            try:
                make_dub(run, manifest)
            finally:
                inference.release()

        Thread(target=work, daemon=True, name=f"dub-{run.name}").start()
        return {"status": "running"}

    @app.get("/api/runs/{run_id}/speakers")
    def speaker_status(run_id: str):
        result = speaker_data(find_run(run_id)) or {"status": "idle"}
        if result["status"] == "running" and run_id not in speaker_runs:
            return {"status": "failed", "message": "人物分析已中断，可以重试。"}
        return result

    @app.post("/api/runs/{run_id}/speakers")
    def start_speaker_analysis(run_id: str, model_tone: bool = False):
        run = find_run(run_id)
        manifest = read_json(run / "manifest.json")
        if not speakers.eligible(run, manifest):
            raise HTTPException(400, "请选择包含原声音轨和字幕的已完成会话。")
        if not speakers.available(settings):
            raise HTTPException(503, "本地说话人模型尚未就绪。")
        state = speaker_status(run_id)
        if state["status"] == "running" or (
            state["status"] == "complete"
            and (not model_tone or state.get("model_tone", {}).get("status") == "complete")
        ):
            return state
        if not inference.acquire(blocking=False):
            raise HTTPException(409, "正在处理另一项任务，请等当前任务结束。")
        state = {"status": "running", "message": "正在读取字幕时间和原声音轨"}
        path = run / speakers.STATUS

        def work() -> None:
            nonlocal engine
            try:
                result_path = run / speakers.RESULT
                result = read_json(result_path) if result_path.is_file() else speakers.analyze(run, manifest, settings)
                if model_tone:
                    engine = engine or make_backend(settings, backend_name)
                    speakers.add_model_tone(run, manifest["direction"], result, Translator(settings, engine))
                write_json(result_path, result)
                path.unlink(missing_ok=True)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                write_json(
                    path, {"status": "failed", "message": "人物分析未完成，可重试。", "error": type(error).__name__}
                )
            finally:
                speaker_runs.discard(run_id)
                inference.release()

        try:
            speaker_runs.add(run_id)
            write_json(path, state)
            Thread(target=work, daemon=True, name=f"speakers-{run_id}").start()
        except (OSError, RuntimeError):
            speaker_runs.discard(run_id)
            inference.release()
            raise
        return state

    @app.get("/", response_class=HTMLResponse)
    def homepage():
        return render_page(
            interactive=panel != "replay",
            default_panel="live" if panel != "replay" else "replay",
            webcam=panel == "webcam",
            experiment=experiment_data(settings, "/experiment-figures"),
        )

    @app.get("/experiment-figures/{name}")
    def experiment_figure(name: str):
        if not re.fullmatch(r"\d{8}-\d{4}_wmt26-eval_[0-9a-f]+-(?:recall|style)\.png", name):
            raise HTTPException(404)
        path = settings.path("reports") / "figures" / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    def active_run_ids() -> set[str]:
        with guard:
            busy = {job_id for job_id, state in jobs.items() if not state["finished"]}
        return busy | dubbing_runs | speaker_runs

    @app.get("/api/runs")
    def runs():
        return [
            run_data(run, manifest, f"/files/{run.name}") for run, manifest in run_list(folder, include_dubbable=True)
        ]

    @app.get("/api/storage")
    def storage():
        return storage_summary(folder, active_run_ids())

    @app.patch("/api/runs/{run_id}")
    async def rename_run(run_id: str, request: Request):
        run = find_run(run_id)
        manifest_path = run / "manifest.json"
        manifest = read_json(manifest_path)
        if is_protected_run(run, manifest):
            raise HTTPException(400, "对照实验评测记录不允许修改。")
        try:
            body = await request.json()
        except ValueError as error:
            raise HTTPException(400, "请求格式无效。") from error
        raw_title = body.get("title") if isinstance(body, dict) else None
        if not isinstance(raw_title, str):
            raise HTTPException(400, "请提供字符串类型的标题。")
        title = " ".join(raw_title.split())[:60]
        if title:
            manifest["custom_title"] = title
        else:
            manifest.pop("custom_title", None)
        write_json(manifest_path, manifest)
        return run_data(run, manifest, f"/files/{run.name}")

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str):
        run = find_run(run_id)
        if run_id in active_run_ids():
            raise HTTPException(409, "该会话正在处理中，暂无法删除。")
        manifest = read_json(run / "manifest.json")
        if is_protected_run(run, manifest):
            raise HTTPException(400, "对照实验评测记录受保护，不能删除。")
        freed = dir_size_bytes(run)
        shutil.rmtree(run)
        with guard:
            jobs.pop(run_id, None)
        return {"deleted": run_id, "freed_bytes": freed, "storage": storage_summary(folder, active_run_ids())}

    @app.post("/api/runs/cleanup")
    def cleanup_runs():
        busy = active_run_ids()
        removed: list[str] = []
        slimmed = 0
        freed = 0
        target_root = folder if not (folder / "manifest.json").is_file() else folder.parent
        if target_root.is_dir():
            for path in list(target_root.iterdir()):
                if not path.is_dir() or path.name in busy:
                    continue
                manifest_file = path / "manifest.json"
                manifest = None
                if manifest_file.is_file():
                    try:
                        manifest = read_json(manifest_file)
                    except (OSError, ValueError):
                        manifest = None
                if is_protected_run(path, manifest):
                    continue
                if _is_reclaimable_run(path, manifest):
                    freed += dir_size_bytes(path)
                    shutil.rmtree(path, ignore_errors=True)
                    removed.append(path.name)
                elif manifest and manifest.get("kind") in {"offline", "realtime"}:
                    saved = slim_run(path, manifest)
                    if saved > 0:
                        freed += saved
                        slimmed += 1
        return {
            "removed_runs": removed,
            "slimmed_runs": slimmed,
            "freed_bytes": freed,
            "storage": storage_summary(folder, active_run_ids()),
        }

    @app.get("/api/options")
    def options():
        samples = [
            {"id": name, "title": title, "direction": direction} for name, title, direction, _ in available_samples()
        ]
        realtime = settings.values["realtime"]
        return {
            "backend": backend_name,
            "dubbing": enabled(settings) and backend_name != "mock",
            "videoGlossary": True,
            "qwenAsr": backend_name != "mock" and qwen_asr_available(),
            "speakerAnalysis": backend_name != "mock" and speakers.available(settings),
            "samples": samples,
            "maxSeconds": settings.values["demo"]["live_max_sec"],
            "capture": {
                "rate": realtime["sr"],
                "framesPerSecond": realtime["frame_fps"],
                "side": realtime["frame_max_side_px"],
                "chunk": realtime["vad"]["chunk_samples"],
            },
        }

    def available_samples():
        candidates = [("synthetic", "Mock 合成示例", "ja2zh", settings.root / "tests/fixtures/synthetic_dialogue.mp4")]
        commons = settings.path("external") / "commons"
        if backend_name != "mock":
            candidates = [
                (Path(name).stem, details[0], details[1], commons / name) for name, details in COMMONS.items()
            ]
        return [(key, title, direction, path) for key, title, direction, path in candidates if path.is_file()]

    @app.post("/api/jobs")
    async def start(request: Request):
        if panel == "replay":
            raise HTTPException(404)
        schema = await request.form(max_files=1, max_fields=10)
        direction, mode, workflow = (schema.get(key) for key in ("direction", "mode", "workflow"))
        if mode == "auto":
            mode = {"offline": "compare", "fast": "perceive"}.get(workflow, "guided")
        with_dub = schema.get("dub") == "1"
        qwen_recheck = schema.get("qwen_recheck") == "1"
        try:
            glossary = parse_glossary(schema.get("glossary", ""))
            job_settings = replace(settings, glossary=glossary)
            hotwords = parse_hotwords(schema.get("hotwords", ""), settings)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if (
            (direction != "auto" and direction not in settings.values["asr"]["lang"])
            or mode not in settings.values["prompt_modes"]["available"]
            or workflow not in {"fast", "live", "offline", "webcam"}
        ):
            raise HTTPException(400, "翻译方向、模式或处理方式无效。")
        if qwen_recheck and workflow != "offline":
            raise HTTPException(400, "Qwen3-ASR 复核用于离线处理。")
        if qwen_recheck and (backend_name == "mock" or not qwen_asr_available()):
            raise HTTPException(503, "本地 Qwen3-ASR 模型尚未就绪。")
        if with_dub:
            if not enabled(settings) or backend_name == "mock":
                raise HTTPException(503, "本地配音模型尚未就绪。")
            if workflow != "webcam" and not getattr(schema.get("video"), "filename", None):
                raise HTTPException(400, "配音仅支持自录或已获许可的视频。")
        if (
            workflow == "webcam"
            and request.url.scheme != "https"
            and request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise HTTPException(400, "请通过 HTTPS 或 localhost 使用摄像头。")
        if not inference.acquire(blocking=False):
            raise HTTPException(409, "正在处理另一个视频，请等待当前会话结束。")
        run = None
        try:
            run = new_run(settings, "job")
            upload = schema.get("video")
            camera = (
                WebcamInput(settings, record_replay=schema.get("record_replay") == "1")
                if workflow == "webcam"
                else None
            )
            if camera:
                source = None
            elif upload and hasattr(upload, "filename"):
                basename = Path(upload.filename or "upload.mp4").name
                source = run / "input" / basename
                source.parent.mkdir()
                size = 0
                with source.open("wb") as output:
                    while block := await upload.read(1024 * 1024):
                        size += len(block)
                        if size > 100 * 1024 * 1024:
                            raise HTTPException(413, "文件超过 100 MB。")
                        output.write(block)
                await upload.close()
            else:
                sample = next((sample for sample in available_samples() if sample[0] == schema.get("sample")), None)
                if sample is None:
                    raise HTTPException(400, "请选择视频或本地示例。")
                _, _, sample_direction, source = sample
                if backend_name == "mock" and direction == "auto":
                    direction = sample_direction
            player = Event()
            with guard:
                jobs[run.name] = {"events": [], "player": player, "finished": False, "camera": camera}
        except BaseException:
            if run is not None:
                shutil.rmtree(run)
            inference.release()
            raise

        def emit(event):
            with guard:
                jobs[run.name]["events"].append(event)

        def progress(message, completed=None, total=None):
            emit({"type": "progress", "message": message, "completed": completed, "total": total})

        def work():
            nonlocal engine, recognizer
            try:
                emit({"type": "status", "message": "正在初始化本地后端"})
                engine = engine or make_backend(settings, backend_name)
                recognizer = recognizer or make_asr(settings, backend_name)

                def wait_for_player():
                    if not player.wait(90):
                        raise RuntimeError("播放器或摄像头未在 90 秒内开始。")

                if camera:
                    result = run_session(
                        job_settings,
                        direction,
                        mode,
                        backend_name,
                        emit,
                        lambda out, manifest: camera.prepare(out, manifest, progress),
                        {"input_name": "摄像头直播", "source_kind": "webcam"},
                        run=run,
                        engine=engine,
                        asr=recognizer,
                        wait_for_player=wait_for_player,
                        hotwords=hotwords,
                    )
                elif workflow == "fast":
                    result = fast_file_run(
                        job_settings,
                        source,
                        direction,
                        mode,
                        backend_name,
                        emit,
                        run=run,
                        engine=engine,
                        asr=recognizer,
                        hotwords=hotwords,
                        source_kind="uploaded" if upload else ("synthetic" if backend_name == "mock" else "commons"),
                        for_dub=with_dub,
                    )
                elif workflow == "live":
                    result = file_sim(
                        job_settings,
                        source,
                        direction,
                        mode,
                        backend_name,
                        emit,
                        run=run,
                        engine=engine,
                        asr=recognizer,
                        wait_for_player=wait_for_player,
                        hotwords=hotwords,
                        source_kind="uploaded" if upload else ("synthetic" if backend_name == "mock" else "commons"),
                    )
                else:
                    result = run_pipeline(
                        job_settings,
                        source,
                        direction,
                        backend_name,
                        mode,
                        list(FOUR),
                        None,
                        backend=engine,
                        asr=recognizer,
                        source_kind="uploaded" if upload else ("synthetic" if backend_name == "mock" else "commons"),
                        max_seconds=settings.values["demo"]["live_max_sec"],
                        on_direction=lambda detection: emit({"type": "language", **detection}),
                        on_progress=progress,
                        hotwords=hotwords,
                        qwen_recheck=qwen_recheck,
                    )
                    export_comparison(result)
                    emit({"type": "done", "run_id": result.name})
                slim_run(result, read_json(result / "manifest.json"))
                if with_dub:
                    emit({"type": "dub", "run_id": result.name})
                    make_dub(result, read_json(result / "manifest.json"))
            except Exception as error:
                emit({"type": "error", "message": str(error)})
            finally:
                if camera:
                    camera.close()
                with guard:
                    jobs[run.name]["finished"] = True
                inference.release()

        Thread(target=work, daemon=True, name=f"cjvmt-{run.name}").start()
        return {"job": run.name}

    @app.get("/api/jobs/{job}")
    def history(job: str, after: int = 0):
        with guard:
            if job not in jobs:
                raise HTTPException(404)
            current = jobs[job]
            return {"events": current["events"][max(after, 0) :], "finished": current["finished"]}

    @app.post("/api/jobs/{job}/play")
    def play(job: str):
        with guard:
            if job not in jobs:
                raise HTTPException(404)
            jobs[job]["player"].set()
        return {"ok": True}

    @app.post("/api/jobs/{job}/capture")
    async def capture(job: str, request: Request):
        camera = jobs.get(job, {}).get("camera")
        if camera is None:
            raise HTTPException(404)
        data = bytearray()
        async for block in request.stream():
            if len(data) + len(block) > 300_000:
                raise HTTPException(413, "摄像头数据块过大。")
            data.extend(block)
        size = int.from_bytes(data[:4], "little")
        if len(data) < 4 or not 0 < size <= len(data) - 4:
            raise HTTPException(400, "摄像头数据块无效。")
        try:
            camera.push(bytes(data[4 : 4 + size]), bytes(data[4 + size :]) or None)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        return {"finished": camera.stopped.is_set()}

    @app.post("/api/jobs/{job}/stop")
    async def stop_capture(job: str, request: Request):
        current = jobs.get(job)
        if not current or current["camera"] is None:
            raise HTTPException(404)
        camera = current["camera"]
        recording = None
        accepted = False
        try:
            if camera.record_replay and request.headers.get("content-type", "").split(";")[0] in {
                "video/webm",
                "video/mp4",
            }:
                size = 0
                with NamedTemporaryFile(dir=settings.path("results") / job, suffix=".capture", delete=False) as stream:
                    recording = Path(stream.name)
                    async for block in request.stream():
                        size += len(block)
                        if size > 32 * 1024 * 1024:
                            raise HTTPException(413, "回放录像超过 32 MiB。")
                        stream.write(block)
                accepted = camera.finish_recording(recording if size else None) and bool(size)
        finally:
            if not accepted:
                camera.finish_recording(None)
                if recording is not None:
                    recording.unlink(missing_ok=True)
            current["player"].set()
        return {"ok": True}

    @app.get("/files/{run_id}/{asset:path}")
    def file(run_id: str, asset: str, compat: bool = False):
        permitted = re.fullmatch(
            r"media/(?:source\.mp4|dub\.(?:mp4|srt))|preview/L\d+\.mp4|subtitles_[A-Za-z_+]+\.(?:srt|vtt)"
            r"|session/(?:subtitles(?:_quick)?\.srt|latency_summary\.csv)|speakers\.json",
            asset,
        )
        if not permitted or Path(run_id).name != run_id:
            raise HTTPException(404)
        base = (
            folder
            if (folder / "manifest.json").is_file() and folder.name == run_id
            else settings.path("results") / run_id
        )
        path = base / asset
        if not path.is_file() or not path.resolve().is_relative_to(base.resolve()):
            raise HTTPException(404)
        if compat and path.suffix == ".mp4":
            with replay_encoding:
                destination = path.with_suffix(".webm")
                if not destination.resolve().is_relative_to(base.resolve()):
                    raise HTTPException(404)
                try:
                    path = browser_replay(path, settings)
                except (RuntimeError, OSError) as error:
                    raise HTTPException(503, "兼容视频暂时无法生成。") from error
        headers = {"Cache-Control": "no-store"}
        return FileResponse(path, headers=headers)

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app


def serve(
    settings: Settings, *, results: Path | None, panel: str, backend: str | None, host: str | None, port: int | None
) -> None:
    import uvicorn

    backend = backend or os.getenv("CJVMT_BACKEND", "mock")
    uvicorn.run(
        create_app(settings, results=results, panel=panel, backend_name=backend),
        host=host or settings.values["demo"]["host"],
        port=port or settings.values["demo"]["port"],
        access_log=False,
    )
