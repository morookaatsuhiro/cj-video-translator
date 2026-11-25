# 中日语音识别质量和 AutoDL 模型选型

## 当前运行

| 项目 | 实际配置 |
|---|---|
| GPU | NVIDIA GeForce RTX 4090，驱动报告 49,140 MiB 显存，计算能力 8.9，驱动 580.105.08 |
| CPU 和内存 | Intel Xeon Platinum 8470Q。容器配额相当于 25 个逻辑 CPU，内存上限 90 GiB |
| 磁盘 | `/root/autodl-tmp` 270 GiB，下载 Qwen 后余约 43 GiB。系统盘余约 22 GiB |
| 当前识别 | Whisper large-v3，1.55B 参数，CUDA FP16。本地 CTranslate2 权重 `model.bin` 为 3,087,284,237 字节，约 2.88 GiB |
| 识别运行时 | 主项目 Python 3.12 环境，faster-whisper 1.2.1、CTranslate2 4.9.1。离线 beam 5，实时首遍 beam 1 |
| 翻译与配音 | Qwen3-Omni-30B-A3B 的 AWQ 4-bit 权重处理字幕译文和音画观察。CosyVoice2-0.5B 生成译文配音。模型职责及文件路径见项目架构 |

当前识别目录为 `/root/autodl-tmp/dataset_create/.work/models/faster-whisper-large-v3`，权重版本为 `edaa852ec7e145841d8ffdb056a99866b5f0a478`。项目用 Silero 检测人声，离线提取词级时间，实时根据语音停顿切句。Whisper 官方 large 为 1,550M，turbo 为 809M。 [Whisper 模型规格](https://github.com/openai/whisper#available-models-and-languages) · [项目模型路径和显存实测](project_architecture.md)

空闲时保留 Whisper 的一次观察中，GPU 已用 4,157 MiB，驱动报告余 44,352 MiB。此前同机 Whisper 加翻译的单任务测试峰值为 28,369.5 MiB，本次网页测试在模型运行时采样到 27,592 MiB。权重文件大小只提供磁盘预算，运行还需要激活、计算工作区和 KV 缓存。新增模型先串行验证，联合运行时再测显存峰值。

## 上下文复听

1. Whisper 正常完成首遍识别，保存原始文本和时间。复核使用同一语段的音频。
2. 当前句置信度偏低或存在重复警告时，可参考前 30 秒内最近最多三句高置信度文本。用户在「高级设置 → 识别热词」输入的术语只用于本次任务。首遍缺少指定术语时，高置信度句也可进入复核。
3. 候选通过文本长度、相似度、数字和识别分数检查后才送去翻译。只有局部等长替换落在指定热词中时，才允许按术语条件采用候选。实时队列已积压时优先处理首遍识别。
4. 在「逐句详情」查看「初次识别」和「复核候选」。离线回放、实时字幕和新生成的配音可显示相应阶段的记录。历史会话继续用当时保存的识别结果。

配置见 [mvp.yaml](../configs/mvp.yaml)。识别及候选检查见 [asr.py](../src/cjvmt/asr.py)，离线入口见 [pipeline.py](../src/cjvmt/pipeline.py)，实时入口见 [realtime](../src/cjvmt/realtime/__init__.py)，整段配音复听见 [dubbing.py](../src/cjvmt/dubbing.py)。

| 本机公开样本 | 首遍与复核 | 预热后的单次复核耗时 |
|---|---|---:|
| 河南口音中文，约 4.28 秒，填写「河南省、南阳市、唐河县」 | 首遍为「奈阳市汤鹤县」，采用复核得到「南阳市、唐河县」。公开原片字幕可核对地名。6006 网页上传重现了这项修正 | 215 ms |
| 日语社区会议，12 秒原片段内的一个语段，填写「MCDC、メディアウィキ」 | 候选遗漏「皆さんこんにちは」，保留了包含问候的首遍转写 | 156 ms |

同一中文片段的 6006 实时运行完成了快速字幕和音画修订。实时首遍还多错了一个字，复核候选改动超过相似度限制，因此字幕沿用首遍，详情显示两份转写。该次识别和复核合计 543.6 ms，冷启动加载翻译模型耗时约 69.7 秒。 [线上实时会话](../results/20261125-1517_job_c9113f4c/session/session.jsonl)

这些样本验证了术语纠错和候选回退。表中耗时为 Whisper 已预热后的本地复核。更长的中文片段中，直接把热词放进首遍曾漏掉开场，故采用同音频二次复核。在相同话题中，前文的识别错误仍可能影响候选，口音、串话、低信噪比和很短的人名也会影响最终结果。 [中文公开字幕](https://commons.wikimedia.org/wiki/TimedText:WIKITONGUES-_Ying_speaking_Henan_Chinese.webm.en.srt) · [样本署名](demo_media.md) · [线上修正记录](../results/20261125-1514_zh2ja_9cd84568/lines.jsonl)

## 开源替代模型

| 候选 | 标称规模和单份权重磁盘预算 | 与本项目的匹配 |
|---|---|---|
| [Qwen3-ASR-1.7B](https://github.com/QwenLM/Qwen3-ASR) | 1.7B，Transformers 格式约 4.09 GB，Apache 2.0 | 已部署为可选离线复核。支持中文、日语及河南等多种中文方言，支持上下文和热词。采用现有 Whisper 时间戳，将来可以另行评估 Qwen3-ForcedAligner-0.6B |
| [Voxtral Mini 4B Realtime](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602) | 4B，一套 BF16 约 8.86 GB，Apache 2.0 | 逐帧流式中日转写。首次选型重点测端到端延迟、漏字和显存。仓库同时提供两种权重布局，安装选择运行时需要的一套 |
| [VibeVoice-ASR](https://github.com/microsoft/VibeVoice/blob/main/docs/vibevoice-asr.md) | 官方称 7B，Transformers 格式约 16.7 GB，MIT | 面向长录音，能输出说话人、时间和转写，支持自定义热点术语。官方报告含中文和日语样本。适合按需处理长视频或多人录制 |

Qwen 官方同一张多语 FLEURS 表中，Whisper large-v3 得分为 5.27，Qwen3-ASR-1.7B 为 4.90。该表覆盖包括中日语的 12 种语言，数字是整组的识别误差统计。Voxtral 官方表的 480 ms 缓冲下中日错误率为 10.45% 和 9.59%，2.4 秒时是 8.48% 和 5.50%。各表应按各自设置解读。 [Qwen 官方评测](https://github.com/QwenLM/Qwen3-ASR#evaluation) · [Voxtral 模型卡](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602) · [VibeVoice 权重](https://huggingface.co/microsoft/VibeVoice-ASR-HF/tree/main)

## 本机 Qwen 对照与部署

两个模型独立转写同一批 16 kHz 单声道音频。FLEURS 的中、日测试包各取文件顺序中的前 10 个不同提示词 ID，参照原始 TSV。WMT26 使用本机已有 6 段中文自然语音以及源语人工转写。先用 Unicode NFKC 统一全半角，去掉标点和空格，再以总编辑字数除以总参考字数。两边分别预热后单次识别，记录文件级延迟和推理进程的显存。

| 同一批样本 | Whisper 字符错误率 | Qwen3-ASR 字符错误率 | 预热后单段中位耗时 Whisper／Qwen |
|---|---:|---:|---:|
| WMT26 中文自然语音 6 段，578 字 | 24.39% | 8.82% | 973／1,212 ms |
| FLEURS 日语朗读 10 段，443 字 | 5.42% | 4.51% | 424／467 ms |
| FLEURS 中文朗读 10 段，367 字 | 7.08% | 9.54% | 403／396 ms |

河南样本的三个已知地名，Whisper 首遍命中 1 个，使用热词复核后命中 3 个。Qwen 首遍命中 3 个。分进程采样的峰值为 Whisper 4,404 MiB 和 Qwen 5,156 MiB。Qwen 的 WMT26 单段 P95 为 1,526 ms，Whisper 为 1,373 ms。抽样规模较小，全段独立转写的误差率也不能直接代表网页逐句复核后的误差率。 [本机对照数字与样本 ID](../reports/asr_benchmark.json) · [FLEURS 数据卡](https://huggingface.co/datasets/google/fleurs)

网页使用方式为「高级设置 → 处理方式 → 离线分析 · 四条件 → 用 Qwen3-ASR 再听一遍」。语音识别先用 Whisper 找句子和时间戳，再由 Qwen 复听相同时间段。候选有明显增删、差异过大或数字变化时沿用首遍。回放可查采用数和两份转写。推理进程在复核结束后释放 GPU，再进入 Qwen3-Omni 翻译。实时处理沿用速度优先的 Whisper 路径。 [Qwen 调用和回退](../src/cjvmt/qwen_asr.py) · [独立推理进程](../src/cjvmt/qwen_asr_worker.py)

6006 上用 Chromium 上传同一段河南口音视频，勾选 Qwen 复核，热词留空。页面显示「Qwen3-ASR 修正 1/1 句」，四份 SRT 均包含「南阳市唐河县」。此次复核阶段约 8.38 秒，计入了独立进程启动和模型加载。 [网页运行记录](../results/20261125-1530_zh2ja_c7920030/manifest.json) · [逐句证据](../results/20261125-1530_zh2ja_c7920030/lines.jsonl)

本机模型目录为 `/root/autodl-tmp/dataset_create/.work/models/qwen3-asr-1.7b-hf/bcd2b5b7f32b480ab5790554cfa8347f246a14f3`。`model.safetensors` 为 4,076,193,080 字节，SHA-256 为 `2db53c7d81bd9b8cbc6a074e89be2c968a0d373fb4ee68bb1b1e14f7042dfee1`。独立环境使用 PyTorch 2.13.0、Transformers 5.19.0、Accelerate 1.12.0，BF16 推理。迁移时用 `CJVMT_QWEN_ASR_PATH` 和 `CJVMT_QWEN_ASR_PYTHON` 指定权重、解释器。 [Transformers 上下文接口](https://huggingface.co/docs/transformers/v5.19.0/model_doc/qwen3_asr#context--hotwords) · [官方权重](https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf/tree/bcd2b5b7f32b480ab5790554cfa8347f246a14f3)
