# CJ-VMT · 中日多模态视频翻译

结课系统原型。可上传日语或中文视频生成双语字幕，也可按原速模拟直播，或直接使用摄像头和麦克风。网页展示原文、快速译文和音画修订，回放页可逐句比较模态条件、展开模型观察和下载字幕。演示样本量有限，数据集建设未开展。

代码路径图、模型权重、执行流程和本机验收结果见 [项目架构与 AutoDL 部署复核](docs/project_architecture.md)。

## AutoDL 上打开网页

服务器内运行以下命令即可启动交互页。当前 AutoDL 实例使用独立进程加载本机缓存的 Qwen3-Omni 量化权重，使用 GPU 上的 Whisper 做语音识别。

```bash
cd /root/autodl-tmp/project/demo/cj-vmt
scripts/start_autodl.sh
```

从自己的电脑访问时，在 AutoDL 提供的 SSH 登录命令上增加 `-L 6006:127.0.0.1:6006`，再在浏览器打开 `http://127.0.0.1:6006/`。打开「实时翻译」后选择演示片段或上传视频。首次推理包括模型初始化，后续任务复用同一模型进程。

网页默认用 Whisper 识别中文或日语，并为整次会话选择语向。短句、混合语言或噪声较大的素材可在「高级设置」指定语向。

在「高级设置」的「识别热词」可填写本次视频中的人名、地名和术语，用逗号分隔。系统用可信前文和热词复听有疑问的句子，逐句详情提供首遍转写、复核候选和采用结果。选择「离线分析 · 四条件」时，已安装 Qwen3-ASR 的服务器可勾选「用 Qwen3-ASR 再听一遍」。 [ASR 实测与模型选型](docs/asr_quality.md)

上传和结束录像后，网页顶部会显示当前处理步骤。上传、分句翻译和配音显示本步骤的完成比例，模型加载、媒体转换和导出显示等待时长。服务器仍有响应但本步骤长时间没有新进度时，进度条会提示继续等待。连接暂时中断时，网页会重试读取任务状态。

翻译模式选「自动」时，实时字幕使用引导模式，离线四条件实验使用对比模式。需要查看模型的动作、语调和语气观察时，可在「高级设置」选择「先感知后翻译」。会话结束后展开「会话延迟」，可查看切句、排队和模型调用耗时。新会话的回放可切换并下载快速译文或最终字幕。

使用摄像头时，选择「摄像头」，点击「开始翻译」并允许浏览器使用摄像头和麦克风。点击「结束录制」后可播放带双语字幕的录像或下载 SRT。支持连续录制的浏览器使用原始录制音画回放。语音和画面只发送至当前 AutoDL 实例，会话保存在项目的 `results/` 中。

### 人物和语气标注

打开任意已有原声和字幕的真实会话，在「结果回放」点击「分析人物和语气」。系统按本视频的声纹标注「说话人 1、2…」，有足够口型证据时给出画面位置。原声回放的字幕和逐句详情会显示人物、语调、语速及用语风格。模型语气可关闭，也可在声纹分析完成后补做。「下载人物标注」导出 JSON。 [处理流程、模型路径和样本测试](docs/speaker_analysis.md)

### 中日配音

录制或上传本人或已获说话人许可的视频时，勾选「生成 AI 配音」并确认授权。字幕生成之后，系统用原声参考和逐句语气生成另一种语言的配音。实时会话在录制结束后会重新识别整段原声。已完成的自录或上传会话可在「结果回放」确认授权并直接生成配音。生成完成后，回放默认选择配音版，可切回原声或下载配音 MP4。下载视频保留原画面，带「AI 合成配音」标识、默认译制音轨和备用原声音轨。语气由模型估计，画面口型维持原样。

配音使用独立 Python 3.10 环境和本机 CosyVoice2-0.5B。当前 AutoDL 部署从 `.cache/cosyvoice-env`、`.cache/CosyVoice` 和 `.cache/cosyvoice-model` 加载；换机器时可按 `.env.example` 指定路径。模型缓存和个人配音结果留在部署机器，不进入 Git 或课堂离线包。

