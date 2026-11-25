from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

import numpy as np

from cjvmt.asr import revise_lines
from cjvmt.clip import read_wav, write_wav
from cjvmt.config import Settings
from cjvmt.media import ffmpeg, probe, video_encode
from cjvmt.pipeline import subtitle
from cjvmt.segment import split_sentences
from cjvmt.store import read_json, read_jsonl, write_json, write_text
from cjvmt.translate import Translator

DUBBABLE = {"webcam", "uploaded", "own", "provided"}
TARGET = {"ja2zh": ("zh", "普通话"), "zh2ja": ("ja", "日语")}


def runtime(settings: Settings) -> tuple[Path, Path, Path]:
    return tuple(
        Path(os.getenv(variable, str(settings.root / default))).expanduser().absolute()
        for variable, default in (
            ("CJVMT_TTS_PYTHON", ".cache/cosyvoice-env/bin/python"),
            ("CJVMT_TTS_REPO", ".cache/CosyVoice"),
            ("CJVMT_TTS_MODEL", ".cache/cosyvoice-model"),
        )
    )


def enabled(settings: Settings) -> bool:
    python, repo, model = runtime(settings)
    weights = ("cosyvoice2.yaml", "llm.pt", "flow.pt", "hift.pt", "speech_tokenizer_v2.onnx", "campplus.onnx")
    files = (python, repo / "cosyvoice/cli/cosyvoice.py", *(model / name for name in weights))
    return (
        settings.values["tts"]["enabled"]
        and all(path.is_file() for path in files)
        and (model / "CosyVoice-BlankEN").is_dir()
    )


def eligible(run: Path, manifest: dict) -> bool:
    return (
        manifest.get("status") == "complete"
        and manifest.get("source_kind") in DUBBABLE
        and manifest.get("model", {}).get("backend") != "mock"
        and manifest.get("direction") in TARGET
        and (manifest.get("kind") == "realtime" or bool(manifest.get("line_count")))
        and (run / "media/source.mp4").is_file()
    )


def translated_lines(run: Path, manifest: dict) -> list[dict]:
    if manifest["kind"] == "realtime":
        return [
            {**row, "original": row["text_asr"], "translation": row.get("multimodal") or row.get("quick")}
            for row in read_jsonl(run / "session/session.jsonl")
        ]
    results = []
    for row in read_jsonl(run / "lines.jsonl"):
        output = row["outputs"].get("T+V+A") or next(iter(row["outputs"].values()))
        results.append(
            {
                "line_id": row["sample_id"].rsplit("-", 1)[-1],
                "start_ms": row["utterance"]["t_start_abs_ms"],
                "end_ms": row["utterance"]["t_end_abs_ms"],
                "original": row["text"]["text_asr"],
                "translation": output["text"],
                "observation": output.get("observation"),
                **({"asr_recheck": row["asr_recheck"]} if "asr_recheck" in row else {}),
            }
        )
    return results


