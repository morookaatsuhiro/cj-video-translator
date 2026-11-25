"""Transfer phrase-level pitch from the original speech to a generated utterance."""

import numpy as np


def pitch_trend(f0: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    voiced = np.flatnonzero(np.isfinite(f0) & (f0 > 0))
    samples = np.arange(voiced[0], voiced[-1] + 1)
    semitones = 12 * np.log2(f0[voiced])
    filled = np.interp(samples, voiced, semitones)
    weights = np.hanning(window)
    padded = np.pad(filled, window // 2, mode="edge")
    trend = np.convolve(padded, weights / weights.sum(), mode="valid")
    return voiced, trend - np.median(trend[voiced - voiced[0]])


def transfer_f0(source_f0: np.ndarray, target_f0: np.ndarray, options: dict) -> tuple[np.ndarray, dict]:
    source_count, target_count = (int(np.count_nonzero(np.isfinite(f0) & (f0 > 0))) for f0 in (source_f0, target_f0))
    frame_ms = options["frame_ms"]
    details = {
        "source_voiced_ms": source_count * frame_ms,
        "target_voiced_ms": target_count * frame_ms,
    }
    if min(source_count, target_count) < 2 or min(details.values()) < options["min_voiced_ms"]:
        return target_f0, {"status": "insufficient_voicing", **details}
    window = max(3, round(options["smooth_ms"] / frame_ms) | 1)
    _, source_trend = pitch_trend(source_f0, window)
    voiced, target_trend = pitch_trend(target_f0, window)
    active = (voiced - voiced[0]) / (voiced[-1] - voiced[0])
    mapped = np.interp(active, np.linspace(0, 1, len(source_trend)), source_trend)
    limit = options["max_shift_semitones"]
    shift = np.clip(options["strength"] * (mapped - target_trend[voiced - voiced[0]]), -limit, limit)
    adjusted = target_f0.copy()
    adjusted[voiced] *= np.exp2(shift / 12)
    return adjusted, {"status": "applied", "max_shift_semitones": round(float(np.max(np.abs(shift))), 2), **details}


def transfer_intonation(source, source_rate: int, generated, target_rate: int, options: dict):
    import pyworld
    import torch
    import torchcrepe

    frame_ms = options["frame_ms"]
    samples = source.reshape(1, -1).cpu()
    if min(samples.numel() / source_rate, generated.numel() / target_rate) * 1000 < options["min_voiced_ms"]:
        return generated, {"status": "insufficient_voicing"}
    if source_rate != torchcrepe.SAMPLE_RATE:
        from torchaudio.functional import resample

        samples = resample(samples, source_rate, torchcrepe.SAMPLE_RATE)
        source_rate = torchcrepe.SAMPLE_RATE
    hop = round(source_rate * frame_ms / 1000)
    amplitude_floor = 10 ** (options["silence_db"] / 20)
    windows = torch.nn.functional.pad(samples, (hop, hop)).unfold(-1, 2 * hop, hop)
    audible = windows.square().mean(dim=-1) >= amplitude_floor**2
    if not audible.any():
        return generated, {"status": "insufficient_voicing", "source_voiced_ms": 0}
    floor, ceiling = options["min_f0_hz"], options["max_f0_hz"]
    f0, periodicity = torchcrepe.predict(
        samples,
        source_rate,
        hop_length=hop,
        fmin=floor,
        fmax=ceiling,
        model="tiny",
        return_periodicity=True,
        batch_size=256,
        device="cpu",
    )
    voiced = audible & (periodicity >= options["min_periodicity"]) & torch.isfinite(f0)
    source_f0 = torch.where(voiced, f0, 0).squeeze(0).numpy()
    if np.count_nonzero(source_f0) * frame_ms < options["min_voiced_ms"]:
        return generated, {"status": "insufficient_voicing", "source_voiced_ms": np.count_nonzero(source_f0) * frame_ms}
    original = np.ascontiguousarray(generated.reshape(-1).cpu().numpy(), dtype=np.float64)
    target_f0, times = pyworld.dio(original, target_rate, f0_floor=floor, f0_ceil=ceiling, frame_period=frame_ms)
    target_f0 = pyworld.stonemask(original, target_f0, times, target_rate)
    adjusted, details = transfer_f0(source_f0, target_f0, options)
    if details["status"] != "applied":
        return generated, details
    spectrum = pyworld.cheaptrick(original, target_f0, times, target_rate, f0_floor=floor)
    noise = pyworld.d4c(original, target_f0, times, target_rate, fft_size=2 * (spectrum.shape[1] - 1))
    output = pyworld.synthesize(adjusted, spectrum, noise, target_rate, frame_period=frame_ms)
    if not np.isfinite(output).all() or not output.any():
        return generated, {**details, "status": "resynthesis_unavailable"}
    aligned = original.copy()
    aligned[: len(output)] = output[: len(aligned)]
    return torch.as_tensor(aligned, dtype=generated.dtype).reshape_as(generated), details
