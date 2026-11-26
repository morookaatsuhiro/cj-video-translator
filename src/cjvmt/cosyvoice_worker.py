"""Run CosyVoice in its own Python runtime (supports both one-shot and persistent --daemon mode)."""

import hashlib
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path


def _run_job(model, request: Path, set_all_random_seed, torch, torchaudio, transfer_intonation, pitch_model) -> None:
    payload = json.loads(request.read_text(encoding="utf-8"))
    options = payload.get("intonation") or {}
    use_intonation = options.get("enabled", False) and transfer_intonation is not None
    pitch_rows = []
    set_all_random_seed(payload["seed"])
    for idx, line in enumerate(payload["lines"]):
        speed = max(0.5, min(2.0, float(line.get("speed", 1.0))))
        max_raw_sec = float(line.get("max_raw_sec") or 0)
        best_wave = None
        for attempt in range(3):
            if attempt > 0:
                set_all_random_seed(int(payload["seed"]) + idx * 31 + attempt * 97)
            chunks = [
                result["tts_speech"]
                for result in model.inference_instruct2(
                    line["text"],
                    line["instruction"] + "<|endofprompt|>",
                    line["reference"],
                    stream=False,
                    speed=min(2.0, speed * (1.0 + 0.08 * attempt)),
                    text_frontend=False,
                )
            ]
            if not chunks:
                raise RuntimeError("CosyVoice returned no audio.")
            candidate = torch.cat(chunks, dim=-1)
            if best_wave is None or candidate.shape[-1] < best_wave.shape[-1]:
                best_wave = candidate
            dur_s = candidate.shape[-1] / model.sample_rate
            if max_raw_sec <= 0 or dur_s <= max_raw_sec:
                break
            print(
                f"Retry line {idx + 1} attempt {attempt + 1}: dur={dur_s:.2f}s > limit={max_raw_sec:.2f}s",
                flush=True,
            )
        waveform = best_wave
        if use_intonation:
            source, source_rate = torchaudio.load(line["source_sentence"])
            waveform, details = transfer_intonation(source, source_rate, waveform, model.sample_rate, options)
            pitch_rows.append(details)
            print(f"Intonation {len(pitch_rows)}: {details}", flush=True)
        # Trim excessive leading/trailing dead silence (> 60 ms) for tighter lip-sync alignment.
        trim_db = float(payload.get("trim_silence_db", -42.0))
        pad_samples = round(model.sample_rate * 0.04)
        amp_floor = 10 ** (trim_db / 20.0)
        active = (waveform.abs().max(dim=0).values >= amp_floor).nonzero(as_tuple=False)
        if active.numel() > 0:
            lo = max(0, int(active[0].item()) - pad_samples)
            hi = min(waveform.shape[-1], int(active[-1].item()) + pad_samples + 1)
            if hi > lo:
                waveform = waveform[..., lo:hi]
        output = Path(line["output"])
        temporary = output.with_suffix(".tmp.wav")
        torchaudio.save(
            str(temporary),
            waveform.clamp(-1, 1),
            model.sample_rate,
            encoding="PCM_S",
            bits_per_sample=16,
        )
        temporary.replace(output)
    if use_intonation:
        request.with_name("prosody.json").write_text(
            json.dumps(
                {
                    **pitch_model,
                    "options": options,
                    "applied_lines": sum(row["status"] == "applied" for row in pitch_rows),
                    "total_lines": len(pitch_rows),
                    "lines": pitch_rows,
                }
            ),
            encoding="utf-8",
        )
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def main() -> None:
    checkout, model_dir = Path(sys.argv[1]), Path(sys.argv[2])
    daemon = len(sys.argv) >= 4 and sys.argv[3] == "--daemon"
    protocol = None
    if daemon:
        protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.path[:0] = [str(checkout), str(checkout / "third_party/Matcha-TTS")]

    import torch
    import torchaudio
    from cosyvoice.cli.cosyvoice import CosyVoice2
    from cosyvoice.utils.common import set_all_random_seed

    transfer_intonation = None
    pitch_model = None
    try:
        import torchcrepe
        from prosody import transfer_intonation as _transfer

        weights = Path(torchcrepe.__file__).with_name("assets") / "tiny.pth"
        pitch_model = {
            "model": "CREPE tiny",
            "package_version": version("torchcrepe"),
            "weights_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
            "vocoder": "WORLD",
            "vocoder_version": version("pyworld"),
        }
        transfer_intonation = _transfer
    except Exception as error:
        print(f"Intonation unavailable: {error}", flush=True)

    torch.set_num_threads(4)
    model = CosyVoice2(str(model_dir), fp16=torch.cuda.is_available())
    if not daemon:
        _run_job(model, Path(sys.argv[3]), set_all_random_seed, torch, torchaudio, transfer_intonation, pitch_model)
        return

    print(json.dumps({"ready": True}), file=protocol)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            job = json.loads(raw)
            _run_job(
                model,
                Path(job["request"]),
                set_all_random_seed,
                torch,
                torchaudio,
                transfer_intonation,
                pitch_model,
            )
            print(json.dumps({"ok": True}), file=protocol)
        except Exception as error:
            print(json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False), file=protocol)


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    main()