def reference_audio(samples: np.ndarray, rate: int, start_ms: int, end_ms: int, minimum: float) -> np.ndarray:
    start = min(len(samples), max(0, round(start_ms * rate / 1000)))
    end = min(len(samples), max(start, round(end_ms * rate / 1000)))
    length = min(len(samples), max(min(round(12 * rate), end - start), round(rate * minimum)))
    begin = min(len(samples) - length, max(0, start - (length - (end - start)) // 2))
    return samples[begin : begin + length]


def voice_instructions(settings: Settings, language: str, observation: dict) -> str:
    tone = observation.get("tone")
    tone = tone if tone in settings.values["tone"]["labels"] and tone != "不确定" else "自然"
    strength = {"弱": "轻微", "中": "适度", "强": "明显"}.get(observation.get("strength"), "")
    return f"请用{language}说话。" + settings.prompts["tts_instruct"].format(tone=strength + tone) + "。"


def synthesize(
    settings: Settings,
    work: Path,
    lines: list[dict],
    log: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    python, repo, model = runtime(settings)
    request = work / "request.json"
    write_json(
        request,
        {"seed": settings.values["seed"], "lines": lines, "intonation": settings.values["tts"].get("intonation")},
    )
    args = [str(python), str(Path(__file__).with_name("cosyvoice_worker.py")), str(repo), str(model), str(request)]
    env = {**os.environ, "HF_HUB_OFFLINE": "1"}
    env["LD_LIBRARY_PATH"] = env.get("CJVMT_LOCAL_OMNI_LD_LIBRARY_PATH", "")
    finished = Event()
    outputs = [Path(line["output"]) for line in lines]

    def watch_outputs() -> None:
        previous = 0
        while not finished.wait(0.5):
            completed = sum(path.is_file() for path in outputs)
            if completed != previous:
                on_progress(completed, len(outputs))
                previous = completed

    monitor = None
    if on_progress:
        on_progress(0, len(outputs))
        monitor = Thread(target=watch_outputs, name="dubbing-progress", daemon=True)
        monitor.start()
    try:
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(
                args,
                check=True,
                timeout=settings.values["tts"]["timeout_sec"],
                env=env,
                cwd=repo,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("CosyVoice 合成失败，请查看此次会话的配音日志。") from error
    finally:
        if monitor:
            finished.set()
            monitor.join()
    if on_progress:
        on_progress(sum(path.is_file() for path in outputs), len(outputs))


def generate_dub(
    run: Path,
    manifest: dict,
    settings: Settings,
    translator: Translator,
    release_translation: Callable[[], None],
    progress: Callable[[str], None],
    asr=None,
    *,
    on_synthesis_progress: Callable[[int, int], None] | None = None,
) -> dict:
    source = run / "media/source.mp4"
    original = run / "media/audio.wav"
    samples, source_rate = read_wav(original)
    if not samples.any():
        raise ValueError("原视频没有可用的人声参考。")
    duration = probe(source, settings)["duration_ms"]
    opts = settings.values["tts"]
    rate = opts["sample_rate"]
    intonation = opts.get("intonation", {}).get("enabled", False)
    language, language_name = TARGET[manifest["direction"]]
    direction = manifest["direction"]
    lines = sorted(
        (row for row in translated_lines(run, manifest) if row["translation"] and row["end_ms"] > row["start_ms"]),
        key=lambda row: row["start_ms"],
    )
    if manifest["kind"] == "realtime" and asr is not None:
        progress("正在重新识别整段原声")
        hotwords = tuple(manifest.get("asr_hotwords", ()))
        groups = asr.transcribe(
            original,
            settings.values["asr"]["lang"][direction],
            duration_ms=duration,
        )
        sentences = split_sentences(groups, direction, settings, duration)
        if sentences:
            revise_lines(asr, sentences, samples, source_rate, direction, settings, hotwords)
            lines = [
                {
                    "line_id": line["line_id"],
                    "start_ms": line["t_start_abs_ms"],
                    "end_ms": line["t_end_abs_ms"],
                    "original": line["text_asr"],
                    "context": line["context"],
                    **({"asr_recheck": line["asr_recheck"]} if "asr_recheck" in line else {}),
                }
                for line in sentences
            ]
    if not lines:
        raise ValueError("此会话没有可配音的译文。")

    with TemporaryDirectory(dir=run) as temporary:
        work = Path(temporary)
        requests = []
        progress("正在分析原声语气")
        for index, row in enumerate(lines):
            reference = work / f"ref-{index}.wav"
            voice = reference_audio(samples, source_rate, row["start_ms"], row["end_ms"], opts["ref_audio_min_sec"])
            write_wav(reference, voice, source_rate)
            observation = row.get("observation")
            translation = row.get("translation")
            sentence = work / f"sentence-{index}.wav"
            if intonation or not observation or not translation:
                write_wav(
                    sentence, reference_audio(samples, source_rate, row["start_ms"], row["end_ms"], 0), source_rate
                )
            if not observation or not translation:
                item_id = f"{run.name}:dub:{row['line_id']}"
                if translation:
                    observation, _ = translator.observe(
                        item_id, condition="T+A", direction=direction, source=row["original"], audio=sentence
                    )
                else:
                    output, _ = translator.line(
                        item_id=item_id,
                        mode="perceive",
                        condition="T+A",
                        direction=direction,
                        source=row["original"],
                        context=row["context"],
                        media={"audio": sentence},
                    )
                    translation, observation = output["text"], output["observation"]
            row["translation"] = translation
            requests.append(
                {
                    "text": row["translation"],
                    "instruction": voice_instructions(settings, language_name, observation),
                    "reference": str(reference),
                    "source_sentence": str(sentence) if intonation else None,
                    "output": str(work / f"voice-{index}.wav"),
                    "tone": observation.get("tone"),
                    "strength": observation.get("strength"),
                }
            )

        release_translation()
        progress("正在合成目标语言语音并匹配原声语调" if intonation else "正在合成目标语言语音")
        synthesize(settings, work, requests, run / "dubbing.log", on_synthesis_progress)
        evidence = work / "prosody.json"
        prosody = read_json(evidence) if evidence.is_file() else None
        progress("正在对齐语音并导出视频")
        parts, captions = [], []
        cursor = 0
        for index, (row, request) in enumerate(zip(lines, requests, strict=True)):
            voice, voice_rate = read_wav(Path(request["output"]))
            if voice_rate != rate or not voice.any():
                raise RuntimeError("合成音频为空或采样率不匹配。")
            start = max(cursor, round(row["start_ms"] * rate / 1000))
            next_start = lines[index + 1]["start_ms"] if index + 1 < len(lines) else duration
            window_end = max(row["end_ms"], next_start)
            available = max(1, round(window_end * rate / 1000) - start)
            speed = max(1, min(len(voice) / available, opts["max_speedup"]))
            if speed > 1:
                aligned = work / f"aligned-{index}.wav"
                ffmpeg(settings, ["-i", request["output"], "-af", f"atempo={speed:.6f}", str(aligned)])
                voice, _ = read_wav(aligned)
            parts.extend((np.zeros(start - cursor, dtype="<i2"), voice))
            cursor = start + len(voice)
            captions.append(
                {
                    "line_id": row["line_id"],
                    "start_ms": round(start * 1000 / rate),
                    "end_ms": round(cursor * 1000 / rate),
                    "text_asr": row["original"],
                    "multimodal": row["translation"],
                    "tone": request["tone"],
                    "strength": request["strength"],
                    **({"asr_recheck": row["asr_recheck"]} if "asr_recheck" in row else {}),
                    **({"intonation": prosody["lines"][index]} if prosody else {}),
                }
            )
        parts.append(np.zeros(max(0, round(duration * rate / 1000) - cursor), dtype="<i2"))
        audio = np.concatenate(parts)
        wav = work / "dub.wav"
        write_wav(wav, audio, rate)
        video = work / "dub.mp4"
        padding = max(0, len(audio) / rate - duration / 1000)
        filters = [
            "drawtext=font='Noto Sans CJK SC':text='AI 合成配音':x=16:y=16:fontsize=h/22:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=5"
        ]
        if padding:
            filters.insert(0, f"tpad=stop_mode=clone:stop_duration={padding:.3f}")
        ffmpeg(
            settings,
            [
                "-i",
                str(source),
                "-i",
                str(wav),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-map",
                "0:a:0?",
                "-vf",
                ",".join(filters),
                *video_encode(),
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-metadata:s:a:0",
                f"language={'jpn' if language == 'ja' else 'zho'}",
                "-metadata:s:a:0",
                "handler_name=AI dub",
                "-metadata:s:a:1",
                "handler_name=Original",
                "-disposition:a:0",
                "default",
                "-disposition:a:1",
                "0",
                str(video),
            ],
        )
        srt = subtitle(
            [
                {
                    "utterance": {"t_start_abs_ms": row["start_ms"], "t_end_abs_ms": row["end_ms"]},
                    "text": {"text_asr": row["text_asr"]},
                    "outputs": {"final": {"text": row["multimodal"]}},
                }
                for row in captions
            ],
            "final",
        )
        write_text(run / "media/dub.srt", srt)
        wav.replace(run / "media/dub.wav")
        video.replace(run / "media/dub.mp4")
    return {
        "status": "complete",
        "model": opts["model"],
        "language": language,
        "label": "AI 合成配音",
        "lines": captions,
        **({"prosody": {key: value for key, value in prosody.items() if key != "lines"}} if prosody else {}),
    }
