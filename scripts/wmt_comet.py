"""Score local WMT documents with an existing offline COMET checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from importlib.metadata import version
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def score(run: Path, checkpoint: Path, encoder: Path, device: str) -> Path:
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1", DO_NOT_TRACK="1")
    from comet.models import RegressionMetric

    class LocalRegressionMetric(RegressionMetric):
        def on_load_checkpoint(self, saved):
            super().on_load_checkpoint(saved)
            key = "encoder.model.embeddings.position_ids"
            embeddings = self.encoder.model.embeddings
            state = saved["state_dict"]
            if key in state and "position_ids" not in embeddings.state_dict():
                if not state[key].equal(embeddings.position_ids):
                    raise ValueError("COMET 位置索引与本地编码器不一致。")
                state.pop(key)

    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "complete" or manifest["model"]["backend"] == "mock":
        raise ValueError("COMET 需要已完成的真实 WMT 运行。")
    metric = LocalRegressionMetric.load_from_checkpoint(
        checkpoint_path=checkpoint,
        hparams_file=checkpoint.parents[1] / "hparams.yaml",
        map_location="cpu",
        strict=True,
        weights_only=True,
        pretrained_model=str(encoder),
        local_files_only=True,
        load_pretrained_weights=False,
    )
    documents = run / "documents.jsonl"
    rows = [json.loads(line) for line in documents.read_text(encoding="utf-8").splitlines()]
    from statistics import mean

    batches = []
    for row in rows:
        if manifest["dataset"] == "wmt26" and row["condition"] != "T_asr":
            extra = [
                dict(src=src, mt=mt, ref=ref)
                for src, mt, ref in zip(
                    row["source_text_lines"], row["translation_lines"], row["reference_lines"], strict=True
                )
            ]
        else:
            extra = [{"src": row["source"], "mt": row["text"], "ref": row["reference"]}]
        batches.append(extra)
    result = metric.predict(
        [entry for batch in batches for entry in batch], gpus=int(device == "cuda"), progress_bar=False, num_workers=0
    )
    scores = []
    start = 0
    for row, batch in zip(rows, batches, strict=True):
        end = start + len(batch)
        values = result.scores[start:end]
        scores.append(
            {"doc_id": row["doc_id"], "condition": row["condition"], "comet": mean(values), "line_scores": values}
        )
        start = end
    if start != len(result.scores):
        raise ValueError("COMET 分数数量与输入不符。")
    evidence = {
        "model": "Unbabel/wmt22-comet-da",
        "checkpoint_sha256": digest(checkpoint),
        "checkpoint_revision": checkpoint.parents[1].name,
        "encoder_revision": encoder.name,
        "documents_sha256": digest(documents),
        "device": device,
        "package": version("unbabel-comet"),
        "scores": scores,
    }
    path = run / "comet.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    arguments = parser.parse_args()
    print(score(arguments.run, arguments.checkpoint, arguments.encoder, arguments.device))
