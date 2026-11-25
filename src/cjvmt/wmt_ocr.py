"""Inspect rendered text with explicitly supplied local PaddleOCR weights."""

import os
import re
from importlib.metadata import version
from math import floor
from pathlib import Path
from tempfile import TemporaryDirectory

from cjvmt.config import Settings, file_hash
from cjvmt.media import ffmpeg, probe


class SubtitleAudit:
    def __init__(self, settings: Settings, detector: Path, recognizer: Path, frame_limit: int | None):
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        from paddleocr import PaddleOCR

        self.settings = settings
        self.options = settings.values["eval"]["onscreen"]
        self.frame_limit = frame_limit
        self.reader = PaddleOCR(
            text_detection_model_name="PP-OCRv5_server_det",
            text_detection_model_dir=str(detector),
            text_recognition_model_name="PP-OCRv5_server_rec",
            text_recognition_model_dir=str(recognizer),
            device="cpu",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
        )
        self.identity = {
            "detector_sha256": file_hash(detector / "inference.pdiparams"),
            "recognizer_sha256": file_hash(recognizer / "inference.pdiparams"),
            "package": version("paddleocr"),
            "frame_limit": frame_limit,
            "interval_sec": self.options["every_sec"],
        }

    def __call__(self, source: Path) -> dict:
        interval = self.options["every_sec"]
        metadata = probe(source, self.settings)
        duration = metadata["duration_ms"] / 1000
        if not duration:
            raise ValueError("OCR 媒体缺少有效时长。")
        last_frame = max(0, duration - 1 / max(metadata["fps"], 1))
        timestamps = [index * interval for index in range(floor(last_frame / interval) + 1)]
        if self.frame_limit and len(timestamps) > self.frame_limit:
            count = self.frame_limit
            timestamps = [timestamps[round(i * (len(timestamps) - 1) / max(1, count - 1))] for i in range(count)]
        frames = []
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for index, stamp in enumerate(timestamps):
                target = root / f"{index}.jpg"
                ffmpeg(self.settings, ["-ss", str(stamp), "-i", str(source), "-frames:v", "1", str(target)])
                if target.is_file():
                    frames.append((stamp, str(target)))
            if len(frames) != len(timestamps):
                raise ValueError(f"OCR 无法读取全部抽样帧：{len(frames)}/{len(timestamps)}。")
            texts = {path: (stamp, []) for stamp, path in frames}
            observed = set()
            for prediction in self.reader.predict(input=list(texts)):
                result = prediction.json["res"]
                path = str(Path(result["input_path"]).resolve())
                texts[path][1].extend(text for text in result["rec_texts"] if text.strip())
                observed.add(path)
            if observed != set(texts):
                raise ValueError("OCR 没有返回全部抽样帧。")
        output = [{"timestamp_sec": stamp, "text": recognized} for stamp, recognized in texts.values()]
        visible = [item for frame in output for item in frame["text"]]
        kana = re.compile(self.options["kana_regex"])
        chinese = [item for item in visible if re.search(r"[\u3400-\u9fff]", item) and not kana.search(item)]
        return {
            "frames": output,
            "japanese": any(kana.search(item) for item in visible),
            "chinese": bool(chinese),
            "chinese_question": any("？" in item or "?" in item for item in chinese),
        }
