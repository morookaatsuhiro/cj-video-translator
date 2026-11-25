from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from cjvmt.config import Settings
from cjvmt.store import read_json


def main(run: Path) -> None:
    settings = Settings.load()
    sizes = settings.values["figures"]["size_px"]
    colors = settings.values["figures"]["colors"]
    font = settings.values["figures"]["font"]
    font_manager.findfont(font, fallback_to_default=False)
    plt.rcParams["font.family"] = font
    dest = settings.path("reports") / "figures"
    dest.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(sizes[0] / 100, sizes[1] / 100))
    figure.patch.set_facecolor("#f4f2ec")
    axis.axis("off")
    columns = [
        (0.08, "视频输入\n声画滚动缓存"),
        (0.29, "Silero 停顿检测\nWhisper 语音识别"),
        (0.52, "快速译文\n仅输入文字"),
        (0.77, "音画修订\n比较差异与语气"),
    ]
    for xx, label in columns:
        axis.text(
            xx,
            0.55,
            label,
            transform=axis.transAxes,
            fontsize=22,
            ha="center",
            va="center",
            bbox={"boxstyle": "round,pad=.7", "fc": "#e4eee1", "ec": "#849b89"},
        )
    for left, right in zip(columns, columns[1:]):
        axis.annotate(
            "",
            xy=(right[0] - 0.103, 0.55),
            xytext=(left[0] + 0.105, 0.55),
            xycoords="axes fraction",
            arrowprops={"arrowstyle": "->", "color": "#557260", "lw": 2},
        )
    axis.set_title("中日多模态翻译 · 句级实时流程", fontsize=31, pad=22)
    axis.text(
        0.5,
        0.25,
        "同一全模态模型逐步接收文字、声音和画面。会话导出两阶段译文与双语字幕。",
        transform=axis.transAxes,
        ha="center",
        fontsize=18,
        color="#55675b",
    )
    figure.savefig(dest / "system_overview.png", dpi=100)
    plt.close(figure)

    manifest = read_json(run / "manifest.json")
    data = manifest["summary"]
    keys = ("asr", "quick", "multimodal")
    figure, axis = plt.subplots(figsize=(sizes[0] / 100, sizes[1] / 100))
    figure.patch.set_facecolor("#f4f2ec")
    axis.set_facecolor("#fffcf6")
    axis.bar([-0.19, 0.81, 1.81], [data[k]["median_ms"] / 1000 for k in keys], 0.37, label="中位数", color=colors[0])
    axis.bar([0.19, 1.19, 2.19], [data[k]["p90_ms"] / 1000 for k in keys], 0.37, label="90 分位数", color=colors[1])
    axis.set_xticks(range(3), ["原文", "快速译文", "音画修订"])
    axis.tick_params(labelsize=15)
    axis.set_ylabel("句末至字幕推出 / 秒", fontsize=16)
    axis.grid(axis="y", alpha=0.18)
    axis.set_title("模拟直播会话 · 字幕推出延迟", fontsize=29, pad=14)
    axis.legend(fontsize=13)
    figure.text(
        0.12,
        0.035,
        f"运行编号 {run.name} · RTX 5090 · Qwen3-Omni AWQ · faster-whisper large-v3",
        color="#55675b",
        fontsize=12,
    )
    figure.subplots_adjust(bottom=0.12, top=0.86)
    figure.savefig(dest / "realtime_latency.png", dpi=100)
    plt.close(figure)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
