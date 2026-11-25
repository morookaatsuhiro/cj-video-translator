from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from cjvmt.config import Settings

app = typer.Typer(
    add_completion=False, no_args_is_help=True, pretty_exceptions_enable=False, help="CJ-VMT 中日多模态视频翻译原型"
)


def settings() -> Settings:
    return Settings.load()


@app.command()
def doctor() -> None:
    """检查 Python、FFmpeg、GPU、磁盘、网络与关键包。"""
    from cjvmt.doctor import doctor as run_doctor

    typer.echo(run_doctor(settings()))


@app.command()
def media(
    input: Annotated[Path, typer.Option("--input", exists=True, readable=True)],
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """读取媒体元数据并生成标准音轨、无声视频和音画源文件。"""
    from cjvmt.media import prepare

    config = settings()
    output = output or config.path("processed") / input.stem
    typer.echo(json.dumps(prepare(input, output, config), ensure_ascii=False, indent=2))


@app.command()
def asr(
    input: Annotated[Path, typer.Option("--input", exists=True)],
    direction: str = "ja2zh",
    backend: str = "mock",
    output: Annotated[Path | None, typer.Option(help="写入供 segment 读取的 JSONL")] = None,
) -> None:
    """运行语音识别，默认使用 Mock。"""
    from cjvmt.asr import make_asr

    config = settings()
    from cjvmt.clip import read_wav
    from cjvmt.store import write_jsonl

    if direction not in config.values["asr"]["lang"]:
        raise typer.BadParameter("请选择 ja2zh 或 zh2ja。")
    samples, rate = read_wav(input)
    info = make_asr(config, backend).transcribe(
        input, config.values["asr"]["lang"][direction], duration_ms=round(len(samples) * 1000 / rate)
    )
    if output:
        write_jsonl(output, info)
        typer.echo(output)
    else:
        typer.echo(json.dumps(info, ensure_ascii=False, indent=2))


@app.command()
def run(
    input: Annotated[Path, typer.Option("--input", exists=True, readable=True)],
    direction: str = "ja2zh",
    backend: str = "mock",
    mode: str = "compare",
    conditions: str = "T_neutral,T_asr,T+A,T+V,T+V+A",
    limit: int | None = None,
    path_file: Path | None = typer.Option(None, help="写入 run 目录路径，供脚本复用"),
    source_kind: str | None = None,
    license_name: str | None = None,
    mismatch_run: Path | None = None,
) -> None:
    """运行视频到字幕、逐句 JSONL 和结果目录。"""
    from cjvmt.demo import export_comparison
    from cjvmt.pipeline import run_pipeline

    run_path = run_pipeline(
        settings(),
        input,
        direction,
        backend,
        mode,
        [item.strip() for item in conditions.split(",") if item.strip()],
        limit,
        source_kind=source_kind,
        license_name=license_name,
        mismatch_run=mismatch_run,
    )
    export_comparison(run_path)
    if path_file:
        path_file.parent.mkdir(parents=True, exist_ok=True)
        path_file.write_text(str(run_path), encoding="utf-8")
    typer.echo(run_path)


@app.command()
def pairs(
    source: Annotated[Path | None, typer.Option("--source")] = None,
    backend: str = "local_omni",
    mode: str = "perceive",
    review_run: Annotated[Path | None, typer.Option("--review-run", exists=True, file_okay=False)] = None,
) -> None:
    """生成自录最小对的四条件对照表，或汇总已填写的人工核对表。"""
    from cjvmt.pairs import run_pairs, summarize_reviews

    if review_run is not None:
        if source is not None:
            raise typer.BadParameter("--source 与 --review-run 分别用于生成和复核。")
        try:
            typer.echo(json.dumps(summarize_reviews(review_run), ensure_ascii=False, indent=2))
        except (OSError, ValueError) as error:
            raise typer.BadParameter(str(error)) from error
        return

    config = settings()
    source = source or config.path("raw_own") / "pairs.csv"
    if not source.is_file():
        raise typer.BadParameter(f"找不到录制清单 {source}。")
    try:
        typer.echo(run_pairs(config, source, backend, mode))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@app.command()
def compare(run: Annotated[Path, typer.Option("--run", exists=True)]) -> None:
    """比较各条件译文，计算差异标记。"""
    from cjvmt.compare import compare_run

    typer.echo(compare_run(run))


@app.command()
def validate(run: Annotated[Path, typer.Option("--run", exists=True)]) -> None:
    """校验运行目录的 manifest、JSONL、字幕与媒体路径。"""
    from cjvmt.pipeline import validate_run

    typer.echo(f"VALID {run} · {validate_run(run, settings())} lines")


@app.command()
def report(run: Annotated[Path, typer.Option("--run", exists=True)]) -> None:
    from cjvmt.compare import compare_run
    from cjvmt.report import report_run

    compare_run(run)
    typer.echo(report_run(run))


@app.command("demo-html")
def demo_html(run: Path | None = None) -> None:
    from cjvmt.demo import export_site

    typer.echo(export_site(settings(), run))


@app.command()
def demo(
    mode: str = "replay",
    results: Path | None = None,
    host: str | None = None,
    port: int | None = None,
    backend: str | None = None,
) -> None:
    """启动交互网页（回放或上传翻译）。"""
    if mode not in {"replay", "live"}:
        raise typer.BadParameter("请选择 replay 或 live。")
    from cjvmt.demo import serve

    serve(settings(), results=results, panel=mode, backend=backend, host=host, port=port)


@app.command()
def realtime(
    input: str = "file_sim",
    backend: str | None = None,
    source: Path | None = None,
    direction: str = "ja2zh",
    mode: str = "guided",
    host: str | None = None,
    port: int | None = None,
) -> None:
    """运行本地视频模拟直播或启动实时网页。"""
    if input not in {"file_sim", "webcam"} or (input == "webcam" and source is not None):
        raise typer.BadParameter("请选择 file_sim 或 webcam。--source 仅用于 file_sim。")
    from cjvmt.demo import serve
    from cjvmt.realtime import file_sim

    config = settings()
    if source is None:
        serve(
            config, results=None, panel="webcam" if input == "webcam" else "live", backend=backend, host=host, port=port
        )
        return
    backend = backend or os.getenv("CJVMT_BACKEND", "mock")
    output = file_sim(
        config, source, direction, mode, backend, lambda event: typer.echo(json.dumps(event, ensure_ascii=False))
    )
    typer.echo(output)


@app.command()
def segment(
    input: Annotated[Path, typer.Option("--input", exists=True)],
    duration_ms: Annotated[int, typer.Option(min=1)],
    direction: str = "ja2zh",
    output: Annotated[Path | None, typer.Option(help="写入供 clip 读取的 JSONL")] = None,
) -> None:
    """将 ASR JSONL 转换为逐句 JSONL。"""
    from cjvmt.segment import split_sentences
    from cjvmt.store import read_jsonl, write_jsonl

    config = settings()
    if direction not in config.values["asr"]["lang"]:
        raise typer.BadParameter("请选择 ja2zh 或 zh2ja。")
    rows = split_sentences(read_jsonl(input), direction, config, duration_ms)
    if output:
        write_jsonl(output, rows)
        typer.echo(output)
    else:
        for line in rows:
            typer.echo(json.dumps(line, ensure_ascii=False))


@app.command()
def clip(
    input: Annotated[Path, typer.Option("--input", exists=True)],
    media: Annotated[Path | None, typer.Option(exists=True, file_okay=False, help="media 生成的目录")] = None,
    output: Annotated[Path | None, typer.Option(file_okay=False, help="片段及 clips.jsonl 的输出目录")] = None,
) -> None:
    """从切句和标准媒体生成片段，或校验已有运行。"""
    import re

    from cjvmt.clip import make_clip, read_wav
    from cjvmt.media import probe
    from cjvmt.pipeline import validate_run
    from cjvmt.store import read_jsonl, write_jsonl

    config = settings()
    if input.is_dir() and media is None and output is None:
        typer.echo(f"片段校验通过，共 {validate_run(input, config)} 句。")
        return
    if not input.is_file() or media is None or output is None:
        raise typer.BadParameter("请传入切句 JSONL，并设置 --media 和 --output。")
    duration_ms = probe(media / "source.mp4", config)["duration_ms"]
    lines = read_jsonl(input)
    ids: set[str] = set()
    for line in lines:
        line_id = line["line_id"]
        if not re.fullmatch(r"L[0-9]+", line_id) or line_id in ids:
            raise typer.BadParameter(f"句子编号无效或重复：{line_id}")
        if not 0 <= line["t_start_abs_ms"] < line["t_end_abs_ms"] <= duration_ms:
            raise typer.BadParameter(f"句子时间超出媒体范围：{line_id}")
        ids.add(line_id)

    audio = read_wav(media / "audio.wav")
    for line in lines:
        line["clip"] = make_clip(media, output, line, duration_ms, config, audio=audio)
    path = output / "clips.jsonl"
    write_jsonl(path, lines)
    typer.echo(path)


@app.command()
def translate(
    input: Annotated[Path, typer.Option("--input", exists=True)],
    direction: str = "ja2zh",
    backend: str = "mock",
    mode: str = "compare",
    conditions: str = "T_neutral,T_asr,T+A,T+V,T+V+A",
) -> None:
    """翻译 clips.jsonl 的句子，或输出已有运行的调用记录。"""
    from cjvmt.backends import make_backend
    from cjvmt.checks import inspect_text
    from cjvmt.config import file_hash
    from cjvmt.store import read_jsonl
    from cjvmt.translate import CONDITIONS, Translator

    if input.is_dir():
        for line in read_jsonl(input / "predictions.jsonl"):
            typer.echo(json.dumps(line, ensure_ascii=False))
        return

    config = settings()
    if direction not in config.values["asr"]["lang"] or mode not in config.values["prompt_modes"]["available"]:
        raise typer.BadParameter("翻译方向或提示词模式无效。")
    selected = [item.strip() for item in conditions.split(",") if item.strip()]
    available = {condition for condition in CONDITIONS if not condition.endswith("_shuf")}
    if not selected or len(set(selected)) != len(selected) or set(selected) - available:
        raise typer.BadParameter("请选择有效且不重复的翻译条件。错配对照请用 cjvmt run --mismatch-run。")
    engine = make_backend(config, backend)
    translator = Translator(config, engine)
    source_hash = file_hash(input)
    for line in read_jsonl(input):
        item_id = f"{source_hash}:{line['line_id']}"
        outputs, checks = {}, {}
        for condition in selected:
            result, calls = translator.translate_clip(
                line, input.parent, item_id=item_id, direction=direction, mode=mode, condition=condition
            )
            outputs[condition] = {**result, "calls": calls}
            checks[condition] = inspect_text(result["text"], direction, result.get("observation"))
        typer.echo(
            json.dumps({**line, "model": engine.identity, "outputs": outputs, "checks": checks}, ensure_ascii=False)
        )


@app.command()
def wmt(
    action: Annotated[str, typer.Argument(help="prepare、audit 或 run")],
    dataset: Annotated[str, typer.Option("--set")] = "wmt26",
    source: Annotated[Path | None, typer.Option("--source")] = None,
    media: Annotated[Path | None, typer.Option("--media")] = None,
    prepared: Annotated[Path | None, typer.Option("--prepared")] = None,
    limit: Annotated[int | None, typer.Option(min=1)] = None,
    backend: str = "local_omni",
    audit: Path | None = None,
    detector: Path | None = None,
    recognizer: Path | None = None,
    ocr_frames: Annotated[int | None, typer.Option(min=1)] = None,
    mismatch: bool = False,
    resume: Path | None = None,
) -> None:
    """核对官方 WMT 发布文件并评测。"""
    from cjvmt.wmt import load_release, record_release

    config = settings()
    if dataset not in {"wmt25", "wmt26"}:
        raise typer.BadParameter("请选择 wmt25 或 wmt26。")
    if action == "prepare":
        source = source or config.path("external") / dataset / f"{dataset}-genmt.jsonl"
        if not source.is_file() or (media and not media.exists()):
            raise typer.BadParameter("请传入已有的转写 JSONL 和可选的媒体 ZIP 或目录。")
        release = load_release(config, dataset, source, media)
        result = record_release(config, release, source, media)
        typer.echo(json.dumps({**release.schema, **release.summary, "run": str(result)}, ensure_ascii=False, indent=2))
        if release.summary["discrepancies"]:
            raise typer.Exit(1)
        return
    if action == "audit" and dataset == "wmt26" and prepared and detector and recognizer:
        from cjvmt.wmt_eval import audit_release

        typer.echo(audit_release(config, prepared, detector, recognizer, ocr_frames))
        return
    if action == "run" and prepared:
        from cjvmt.wmt_eval import evaluate

        typer.echo(evaluate(config, prepared, dataset, backend, limit, audit=audit, mismatch=mismatch, resume=resume))
        return
    raise typer.BadParameter("使用 prepare；audit 需要 --prepared、--detector、--recognizer，run 需要 --prepared。")


@app.command()
def score(input: Annotated[Path, typer.Option("--input", exists=True)]) -> None:
    from cjvmt.wmt_eval import score_release

    typer.echo(score_release(input, settings().path("reports")))


@app.command()
def stats(input: Annotated[Path, typer.Option("--input", exists=True)]) -> None:
    from cjvmt.wmt_eval import score_release

    score_release(input, settings().path("reports"))
    comparisons = input / "comparisons.csv"
    if not comparisons.is_file():
        raise typer.BadParameter("此运行没有配对统计结果。可用本地 COMET 检查点补算。")
    typer.echo(comparisons)


def main() -> None:
    app()
