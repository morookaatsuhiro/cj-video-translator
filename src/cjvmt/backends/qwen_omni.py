from __future__ import annotations

import os
from pathlib import Path

from cjvmt.backends import Request
from cjvmt.config import Settings


class QwenOmni:
    def __init__(self, settings: Settings):
        from huggingface_hub import snapshot_download

        self.options = settings.values["models"]["omni"]
        local = os.getenv("CJVMT_OMNI_PATH")
        revision = os.getenv("CJVMT_OMNI_REVISION") or self.options["revision"]
        self.path = (
            Path(local).expanduser()
            if local
            else Path(
                snapshot_download(
                    self.options["id"],
                    revision=revision,
                    local_files_only=True,
                )
            )
        )
        if not (self.path / "config.json").exists():
            raise ValueError("找不到本地 Qwen2.5-Omni 权重。请设置 CJVMT_OMNI_PATH。")
        self.identity = {
            "backend": "qwen_omni",
            "model": self.options["id"],
            "revision": revision or self.path.name,
            "dtype": self.options["dtype"],
            "attention": self.options["attn_implementation"],
        }
        self.model = None

    def warm(self) -> None:
        if self.model is not None:
            return

        import torch
        from transformers import Qwen2_5OmniForConditionalGeneration, Qwen2_5OmniProcessor

        if not torch.cuda.is_available():
            raise RuntimeError("Qwen2.5-Omni 需要可用的 CUDA GPU。")
        self.processor = Qwen2_5OmniProcessor.from_pretrained(self.path, local_files_only=True)
        self.model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
            self.path,
            dtype=getattr(torch, self.options["dtype"]),
            device_map="cuda:0",
            enable_audio_output=False,
            attn_implementation=self.options["attn_implementation"],
            local_files_only=True,
        ).eval()

    def generate(self, request: Request) -> dict:
        import torch
        from qwen_omni_utils import process_mm_info

        self.warm()
        content = []
        if request.video:
            content.append(
                {"type": "video", "video": str(request.video), "fps": request.fps, "max_pixels": request.max_pixels}
            )
        elif request.frames:
            content.append(
                {
                    "type": "video",
                    "video": [str(path) for path in request.frames],
                    "sample_fps": request.fps,
                    "max_pixels": request.max_pixels,
                }
            )
        if request.audio:
            content.append({"type": "audio", "audio": str(request.audio)})
        content.append({"type": "text", "text": request.user})
        messages = [
            {"role": "system", "content": [{"type": "text", "text": request.system}]},
            {"role": "user", "content": content},
        ]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        audio, images, video, video_kwargs = process_mm_info(
            messages, use_audio_in_video=request.interleaved, return_video_kwargs=True
        )
        # Qwen2.5-Omni expects a scalar FPS. Each request contains at most one video.
        processor_kwargs = {"fps": video_kwargs["fps"][0]} if video is not None else {}
        batch = self.processor(
            text=text,
            audio=audio,
            images=images,
            videos=video,
            return_tensors="pt",
            padding=True,
            use_audio_in_video=request.interleaved,
            **processor_kwargs,
        ).to(self.model.device)
        batch = batch.to(self.model.dtype)
        torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode():
            result = self.model.generate(
                **batch,
                use_audio_in_video=request.interleaved,
                return_audio=False,
                thinker_max_new_tokens=request.max_tokens,
                thinker_do_sample=False,
            )
        sequences = result.sequences if hasattr(result, "sequences") else result
        generated = sequences[:, batch.input_ids.shape[1] :]
        text = self.processor.batch_decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
        return {
            "text": text,
            "output_tokens": generated.shape[-1],
            "peak_vram_mib": round(torch.cuda.max_memory_allocated() / 2**20, 2),
        }
