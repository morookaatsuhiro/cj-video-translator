"""Run CosyVoice in its own Python runtime."""

import hashlib
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path


def main() -> None:
    checkout, model_dir, request = map(Path, sys.argv[1:])
    sys.path[:0] = [str(checkout), str(checkout / "third_party/Matcha-TTS")]

    import torch
    import torchaudio
    from cosyvoice.cli.cosyvoice import CosyVoice2
    from cosyvoice.utils.common import set_all_random_seed

    payload = json.loads(request.read_text(encoding="utf-8"))
    options = payload.get("intonation") or {}
    use_intonation = options.get("enabled", False)
    if use_intonation:
        import torchcrepe
        from prosody import transfer_intonation

        weights = Path(torchcrepe.__file__).with_name("assets") / "tiny.pth"
        pitch_model = {
            "model": "CREPE tiny",
            "package_version": version("torchcrepe"),
            "weights_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
            "vocoder": "WORLD",
            "vocoder_version": version("pyworld"),
            "options": options,
        }
    pitch_rows = []
    torch.set_num_threads(4)
    model = CosyVoice2(str(model_dir), fp16=torch.cuda.is_available())
    set_all_random_seed(payload["seed"])
    for line in payload["lines"]:
        chunks = [
            result["tts_speech"]
            for result in model.inference_instruct2(
                line["text"],
                line["instruction"] + "<|endofprompt|>",
                line["reference"],
                stream=False,
                text_frontend=False,
            )
        ]
        if not chunks:
            raise RuntimeError("CosyVoice returned no audio.")
        waveform = torch.cat(chunks, dim=-1)
        if use_intonation:
            source, source_rate = torchaudio.load(line["source_sentence"])
            waveform, details = transfer_intonation(source, source_rate, waveform, model.sample_rate, options)
            pitch_rows.append(details)
            print(f"Intonation {len(pitch_rows)}: {details}", flush=True)
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
                    "applied_lines": sum(row["status"] == "applied" for row in pitch_rows),
                    "total_lines": len(pitch_rows),
                    "lines": pitch_rows,
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    main()