新生成的配音会用 **CREPE tiny** 在本机测量每句原声的基频，再用 **WORLD** 让译文音频跟随原句平稳、上扬或下落的整体走势。回放会显示语调实际应用了几句。可用基频不足的句子沿用基础配音。此前生成的配音可继续在原会话回放，新语调功能适用于新生成的配音。 [模型路径与参数](docs/tone_preservation.md#当前部署的句级语调迁移)

## 安装和离线使用

需要 Python 3.11 或更高版本、FFmpeg 和 ffprobe。在 Ubuntu 上用 `apt-get install ffmpeg fonts-noto-cjk` 安装媒体工具和绘图字体。

```bash
make setup
make check
make smoke
make demo-html
make demo-package
make demo
```

直接用浏览器打开 `reports/html/index.html`，也可解压 `reports/html/cj-vmt-offline.zip` 后打开 `index.html`。选择字幕条件会同时更新播放器中的双语字幕和 SRT 下载。离线首页的「课程材料」可打开报告、图表和演示录屏。回放和 Mock 冒烟均可在 CPU 上运行，Mock 结果有明确标识。

## 真实推理

本机 `.env` 指向已有的 Qwen3-Omni AWQ、vLLM-Omni 运行环境和 Whisper 模型。当前 RTX 4090 实例已验证直接从 CLI 使用 CUDA Whisper。若迁移后遇到 CUDA 12 动态库问题，可按 `scripts/start_autodl.sh` 配置库路径，或用 `CJVMT_ASR_DEVICE=cpu` 将 Whisper 转到 CPU。Web 和独立 CLI 会分别启动推理进程，单卡上应逐个执行 GPU 作业。其他机器可以参考 `.env.example` 指定自己的本地权重。使用官方 Qwen2.5-Omni 时，在支持 CUDA 的独立环境安装 `.[omni]`，填写 `CJVMT_OMNI_PATH` 和 `CJVMT_OMNI_REVISION`，在 `configs/mvp.yaml` 选用与实际权重一致的模型 ID，并指定 `--backend qwen_omni`。模型默认从本地读取。

```bash
.venv/bin/cjvmt doctor
.venv/bin/cjvmt run --input /path/to/video.mp4 --direction ja2zh --backend local_omni \
  --conditions T_neutral,T_asr,T+A,T+V,T+V+A --mode compare
.venv/bin/cjvmt validate --run results/your_run_id
.venv/bin/cjvmt realtime --input file_sim --source /path/to/video.mp4 \
  --direction zh2ja --backend local_omni --mode perceive
```

公开演示源和许可见 [演示媒体署名](docs/demo_media.md)。自录视频可放在 `data/raw/own/`，录制和授权要求见 [HUMAN_TASKS.md](docs/HUMAN_TASKS.md)。WMT25 日→中和 WMT26 中→日均已完成文档级 chrF、COMET 及配套报告。WMT 视频只在本机用于研究与评测，不进入课堂分发包。

### 分步复现

可单独检查音轨、转写、切句和翻译。运行 `media` 后，将其 JSON 输出中的 `duration_ms` 用于 `segment`。这里以日→中为例。

```bash
cjvmt media --input /path/to/video.mp4 --output data/processed/lesson/media
cjvmt asr --input data/processed/lesson/media/audio.wav --backend local_omni \
  --direction ja2zh --output data/processed/lesson/asr.jsonl
cjvmt segment --input data/processed/lesson/asr.jsonl --duration-ms "$DURATION_MS" \
  --direction ja2zh --output data/processed/lesson/segments.jsonl
cjvmt clip --input data/processed/lesson/segments.jsonl --media data/processed/lesson/media \
  --output data/processed/lesson/clips
cjvmt translate --input data/processed/lesson/clips/clips.jsonl --backend local_omni \
  --direction ja2zh > data/processed/lesson/translations.jsonl
```

使用 `cjvmt run` 可另外生成运行记录、SRT/VTT 和回放页。

## WMT 评测

- [WMT25 文档级结果](reports/eval_wmt25.md)
- [WMT26 中→日正式评测](reports/eval_wmt26.md)

已有发布文件时，用 `cjvmt wmt prepare --set wmt25 --source <JSONL> --media <ZIP>` 建立准备记录，再用 `cjvmt wmt run --set wmt25 --prepared <运行目录>` 运行。已有本地 COMET 检查点和编码器时，用 COMET 环境运行 `scripts/wmt_comet.py`，再用 `cjvmt stats --input <运行目录>` 刷新报告。

### WMT26 媒体评测

当前 AutoDL 实例已下载并校验 WMT26 `multimodal_inputs.zip`，保存在 `data/external/wmt26/`。换机器时按项目的下载预算确认，并用[数据卡](docs/data_cards/wmt26.md)中的 SHA-256 校验。正式运行记录为 `results/20261125-0247_wmt26-eval_bb2b92a1/`，覆盖 114 段、7 个条件，回退为 0。准备、审计和试跑命令如下。OCR 命令使用安装了 PaddleOCR 的 Python 环境。

```bash
cjvmt wmt prepare --set wmt26 --media /path/to/multimodal_inputs.zip
cjvmt wmt audit --set wmt26 --prepared results/PREPARE_RUN \
  --detector /path/to/PP-OCRv5_server_det --recognizer /path/to/PP-OCRv5_server_rec
cjvmt wmt run --set wmt26 --prepared results/PREPARE_RUN \
  --audit results/PREPARE_RUN/ocr.json --limit 3
```

去掉 `--limit` 可评测全量。`--mismatch` 启用声音和画面错配对照。恢复中断的运行时，保留原参数并加 `--resume results/EVAL_RUN`。可用本地 COMET 环境补算，再通过 `cjvmt score --input results/EVAL_RUN` 刷新报告。发布媒体留在本机，生成的报告提供 OCR 分层和统计图。

## 自录最小对

把获准用于课程的 `pairs.csv` 与 `pair01_A.mp4`、`pair01_B.mp4` 等视频放进 `data/raw/own/`，在 GPU 空闲时运行 `.venv/bin/cjvmt pairs`。命令按清单为 A、B 提供同一台词和前文，默认使用「先感知后翻译」，并在输出目录写出四条件 `pairs.csv`。 [录制与授权清单](docs/HUMAN_TASKS.md)

在输出表的 `review_T_neutral`、`review_T+A`、`review_T+V`、`review_T+V+A` 列分别填写「符合」「不符合」或「不确定」。本地回放刷新后显示逐条件判定，也可切换 A、B 版本。运行 `.venv/bin/cjvmt pairs --review-run results/PAIR_RUN_ID` 可统计两个版本都符合预期的成对结果，以及各条件相对纯文字的改善和退步。旧表的 `review` 总判定仍可显示。自录素材保留在项目本地。

## 找到结果

- 离线翻译在 `results/<run_id>/` 生成 `manifest.json`、`lines.jsonl`、`predictions.jsonl`、SRT/VTT、预览片段与 `compare.html`。
- 模拟直播在对应目录的 `session/` 保存逐句记录、音画输入、延迟 CSV、最终 SRT 和快译 SRT。 `manifest.json` 还记录了播放前的媒体和模型准备时间。
- 生成配音后，结果目录中的 `media/dub.mp4` 和 `media/dub.srt` 对应配音视频与对齐字幕。`dubbing.json` 记录状态，`dubbing.log` 记录合成过程。
- 验收证据见 [进度记录](reports/progress.md)，实测结果和局限见 [课程报告](reports/final_report.md)，课堂操作见 [演示脚本](docs/demo_script.md)。
- GitHub 上传范围、目录说明和当前磁盘规模见 [上传清单](docs/github_upload.md)。

语气标签属于未核实的模型观察。翻译差异只表示输出发生变化。语气最小对仍待已授权的录制素材。摄像头直播已用 Chromium 的测试输入验证，课堂设备待现场彩排。云 API 属于可选工作。
