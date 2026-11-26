"""Subprocess entry point for an existing vLLM-Omni installation (supports single & batched requests)."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread


def ffmpeg(path: str, *args: str) -> bytes:
    command = [os.getenv("CJVMT_FFMPEG", "ffmpeg"), "-nostdin", "-v", "error", "-i", path, *args, "pipe:1"]
    return subprocess.run(command, capture_output=True, check=True, timeout=120).stdout


def payload(request: dict) -> dict:
    import numpy as np
    from PIL import Image

    media = {}
    options = {}
    slots = []
    fps = request["fps"]
    pixels = request["max_pixels"]
    if request["video"] or request["frames"]:
        if request["video"]:
            import io

            raw = ffmpeg(request["video"], "-vf", f"fps={fps:g}", "-f", "image2pipe", "-c:v", "mjpeg")
            frames = [
                Image.open(io.BytesIO(frame + b"\xff\xd9")).convert("RGB") for frame in raw.split(b"\xff\xd9") if frame
            ]
            if not frames:
                raw = ffmpeg(request["video"], "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg")
                frames = [Image.open(io.BytesIO(raw)).convert("RGB")]
        else:
            frames = []
            for path in request["frames"]:
                with Image.open(path) as frame:
                    frames.append(frame.convert("RGB"))
        width, height = frames[0].size
        scale = min(1, (pixels / (width * height)) ** 0.5)
        size = (max(2, int(width * scale)), max(2, int(height * scale)))
        media["video"] = np.stack([np.asarray(frame.resize(size, Image.Resampling.BILINEAR)) for frame in frames])
        options["fps"] = fps
        slots.append("<|vision_start|><|video_pad|><|vision_end|>")
    audio = request["video"] if request["interleaved"] else request["audio"]
    if audio:
        raw = ffmpeg(audio, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le")
        media["audio"] = (np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768, 16000)
        if request["interleaved"]:
            options["use_audio_in_video"] = True
        else:
            slots.insert(0, "<|audio_start|><|audio_pad|><|audio_end|>")
    result = {
        "prompt": (
            f"<|im_start|>system\n{request['system']}<|im_end|>\n"
            f"<|im_start|>user\n{''.join(slots)}{request['user']}<|im_end|>\n<|im_start|>assistant\n"
        ),
        "modalities": ["text"],
    }
    if media:
        result["multi_modal_data"] = media
        result["mm_processor_kwargs"] = options
    return result


def main() -> None:
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        VLLM_NO_USAGE_STATS="1",
        DO_NOT_TRACK="1",
        VLLM_USE_FLASHINFER_SAMPLER="0",
    )
    from vllm import SamplingParams
    from vllm_omni.entrypoints.omni import Omni

    cache, model, deploy, seed = sys.argv[1:]
    Path(cache).mkdir(parents=True, exist_ok=True)
    os.environ["VLLM_CACHE_ROOT"] = cache
    import pynvml

    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    engine = Omni(model=model, deploy_config=deploy, init_timeout=600)
    prep_pool = ThreadPoolExecutor(max_workers=4)
    print(json.dumps({"ready": True}), file=protocol)
    try:
        for line in sys.stdin:
            done = Event()
            peaks = [pynvml.nvmlDeviceGetMemoryInfo(handle).used]

            def measure() -> None:
                while not done.wait(0.05):
                    peaks.append(pynvml.nvmlDeviceGetMemoryInfo(handle).used)

            monitor = Thread(target=measure, daemon=True)
            monitor.start()
            try:
                raw_msg = json.loads(line)
                if "batch" in raw_msg:
                    reqs = raw_msg["batch"]
                    payloads = list(prep_pool.map(payload, reqs))
                    max_tok = max(int(r["max_tokens"]) for r in reqs)
                    params = SamplingParams(temperature=0, max_tokens=max_tok, seed=int(seed))
                    results = engine.generate(payloads, [params], py_generator=False)
                    ordered: list[dict | None] = [None] * len(reqs)
                    for item in results:
                        if item.final_output_type != "text":
                            continue
                        idx = int(str(item.request_id).split("_", 1)[0])
                        out = item.outputs[0]
                        ordered[idx] = {
                            "text": out.text,
                            "output_tokens": len(out.token_ids),
                        }
                    peaks.append(pynvml.nvmlDeviceGetMemoryInfo(handle).used)
                    peak_mib = round(max(peaks) / 2**20, 2)
                    if any(entry is None for entry in ordered):
                        raise RuntimeError("批处理推理返回条目不完整。")
                    message = {
                        "batch": [
                            {**entry, "peak_vram_mib": peak_mib, "vram_measurement": "NVML device used"}
                            for entry in ordered
                        ]
                    }
                else:
                    request = raw_msg
                    params = SamplingParams(temperature=0, max_tokens=request["max_tokens"], seed=int(seed))
                    results = engine.generate([payload(request)], [params], py_generator=False)
                    result = next(item.outputs[0] for item in results if item.final_output_type == "text")
                    peaks.append(pynvml.nvmlDeviceGetMemoryInfo(handle).used)
                    message = {
                        "text": result.text,
                        "output_tokens": len(result.token_ids),
                        "peak_vram_mib": round(max(peaks) / 2**20, 2),
                        "vram_measurement": "NVML device used",
                    }
            except Exception as error:
                message = {"error": f"{type(error).__name__}: {error}"}
            finally:
                done.set()
                monitor.join()
            print(json.dumps(message, ensure_ascii=False), file=protocol)
    finally:
        prep_pool.shutdown(wait=False)
        engine.close()
        pynvml.nvmlShutdown()


if __name__ == "__main__":
    main()
