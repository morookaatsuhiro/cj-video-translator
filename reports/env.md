# 运行环境

- Python 3.12.3
- ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright (c) 2000-2021 the FFmpeg developers
- GPU（型号、显存、驱动、架构）NVIDIA GeForce RTX 4090, 49140 MiB, 580.105.08, 8.9
- 硬件 BF16 8.9 支持
- 当前可用的逻辑 CPU（nproc）25
- cgroup 内存上限（GiB）90
- 项目盘剩余 46.3 GiB
- github.com 可达（HTTP 200）
- huggingface.co 可达（HTTP 200）
- modelscope.cn 可达（HTTP 200）

## 本地模型配置

- faster-whisper `large-v3`，设备配置 `cuda`，revision `edaa852ec7e145841d8ffdb056a99866b5f0a478`，本地权重 `/root/autodl-tmp/dataset_create/.work/models/faster-whisper-large-v3`
- `local_omni` 模型 `cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit`，revision `b8f7bfd32ec3e7a59fa78cad0be54463eb89e9a9`
- 本地权重 `/root/autodl-tmp/dataset_create/.work/models/qwen3-omni-awq/b8f7bfd32ec3e7a59fa78cad0be54463eb89e9a9`
- vLLM-Omni Python `/root/autodl-tmp/dataset_create/.work/p3-omni-env/bin/python`，部署配置 `configs/local_omni_deploy.yaml`
- 可选 `qwen_omni` 配置 `Qwen/Qwen2.5-Omni-7B`，revision `尚未记录`

## 已安装版本

- cjvmt 0.1.0
- faster-whisper 1.2.1
- ctranslate2 4.9.1
- av 18.1.1
- numpy 2.5.5
- gradio 6.32.0
- fastapi 0.165.0
- ruff 0.17.6
- pytest 9.2.0
- fugashi 1.5.2

## 四条件实测

### 当前 RTX 4090 实例

2026-11-25，Whisper large-v3 使用 CUDA FP16，Qwen3-Omni AWQ 使用独立 vLLM-Omni 0.32.0 与 PyTorch 2.13.0 CUDA 13.0。BF16 矩阵计算通过。同一日语视频取两句运行五条件，前后各 10 次推理均未命中缓存，模型原始输出逐条相同。

| 部署 | 句级设备峰值显存 | 命令行运行证据 |
| --- | ---: | --- |
| 原配置，自动分配约 16.23 GiB KV 缓存 | 42,833.5 MiB | [manifest](../results/20261125-1357_auto_9966bc94/manifest.json) |
| 固定 2 GiB KV 缓存 | 28,369.5 MiB | [manifest](../results/20261125-1400_auto_e135591a/manifest.json) |

新配置仍使用 8,192 token 上下文和单序列。vLLM 日志报告 KV 缓存容量 21,840 token。当前实例另通过中文模拟直播两句和 WMT26 全模态 16 行文档的实测。 [实时结果](../results/20261125-1401_session_e4077ea0/manifest.json) · [文档验证](../results/20261125-1400_auto_e135591a/wmt_document_probe.json)

### 历史 RTX 5090 实例

- GPU NVIDIA GeForce RTX 5090, 32607 MiB, 595.71.05
- 模型 `cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit`，revision `b8f7bfd32ec3e7a59fa78cad0be54463eb89e9a9`。vLLM-Omni 0.32.0，PyTorch 2.13.0+cu130。实际验证了 BF16 矩阵乘法。
- 证据 `results/20261124-1532_zh2ja_9c65794c/predictions.jsonl`，首句片段 9.4 秒。以下为 NVML 设备已用显存峰值，包含模型和服务的常驻显存。四个条件均未命中缓存。

| 条件 | 推理耗时（毫秒） | 显存峰值（MiB） |
|---|---:|---:|
| `T_neutral` | 1626.453 | 27983.38 |
| `T+A` | 1483.51 | 27983.38 |
| `T+V` | 1769.256 | 28031.38 |
| `T+V+A` | 1786.306 | 28079.38 |

网页部署版将 faster-whisper large-v3 迁移到 GPU，实时识别使用单束解码。见 `reports/final_report.md` 中的浏览器会话结果。
