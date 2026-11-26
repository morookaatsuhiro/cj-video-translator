from __future__ import annotations

import atexit
import json
import os
import re
import selectors
import signal
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread

import numpy as np

from cjvmt.asr import NUMBER, revise_lines
from cjvmt.backends import Request
from cjvmt.clip import read_wav, write_wav
from cjvmt.config import Settings
from cjvmt.media import ffmpeg, probe, video_encode
from cjvmt.pipeline import subtitle
from cjvmt.segment import split_sentences
from cjvmt.store import read_json, read_jsonl, write_json, write_text
from cjvmt.translate import Translator, model_json_object

_ORIG_SUBPROCESS_RUN = subprocess.run


@lru_cache(maxsize=1)
def gpu_total_mib() -> int:
    try:
        raw = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        return int(raw.splitlines()[0].strip()) if raw else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def can_keep_resident(settings: Settings) -> bool:
    override = os.getenv("CJVMT_KEEP_OMNI_RESIDENT")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes"}
    return bool(settings.values["tts"].get("keep_resident", True)) and gpu_total_mib() >= 36000


class _CosyVoiceDaemon:
    def __init__(self) -> None:
        self.lock = Lock()
        self.process: subprocess.Popen | None = None
        self.key: tuple[Path, Path, Path] | None = None

    def close(self) -> None:
        process = self.process
        self.process = None
        self.key = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=4)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
        if process.stdout:
            process.stdout.close()

    def _read(self, timeout: float) -> dict:
        assert self.process and self.process.stdout
        with selectors.DefaultSelector() as sel:
            sel.register(self.process.stdout, selectors.EVENT_READ)
            if not sel.select(timeout):
                self.close()
                raise RuntimeError("CosyVoice 常驻进程响应超时。")
            line = self.process.stdout.readline()
        if not line:
            self.close()
            raise RuntimeError("CosyVoice 常驻进程提前退出。")
        return json.loads(line)

    def run_job(self, settings: Settings, request: Path, log: Path) -> None:
        python, repo, model = runtime(settings)
        key = (python, repo, model)
        env = {**os.environ, "HF_HUB_OFFLINE": "1"}
        env["LD_LIBRARY_PATH"] = env.get("CJVMT_LOCAL_OMNI_LD_LIBRARY_PATH", "")
        with self.lock:
            if self.process is None or self.process.poll() is not None or self.key != key:
                self.close()
                daemon_log = settings.path("cache") / "cosyvoice_daemon.log"
                daemon_log.parent.mkdir(parents=True, exist_ok=True)
                with daemon_log.open("a", encoding="utf-8") as stream:
                    self.process = subprocess.Popen(
                        [
                            str(python),
                            str(Path(__file__).with_name("cosyvoice_worker.py")),
                            str(repo),
                            str(model),
                            "--daemon",
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=stream,
                        text=True,
                        encoding="utf-8",
                        env=env,
                        cwd=repo,
                        start_new_session=True,
                    )
                self.key = key
                if not self._read(240).get("ready"):
                    self.close()
                    raise RuntimeError("CosyVoice 常驻服务初始化失败。")
            assert self.process and self.process.stdin
            self.process.stdin.write(json.dumps({"request": str(request)}, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            reply = self._read(float(settings.values["tts"]["timeout_sec"]))
            if "error" in reply:
                write_text(log, f"CosyVoice daemon error: {reply['error']}\n")
                raise RuntimeError(f"CosyVoice 合成失败：{reply['error']}")
            write_text(log, "CosyVoice resident daemon: ok\n")


_COSY_DAEMON = _CosyVoiceDaemon()
atexit.register(_COSY_DAEMON.close)

DUBBABLE = {"webcam", "uploaded", "own", "provided"}
TARGET = {"ja2zh": ("zh", "普通话"), "zh2ja": ("ja", "日语")}
BASE_CPS = {"zh": 5.0, "ja": 4.3}
PUNCT_RE = re.compile(r"[\s，,。\.？！?!、；;：:「」『』“”\"'（）()—\-…]+")
KANJI_RE = re.compile(r"[\u4e00-\u9fff々〆ヵヶ]")


def _katakana_to_hiragana(text: str) -> str:
    return "".join(chr(ord(ch) - 0x60) if 0x30A1 <= ord(ch) <= 0x30F6 else ch for ch in text)


def _get_fugashi_tagger():
    from cjvmt.checks import tagger

    instance = tagger()
    if instance is None:
        import fugashi

        return fugashi.Tagger()
    return instance


def japanese_to_kana(text: str) -> str:
    """Use contextual UniDic readings. Preserve unrecognized characters instead of dropping them."""
    if not text or not KANJI_RE.search(text):
        return text
    analyzer = _get_fugashi_tagger()
    tokens: list[tuple[str, str]] = []
    for token in analyzer(text):
        surface = token.surface
        pos1 = getattr(token.feature, "pos1", "")
        if not KANJI_RE.search(surface):
            tokens.append((surface, pos1))
            continue
        reading = getattr(token.feature, "kana", None) or getattr(token.feature, "pron", None)
        if reading and reading != "*" and not KANJI_RE.search(reading):
            tokens.append((_katakana_to_hiragana(reading), pos1))
            continue
        # Fall back to single-character readings while preserving genuinely unknown names.
        converted = []
        for ch in surface:
            if not KANJI_RE.search(ch):
                converted.append(ch)
                continue
            for sub in analyzer(ch):
                cand = getattr(sub.feature, "kana", None) or getattr(sub.feature, "pron", None)
                if cand and cand != "*" and not KANJI_RE.search(cand):
                    converted.append(_katakana_to_hiragana(cand))
                    break
            else:
                converted.append(ch)
        tokens.append(("".join(converted), pos1))

    # Add light phrasing commas inside long unbroken kana runs (>= 14 chars) at natural case-particle boundaries
    # while avoiding breaking right before a sentence ending (remaining >= 6 chars).
    out: list[str] = []
    run_len = 0
    for idx, (piece, pos1) in enumerate(tokens):
        out.append(piece)
        if any(p in piece for p in "、。？！?!,.\n"):
            run_len = 0
            continue
        run_len += len(piece)
        remaining = "".join(t[0] for t in tokens[idx + 1 :])
        next_break = min((remaining.find(p) for p in "、。？！?!,.\n" if p in remaining), default=len(remaining))
        if run_len >= 14 and next_break >= 6 and pos1 == "助詞":
            out.append("、")
            run_len = 0
    return "".join(out)


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


def sentence_audio(samples: np.ndarray, rate: int, start_ms: int, end_ms: int) -> np.ndarray:
    start = max(0, round(start_ms * rate / 1000))
    end = max(start, round(end_ms * rate / 1000))
    return samples[start:end]


def voice_reference(
    samples: np.ndarray, rate: int, row: dict, annotations: dict, opts: dict
) -> tuple[np.ndarray, dict]:
    start, end = row["start_ms"], row["end_ms"]
    ref_id = row["line_id"]
    group = annotations.get(ref_id, {})
    ids = group.get("speaker_ids", [])
    minimum = opts["ref_audio_min_sec"] * 1000
    maximum = round(opts.get("ref_audio_max_sec", 12) * 1000)
    # Re-ASR can reuse a line ID with different boundaries. Its old speaker label is then ambiguous.
    if end - start < minimum and (group.get("start_ms"), group.get("end_ms")) == (start, end) and len(ids) == 1:
        candidates = [
            (candidate["speech_ms"] / (candidate["end_ms"] - candidate["start_ms"]), key, candidate)
            for key, candidate in annotations.items()
            if candidate.get("speaker_ids") == ids
            and 0 <= candidate["start_ms"] < candidate["end_ms"] <= len(samples) * 1000 / rate
            and minimum <= candidate.get("speech_ms", 0) <= candidate["end_ms"] - candidate["start_ms"] <= maximum
        ]
        if candidates:
            _, ref_id, candidate = max(candidates, key=lambda item: item[:2])
            start, end = candidate["start_ms"], candidate["end_ms"]
    start += max(0, (end - start - maximum) // 2)
    end = min(end, start + maximum)
    wave = sentence_audio(samples, rate, start, end)
    if not wave.any():
        raise ValueError(f"{row['line_id']} 的音色参考没有可用音频。")
    info = {"line_id": ref_id, "start_ms": start, "end_ms": start + round(len(wave) * 1000 / rate)}
    # Very short clips need enough samples for feature extraction. Silence cannot introduce a neighbouring voice.
    pad = max(0, round(rate * opts.get("ref_audio_pad_floor_sec", 0.5)) - len(wave))
    return np.pad(wave, (0, pad)) if pad else wave, info


def duration_hint(direction: str, slot_ms: int) -> str:
    """Guide translation length so dubbed speech matches the speaker's on-screen pacing."""
    if slot_ms < 1500:
        return ""
    seconds = slot_ms / 1000.0
    if direction == "ja2zh":
        lo = max(4, round(seconds * 3.4))
        hi = max(lo + 4, round(seconds * 4.8))
        return (
            f"配音节奏要求：原句语音约 {seconds:.1f} 秒，请用自然完整的简体中文口语表达"
            f"（建议约 {lo} 到 {hi} 字），不要过度压缩省略细节，以便中文配音与原视频说话时长接近。"
        )
    hi = max(8, round(seconds * 5.6))
    return (
        f"配音节奏要求：原句语音约 {seconds:.1f} 秒，请用简洁自然的日语口语表达"
        f"（建议控制在约 {hi} 字以内），避免过度冗长，以便日语配音与原视频说话时长接近。"
    )


def estimate_tts_speed(text: str, language: str, slot_ms: int, speed_range: tuple[float, float]) -> float:
    """Estimate a natural CosyVoice2 native speaking rate before synthesis."""
    chars = len(PUNCT_RE.sub("", text or ""))
    if chars < 3 or slot_ms < 700:
        return 1.0
    est_ms = (chars / BASE_CPS.get(language, 5.2)) * 1000.0
    ratio = est_ms / max(500.0, float(slot_ms))
    low, high = speed_range
    # Keep native generation speed gently biased toward target without extreme distortion.
    if 0.92 <= ratio <= 1.08:
        return 1.0
    return round(max(low, min(high, ratio)), 2)


def voice_instructions(settings: Settings, language: str, observation: dict, speed: float = 1.0) -> str:
    tone = observation.get("tone")
    tone = tone if tone in settings.values["tone"]["labels"] and tone != "不确定" else "自然"
    strength = {"弱": "轻微", "中": "适度", "强": "明显"}.get(observation.get("strength"), "")
    pace = ""
    if speed <= 0.88:
        pace = "语速稍慢、从容清晰。"
    elif speed >= 1.12:
        pace = "语速稍快、紧凑自然。"
    return f"请用{language}说话。{pace}" + settings.prompts["tts_instruct"].format(tone=strength + tone) + "。"


def _apply_fades_and_level(samples: np.ndarray, rate: int, fade_ms: int, target_dbfs: float = -18.0) -> np.ndarray:
    """Smooth segment edges and gently level RMS loudness without clipping."""
    wave = samples.astype(np.float64)
    rms = float(np.sqrt(np.mean(wave**2))) if len(wave) else 0.0
    if rms > 80.0:
        desired_rms = 32767.0 * (10 ** (target_dbfs / 20.0))
        gain = max(0.55, min(2.2, desired_rms / rms))
        peak = float(np.max(np.abs(wave)))
        if peak * gain > 30500.0:
            gain = 30500.0 / peak
        wave *= gain
    fade = min(len(wave) // 4, max(0, round(rate * fade_ms / 1000)))
    if fade > 1:
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, fade))
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return np.clip(np.round(wave), -32768, 32767).astype("<i2")


def _blend_background(
    dub_audio: np.ndarray,
    speech_mask: np.ndarray,
    original: np.ndarray,
    source_rate: int,
    target_rate: int,
    duck_db: float,
) -> np.ndarray:
    """Layer a subtle, smoothly ducked ambient bed from original audio to prevent jarring dead silence."""
    if not len(original) or duck_db <= -60.0:
        return dub_audio
    src_len = len(original)
    target_orig_len = round(src_len * target_rate / source_rate)
    if target_orig_len < 2:
        return dub_audio
    idx = np.linspace(0.0, src_len - 1, target_orig_len)
    bed = np.interp(idx, np.arange(src_len), original.astype(np.float64))
    if len(bed) < len(dub_audio):
        bed = np.pad(bed, (0, len(dub_audio) - len(bed)))
    else:
        bed = bed[: len(dub_audio)]
    gap_gain = 10 ** (duck_db / 20.0)
    speech_gain = gap_gain * 0.35
    gain_curve = np.where(speech_mask, speech_gain, gap_gain)
    # Smooth envelope (~60ms) so background ducking breathes naturally without pumping.
    smooth = max(3, round(target_rate * 0.06))
    kernel = np.ones(smooth, dtype=np.float64) / smooth
    gain_curve = np.convolve(gain_curve, kernel, mode="same")
    mixed = dub_audio.astype(np.float64) + bed * gain_curve
    return np.clip(np.round(mixed), -32768, 32767).astype("<i2")


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
        {
            "seed": settings.values["seed"],
            "lines": lines,
            "intonation": settings.values["tts"].get("intonation"),
            "trim_silence_db": settings.values["tts"].get("trim_silence_db", -42.0),
        },
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
    use_daemon = (
        subprocess.run is _ORIG_SUBPROCESS_RUN
        and os.getenv("PYTEST_CURRENT_TEST") is None
        and can_keep_resident(settings)
    )
    try:
        if use_daemon:
            _COSY_DAEMON.run_job(settings, request, log)
        else:
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


def spoken_draft(text: str, source: str, language: str, settings: Settings) -> str:
    if language != "ja":
        return text
    readings = {t["target"]: t["reading"] for t in settings.matched_terms(source) if t["reading"]}
    if readings:
        pattern = "|".join(re.escape(word) for word in sorted(readings, key=len, reverse=True))
        text = re.sub(pattern, lambda match: readings[match[0]], text)
    return japanese_to_kana(text)


def faithful_compaction(original: str, candidate: str, targets: list[str]) -> bool:
    if not 0 < len(candidate) < len(original) or "\n" in candidate:
        return False
    if NUMBER.findall(original) != NUMBER.findall(candidate):
        return False
    return all(word not in original or word in candidate for word in targets)


def entailment_confirmed(reply: str) -> bool:
    result = model_json_object(reply)
    return result.get("certain") is True and result.get("unsupported") == []


def shorten_overruns(settings, translator, lines, requests, work, run, direction, language, prosody, release, progress):
    """Use measured audio, then validate wording and accept only a shorter second recording."""
    if not hasattr(translator, "infer_batch"):
        return
    proposals = []
    slots = []
    for idx, (row, req) in enumerate(zip(lines, requests, strict=True)):
        wave, sr = read_wav(Path(req["output"]))
        slot = row["end_ms"] - row["start_ms"]
        if len(wave) * 1000 / sr <= slot * settings.values["tts"]["max_speedup"]:
            continue
        row["first_tts_ms"] = round(len(wave) * 1000 / sr)
        draft = row["translation"]
        user = json.dumps({"字幕定稿": draft, "原句": row["original"], "目标时长毫秒": slot}, ensure_ascii=False)
        user += duration_hint(direction, slot) + settings.terminology_note(row["original"])
        request = Request(
            system=settings.prompts["dub_compact"],
            user=user,
            source=draft,
            direction=direction,
            condition="T_asr",
            task="dub_compact",
            max_tokens=192,
        )
        slots.append((idx, len(wave) / sr))
        proposals.append((request, f"{run.name}:dub-compact:{row['line_id']}"))
    if not slots:
        return
    progress("正在核对超时句的精简配音稿")
    candidates, checks = [], []
    try:
        for (idx, seconds), pred in zip(slots, translator.infer_batch(proposals), strict=True):
            row = lines[idx]
            original, candidate = row["translation"], pred["text"].strip()
            terms = settings.matched_terms(row["original"])
            targets = [term["target"] for term in terms]
            if not faithful_compaction(original, candidate, targets):
                continue
            for order, (premise, claim) in enumerate(((original, candidate), (candidate, original))):
                req = Request(
                    system=settings.prompts["dub_entailment"],
                    user=json.dumps(
                        {"原语台词": row["original"], "第一段": premise, "第二段": claim}, ensure_ascii=False
                    ),
                    source=premise,
                    direction=direction,
                    condition="T_asr",
                    task="dub_entailment",
                    max_tokens=128,
                )
                checks.append((req, f"{run.name}:dub-entailment:{row['line_id']}:{order}"))
            candidates.append((idx, seconds, candidate))
        if not checks:
            return
        verdicts = translator.infer_batch(checks)
    except (RuntimeError, TimeoutError, OSError):
        return
    finally:
        release()
    retry = work / "compact"
    retry.mkdir()
    retry_requests, accepted = [], []
    for (idx, seconds, script), forward, backward in zip(candidates, verdicts[::2], verdicts[1::2], strict=True):
        if not all(entailment_confirmed(check["text"]) for check in (forward, backward)):
            continue
        req = {
            **requests[idx],
            "text": spoken_draft(script, lines[idx]["original"], language, settings),
            "output": str(retry / f"voice-{idx}.wav"),
        }
        retry_requests.append(req)
        accepted.append((idx, seconds, script))
    if not accepted:
        return
    try:
        synthesize(settings, retry, retry_requests, run / "dubbing-compact.log")
    except (RuntimeError, OSError):
        return
    second = read_json(retry / "prosody.json") if (retry / "prosody.json").is_file() else None
    for j, ((idx, seconds, script), req) in enumerate(zip(accepted, retry_requests, strict=True)):
        audio, sr = read_wav(Path(req["output"]))
        if sr == settings.values["tts"]["sample_rate"] and audio.any() and len(audio) / sr < seconds:
            requests[idx] = req
            lines[idx].update(dub_script=script, tts_text=req["text"])
            if prosody and second:
                prosody["lines"][idx] = second["lines"][j]
    if prosody:
        prosody["applied_lines"] = sum(item["status"] == "applied" for item in prosody["lines"])


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
    source_info = probe(source, settings)
    duration = source_info["duration_ms"]
    opts = settings.values["tts"]
    rate = opts["sample_rate"]
    intonation = opts.get("intonation", {}).get("enabled", False)
    language, language_name = TARGET[manifest["direction"]]
    direction = manifest["direction"]
    lines = sorted(
        (row for row in translated_lines(run, manifest) if row["translation"] and row["end_ms"] > row["start_ms"]),
        key=lambda row: row["start_ms"],
    )
    if not lines and manifest["kind"] == "realtime" and asr is not None and not manifest.get("segmented_for_dub"):
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

    speaker_file = run / "speakers.json"
    speaker_data = read_json(speaker_file) if speaker_file.is_file() else {}
    annotations = speaker_data.get("lines", {}) if speaker_data.get("status") == "complete" else {}
    with TemporaryDirectory(dir=run) as temporary:
        work = Path(temporary)
        requests = []
        progress("正在分析原声语气")
        prep_rows = []
        scene_tasks: list[tuple[int, dict, Path]] = []
        need_scene = bool(source_info["width"] and settings.values["prompt_modes"].get("object_grounding", False))
        for index, row in enumerate(lines):
            reference = work / f"ref-{index}.wav"
            voice, reference_info = voice_reference(samples, source_rate, row, annotations, opts)
            write_wav(reference, voice, source_rate)
            observation = row.get("observation")
            translation = row.get("translation")
            sentence = work / f"sentence-{index}.wav"
            if intonation or not observation or not translation:
                write_wav(sentence, sentence_audio(samples, source_rate, row["start_ms"], row["end_ms"]), source_rate)
            scene_path = None
            if not translation and need_scene:
                scene_path = work / f"scene-{index}.mp4"
                scene_tasks.append((index, row, scene_path))
            prep_rows.append(
                {
                    "index": index,
                    "row": row,
                    "reference": reference,
                    "reference_info": reference_info,
                    "sentence": sentence,
                    "scene": scene_path,
                    "observation": observation,
                    "translation": translation,
                }
            )
        if scene_tasks:

            def _cut_scene(task: tuple[int, dict, Path]) -> None:
                _, item_row, silent = task
                ffmpeg(
                    settings,
                    [
                        "-ss",
                        str(item_row["start_ms"] / 1000),
                        "-i",
                        str(source),
                        "-t",
                        str((item_row["end_ms"] - item_row["start_ms"]) / 1000),
                        "-an",
                        *video_encode(),
                        str(silent),
                    ],
                )

            with ThreadPoolExecutor(max_workers=min(4, len(scene_tasks))) as pool:
                list(pool.map(_cut_scene, scene_tasks))

        # Batch all missing translations (observe + translate) and standalone observations across vLLM-Omni!
        translate_indices = [item["index"] for item in prep_rows if not item["translation"]]
        if translate_indices:
            batch_jobs = []
            for idx in translate_indices:
                item = prep_rows[idx]
                row = item["row"]
                media = {"audio": item["sentence"]}
                if item["scene"] is not None:
                    media["video"] = item["scene"]
                batch_jobs.append(
                    {
                        "item_id": f"{run.name}:dub:{row['line_id']}",
                        "mode": "perceive",
                        "condition": "T+V+A_sep" if "video" in media else "T+A",
                        "direction": direction,
                        "source": row["original"],
                        "context": row["context"],
                        "media": media,
                    }
                )
            batch_results = (
                translator.lines_batch(batch_jobs)
                if hasattr(translator, "lines_batch")
                else [translator.line(**job) for job in batch_jobs]
            )
            for idx, (output, _) in zip(translate_indices, batch_results, strict=True):
                prep_rows[idx]["translation"] = output["text"]
                prep_rows[idx]["observation"] = output["observation"]

        observe_only_indices = [item["index"] for item in prep_rows if item["translation"] and not item["observation"]]
        if observe_only_indices:
            if hasattr(translator, "infer_batch") and hasattr(translator, "_observe_request"):
                obs_items = [
                    (
                        translator._observe_request(
                            condition="T+A",
                            direction=direction,
                            source=prep_rows[idx]["row"]["original"],
                            audio=prep_rows[idx]["sentence"],
                            fps=translator.video["fps"],
                            max_pixels=translator.video["max_pixels"],
                        ),
                        f"{run.name}:dub:{prep_rows[idx]['row']['line_id']}",
                    )
                    for idx in observe_only_indices
                ]
                obs_preds = translator.infer_batch(obs_items)
                for idx, pred in zip(observe_only_indices, obs_preds, strict=True):
                    prep_rows[idx]["observation"] = translator._parse_observation(pred["text"])
            else:
                for idx in observe_only_indices:
                    item = prep_rows[idx]
                    row = item["row"]
                    obs, _ = translator.observe(
                        f"{run.name}:dub:{row['line_id']}",
                        condition="T+A",
                        direction=direction,
                        source=row["original"],
                        audio=item["sentence"],
                    )
                    item["observation"] = obs

        tts_texts = [
            spoken_draft(item["translation"], item["row"]["original"], language, settings) for item in prep_rows
        ]
        speed_range = tuple(opts.get("tts_speed_range", [0.80, 1.20]))
        for item, spoken_text in zip(prep_rows, tts_texts, strict=True):
            row = item["row"]
            row["translation"] = item["translation"]
            row["tts_text"] = spoken_text
            row["observation"] = item["observation"]
            slot_ms = max(300, row["end_ms"] - row["start_ms"])
            tts_speed = estimate_tts_speed(row["translation"], language, slot_ms, speed_range)
            requests.append(
                {
                    "text": spoken_text,
                    "instruction": voice_instructions(settings, language_name, item["observation"], tts_speed),
                    "reference": str(item["reference"]),
                    "reference_info": item["reference_info"],
                    "source_sentence": str(item["sentence"]) if intonation else None,
                    "output": str(work / f"voice-{item['index']}.wav"),
                    "speed": tts_speed,
                    "max_raw_sec": round(max(4.5, (slot_ms / 1000.0) * 1.65), 2),
                    "tone": item["observation"].get("tone"),
                    "strength": item["observation"].get("strength"),
                }
            )

        release_translation()
        progress("正在合成目标语言语音并匹配原声语调" if intonation else "正在合成目标语言语音")
        synthesize(settings, work, requests, run / "dubbing.log", on_synthesis_progress)
        evidence = work / "prosody.json"
        prosody = read_json(evidence) if evidence.is_file() else None
        shorten_overruns(
            settings,
            translator,
            lines,
            requests,
            work,
            run,
            direction,
            language,
            prosody,
            release_translation,
            progress,
        )
        progress("正在对齐语音并导出视频")
        min_speed = float(opts.get("min_speed", 0.78))
        max_speedup = float(opts["max_speedup"])
        fade_ms = int(opts.get("fade_ms", 18))
        duck_db = float(opts.get("background_duck_db", -28.0))
        parts, mask_parts, captions = [], [], []
        # Pre-load raw TTS segments & pre-run nominal atempo adjustments concurrently across CPU cores.
        loaded_voices: list[tuple[np.ndarray, float]] = []
        pre_atempo_tasks: list[tuple[int, str, float, Path]] = []
        for index, (row, request) in enumerate(zip(lines, requests, strict=True)):
            voice, voice_rate = read_wav(Path(request["output"]))
            if voice_rate != rate or not voice.any():
                raise RuntimeError("合成音频为空或采样率不匹配。")
            nom_start = round(row["start_ms"] * rate / 1000)
            nom_end = round(row["end_ms"] * rate / 1000)
            nom_target = max(1, nom_end - nom_start)
            next_start = lines[index + 1]["start_ms"] if index + 1 < len(lines) else duration
            nom_avail = max(1, round(max(row["end_ms"], next_start) * rate / 1000) - nom_start)
            nom_atempo = 1.0
            if len(voice) > nom_avail:
                nom_atempo = min(len(voice) / nom_avail, max_speedup)
            elif len(voice) > nom_target * 1.12:
                nom_atempo = min(len(voice) / (nom_target * 1.05), min(1.22, max_speedup))
            elif nom_target >= round(1.2 * rate) and len(voice) < nom_target * 0.90:
                nom_atempo = max(min_speed, min(1.0, len(voice) / (nom_target * 0.94)))
            loaded_voices.append((voice, nom_atempo))
            if abs(nom_atempo - 1.0) > 0.025:
                pre_atempo_tasks.append((index, request["output"], nom_atempo, work / f"aligned-{index}.wav"))

        pre_aligned: dict[int, tuple[np.ndarray, float]] = {}
        if pre_atempo_tasks:

            def _run_atempo(task: tuple[int, str, float, Path]) -> tuple[int, np.ndarray, float]:
                idx, src_path, factor, dst = task
                ffmpeg(settings, ["-i", src_path, "-af", f"atempo={factor:.6f}", str(dst)])
                wav_data, _ = read_wav(dst)
                return idx, wav_data, factor

            with ThreadPoolExecutor(max_workers=min(4, len(pre_atempo_tasks))) as pool:
                for idx, wav_data, factor in pool.map(_run_atempo, pre_atempo_tasks):
                    pre_aligned[idx] = (wav_data, factor)

        cursor = 0
        for index, (row, request) in enumerate(zip(lines, requests, strict=True)):
            raw_voice, _ = loaded_voices[index]
            raw_dur_ms = round(len(raw_voice) * 1000 / rate)
            orig_dur_ms = max(1, row["end_ms"] - row["start_ms"])
            slot_start = max(cursor, round(row["start_ms"] * rate / 1000))
            slot_end = round(row["end_ms"] * rate / 1000)
            slot_target = max(1, slot_end - slot_start)
            next_start = lines[index + 1]["start_ms"] if index + 1 < len(lines) else duration
            window_end = max(row["end_ms"], next_start)
            available = max(1, round(window_end * rate / 1000) - slot_start)

            # Bidirectional tempo alignment: speed up long clips and gently slow down overly short clips.
            atempo = 1.0
            if len(raw_voice) > available:
                atempo = min(len(raw_voice) / available, max_speedup)
            elif len(raw_voice) > slot_target * 1.12:
                atempo = min(len(raw_voice) / (slot_target * 1.05), min(1.22, max_speedup))
            elif slot_target >= round(1.2 * rate) and len(raw_voice) < slot_target * 0.90:
                atempo = max(min_speed, min(1.0, len(raw_voice) / (slot_target * 0.94)))

            if abs(atempo - 1.0) > 0.025:
                cached = pre_aligned.get(index)
                if cached is not None and abs(cached[1] - atempo) < 1e-4:
                    voice = cached[0]
                else:
                    aligned = work / f"aligned-{index}-seq.wav"
                    ffmpeg(settings, ["-i", request["output"], "-af", f"atempo={atempo:.6f}", str(aligned)])
                    voice, _ = read_wav(aligned)
            else:
                atempo = 1.0
                voice = raw_voice

            voice = _apply_fades_and_level(voice, rate, fade_ms)
            # If still noticeably shorter than speaker's mouth window, gently center it inside the window.
            place_start = slot_start
            residual = slot_target - len(voice)
            if residual > round(0.35 * rate):
                inward = min(round(0.35 * residual), round(0.55 * rate))
                if place_start + inward + len(voice) <= round(window_end * rate / 1000):
                    place_start += inward

            gap = place_start - cursor
            if gap > 0:
                parts.append(np.zeros(gap, dtype="<i2"))
                mask_parts.append(np.zeros(gap, dtype=bool))
            parts.append(voice)
            mask_parts.append(np.ones(len(voice), dtype=bool))
            cursor = place_start + len(voice)
            aligned_dur_ms = round(len(voice) * 1000 / rate)
            end_ms = round(cursor * 1000 / rate)
            captions.append(
                {
                    "line_id": row["line_id"],
                    "start_ms": round(place_start * 1000 / rate),
                    "end_ms": end_ms,
                    "text_asr": row["original"],
                    "multimodal": row["translation"],
                    "dub_script": row.get("dub_script", row["translation"]),
                    **({"first_tts_ms": row["first_tts_ms"]} if "first_tts_ms" in row else {}),
                    **({"tts_text": row["tts_text"]} if row.get("tts_text") != row["translation"] else {}),
                    "voice_reference": request["reference_info"],
                    **(
                        {"object_evidence": row["observation"]["object_evidence"]}
                        if row["observation"].get("object_evidence")
                        else {}
                    ),
                    "tone": request["tone"],
                    "strength": request["strength"],
                    "timing": {
                        "source_start_ms": row["start_ms"],
                        "source_end_ms": row["end_ms"],
                        "source_dur_ms": orig_dur_ms,
                        "raw_dur_ms": raw_dur_ms,
                        "aligned_dur_ms": aligned_dur_ms,
                        "tts_speed": request["speed"],
                        "atempo": round(atempo, 3),
                        "effective_speed": round(request["speed"] * atempo, 3),
                        "coverage": round(min(aligned_dur_ms, orig_dur_ms) / orig_dur_ms, 3),
                        "drift_end_ms": end_ms - row["end_ms"],
                    },
                    **({"asr_recheck": row["asr_recheck"]} if "asr_recheck" in row else {}),
                    **({"intonation": prosody["lines"][index]} if prosody else {}),
                }
            )
        tail_gap = max(0, round(duration * rate / 1000) - cursor)
        if tail_gap > 0:
            parts.append(np.zeros(tail_gap, dtype="<i2"))
            mask_parts.append(np.zeros(tail_gap, dtype=bool))
        audio = _blend_background(
            np.concatenate(parts),
            np.concatenate(mask_parts),
            samples,
            source_rate,
            rate,
            duck_db,
        )
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
        # Keep dubbed audio inside dub.mp4 (AAC) and drop redundant uncompressed dub.wav to save disk space.
        (run / "media/dub.wav").unlink(missing_ok=True)
        (run / "media/dub.webm").unlink(missing_ok=True)
        video.replace(run / "media/dub.mp4")
    coverages = [row["timing"]["coverage"] for row in captions]
    drifts = [abs(row["timing"]["drift_end_ms"]) for row in captions]
    timing_summary = {
        "median_coverage": round(float(median(coverages)), 3) if coverages else 1.0,
        "max_abs_drift_ms": max(drifts) if drifts else 0,
        "slowed_lines": sum(row["timing"]["effective_speed"] < 0.96 for row in captions),
        "sped_up_lines": sum(row["timing"]["effective_speed"] > 1.04 for row in captions),
        "total_lines": len(captions),
    }
    return {
        "status": "complete",
        "model": opts["model"],
        "language": language,
        "label": "AI 合成配音",
        "lines": captions,
        "timing": timing_summary,
        **({"prosody": {key: value for key, value in prosody.items() if key != "lines"}} if prosody else {}),
    }
