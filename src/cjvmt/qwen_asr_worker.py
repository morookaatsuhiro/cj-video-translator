"""Run the optional Qwen transcriber in its own CUDA environment."""

import json
import math
import sys
from pathlib import Path


def main(model_path: str, request: Path, output: Path) -> None:
    import soundfile
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    data = json.loads(request.read_text(encoding="utf-8"))
    audio, rate = soundfile.read(data["audio"], dtype="float32")
    if rate != 16000 or audio.ndim != 1:
        raise ValueError("Qwen ASR expects mono PCM at 16 kHz")
    torch.set_num_threads(4)
    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForMultimodalLM.from_pretrained(
        model_path, local_files_only=True, device_map="cuda:0", dtype=torch.bfloat16
    ).eval()
    lines = []
    with torch.inference_mode():
        for line in data["lines"]:
            samples = audio[round(line["start_ms"] * rate / 1000) : round(line["end_ms"] * rate / 1000)]
            inputs = processor.apply_transcription_request(
                audio=samples, language=data["language"], prompt=line["prompt"]
            ).to(model.device, model.dtype)
            generated = model.generate(
                **inputs, max_new_tokens=max(96, math.ceil(len(samples) / rate * 16)), do_sample=False
            )
            text = processor.decode(generated[:, inputs["input_ids"].shape[1] :], return_format="transcription_only")[0]
            lines.append(text.strip())
    output.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
