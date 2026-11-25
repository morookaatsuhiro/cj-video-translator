# 视频人物、语调和说话风格标注

## 网页操作

在 6006 的「结果回放」选择已完成的上传、按原速翻译或摄像头会话，点击「分析人物和语气」。可关闭「推测语气」，先生成声纹、画面位置、音高、语速和用语标注。完成后可选择「补充模型语气」。有配音的会话切换到原声即可查看说话人字幕和相关逐句详情。

回放用「说话人 1」「说话人 2」表示同一视频内的匿名分组。逐句详情可以定位口型匹配的画面，并显示句末上扬、下降或较平、基频中位数、音高跨度和估计每秒字数。同一说话人有至少三句有效数据时，才显示其个人音高或语速基线。敬体、尊敬表达、常体、中文「您」和问句是基于转写的用语线索。「平静」「亲切」「犹豫」「讽刺」等是模型推测，页面保留「未核实」提示。

## 实现流程和模型

| 处理阶段 | 本机模型与算法 | 产物 |
|---|---|---|
| 时间定位 | 读取 Whisper 切句时间和最终转写。用 faster-whisper 集成的 Silero VAD 获取有声范围 | 16 kHz 单声道原声和每句时间 |
| 说话人分组 | 使用 CosyVoice 的 CAMPPlus。每句切成约 2.2 秒窗口，提取 Kaldi 80 维滤波器组特征，按余弦距离做平均链接聚类。默认聚类距离阈值 0.60。短窗口只在与已有参考声音足够接近时归类 | 仅在本视频有效的编号。窗段证据不充分或存在多个声音组的字幕显示待确认 |
| 画面关联 | OpenCV YuNet 在 640×384、12 FPS 的采样画面定位脸部，MediaPipe Face Landmarker 提取口部关键点。短轨迹按框重叠连接，同步比较口部开合和原声音量的时间变化 | 有明显匹配时记录左侧、中央或右侧及可回放时间 |
| 声调和风格 | WORLD DIO 和 StoneMask 提取基频。用有声段前后部分的稳健统计判定句末趋势。用 VAD 内字数估计语速，按同一声音组的中位数计算相对变化。复用项目的中日语用语检查 | 逐句基频、音高跨度、相对语速及文本风格 |
| 模型语气 | 复用已有的音画观察。有确认说话人且还缺少标签的句子，可交给本机 Qwen3-Omni 的原声语气提示 | 有限标签和强度。不确定结果留空 |

CAMPPlus、YuNet、Face Landmarker 和 WORLD 在 CPU 环境运行。GPU 只用于所选的 Qwen3-Omni 语气推测。声音和脸部特征在本次分析进程内使用，结果文件保存说话人编号、时间戳、匹配侧、音高和标签。 [CosyVoice 官方前端](https://github.com/QwenAudio/CosyVoice/blob/main/cosyvoice/cli/frontend.py) · [YuNet 官方说明](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet) · [Face Landmarker 官方文档](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker) · [WORLD Python 实现](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder)

```text
cj-vmt/
├── src/cjvmt/
│   ├── demo.py                         回放 /api/runs/{id}/speakers
│   ├── speakers.py                     解析时间轴、用语标注、语气提示
│   ├── speaker_worker.py               CPU 分组、口型和基频
│   └── web/{app.js,index.html,app.css}  回放按钮、标注和字幕
├── .cache/
│   ├── cosyvoice-env/bin/python         Torch、ONNX Runtime、WORLD、MediaPipe
│   ├── cosyvoice-model/campplus.onnx    约 27 MiB，复用现有声纹权重
│   └── speaker-model/
│       ├── face_detection_yunet_2023mar.onnx  232,589 字节
│       └── face_landmarker_v1.task            3,758,596 字节
└── results/<会话 ID>/
    ├── speakers.json                   标注和每位说话人的统计
    └── speakers.log                    本地 CPU 分析日志
```

当前环境使用 MediaPipe 0.10.32 和 OpenCV Headless 5.1.0.95。安装到其他机器时，可以通过 `CJVMT_TTS_PYTHON` 与 `CJVMT_TTS_MODEL` 指定已具备音频依赖的 Python 和 CAMPPlus 权重，通过 `CJVMT_FACE_DETECTOR`、`CJVMT_FACE_LANDMARKER` 指定两个视觉模型。缺少视觉模型时，回放仍可显示声纹分组和音高测量。 [环境变量示例](../.env.example)

## 样本验证和适用范围

- 将两段来源已知的真实中文声音交替放在双人同框的受控视频里，顺序为左侧 A、右侧 B、左侧 A，约 15 秒。CPU 分析耗时约 18.5 秒。三段声音全部归到预期组。后两段分别定位到右侧和左侧，首段口型保留待确认。A 两段的基频中位数分别为 254.7 Hz、244.9 Hz，B 为 120.1 Hz。这些数字是样本测量值。
- 保持同样的音频，把画面换成静止双人截图。仍得到 A、B、A 三个发言段，画面关联为零，体现画面有脸时也要检查同步证据。
- 在 6006 网页上传加入停顿的双人样本，真实字幕切句产生三句。Chromium 验证人物顺序为 1、2、1，画面依次匹配左、右、左。三句都有模型语气标注。字幕、逐句详情、进度与标注下载均通过。 [线上双人标注](../results/20261125-1550_zh2ja_62ebdbc8/speakers.json)
- 旧日语会议回放可直接补做分析。核对声纹聚类阈值之后，五句里三句归为单人，两句保留多人待确认。两句同声线的日语字幕归到同一组，逐句展示敬体、常体等用语线索。独立验证了从 CPU 标注补做模型语气时可复用已有结果。 [旧实时会话标注](../results/20261125-1128_job_e0c4483c/speakers.json)
- 带背景音乐、插话、镜头切换及侧脸的新录音，可能被拆成过多声音组或缺少画面位置。短句也可能只有文字风格。当前样本支撑功能验证，正式评测可补入人工标注的中日对话，分别计算说话人错误率、画面匹配精确率和覆盖率，以及语气标签的一致率。

后续若主要处理多人同时说话，可以先用带重叠输出的 pyannote Community-1 对照声纹分段，再验证 TalkNet 这类训练过的音画发言者检测。两项改动分别针对声音重叠和画面唇音匹配，适合用同一批多人样本单独比较。 [pyannote Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) · [TalkNet](https://github.com/TaoRuijie/TalkNet-ASD)
