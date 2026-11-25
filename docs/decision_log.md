# decision_log：设计决定与变更记录

规则：改 `configs/` 里的默认值、提示词、判定规则或模型选择时，在这里追加一条，不修改以前的记录。每条写明依据（哪份报告、哪个数字），并在同一次提交里完成。

格式：

```text
### D-<编号> 标题
- 日期：
- 决定：
- 理由和依据：
- 影响的文件：
- 状态：提议 / 已确认（谁、何时）/ 已废止（被 D-xxx 取代）
```

下面 D-001 到 D-014 是规划阶段（v0.2、v0.3）做出的设计决定，都还没有数据支持，状态是“提议”，等 H1 确认。

---

### D-001 训练目标由多模态 teacher 生成
- 日期：2026-11-16
- 决定：训练目标只取 teacher 在 `TARGET` 条件（`text_asr` + 画面 + 声音）下的译文。纯文本翻译的输出只作对照。
- 理由和依据：答案如果只由文字决定，视频和音频对它没有信息量，模型学到的是忽略视频（`docs/review_2026-11-16.md` §4.1）。
- 影响的文件：`AGENTS.md` §0；`docs/spec/02_pipeline.md` S8；`docs/spec/04_schema.md` §7
- 状态：提议

### D-002 每条台词保存两种文本
- 日期：2026-11-16
- 决定：`text_asr` 保留识别出的标点；`text_neutral` 把句末的问号、叹号、省略号统一成句号。分桶判定和模态消融用后者，训练目标的生成用前者。
- 理由和依据：识别模型加的问号已经带有语调信息，留在文本里会把音频的作用算到文本头上。
- 影响的文件：`docs/spec/01_task.md` §2；`configs/pipeline.yaml` 的 `text.*`
- 状态：提议

### D-003 画面用宽窗口，音频只保留目标台词
- 日期：2026-11-16
- 决定：画面取台词前 3 秒到后 1 秒；音频在台词区间之外静音。
- 理由和依据：窗口里的声音会带进别人说的话，尤其是台词之后的回答，这些内容不在文字输入里。
- 影响的文件：`docs/spec/01_task.md` §4；`configs/pipeline.yaml` 的 `clip.*`
- 状态：提议

### D-004 画面和音频作为两个独立文件传给模型
- 日期：2026-11-16
- 决定：不使用 mp4 内嵌音轨，不依赖 `use_audio_in_video`。
- 理由和依据：门控更容易保证；ms-swift issue #9507 报告过内嵌音频解码挂死。P3 M3.1 用资格探针比较两种方式后可以修改。
- 影响的文件：`docs/spec/01_task.md` §4.4；`configs/models.yaml` 的 `teacher.media_input`
- 状态：提议

### D-005 用“训练目标与纯文本译文是否不同”做候选预筛
- 日期：2026-11-16
- 决定：每条样本都跑 `TARGET` 和 `T`，由文本评审判断两者意思是否不同；只有不同或拿不准的进入后续验证。
- 理由和依据：两者相同的样本不可能通过通道 M。这比让文本模型凭空猜“有没有歧义”更省调用，也不容易漏。漏判率在随机试点上实测。
- 影响的文件：`docs/spec/02_pipeline.md` S8；`docs/spec/03_rules.md` §2
- 状态：提议

### D-006 增加中性译法检验
- 日期：2026-11-16
- 决定：存在一种在所有读法下都对的中文译法时，样本不进 core。
- 理由和依据：否则不表态的译文也算对，纯文本模型不看视频就能得分。
- 影响的文件：`docs/spec/01_task.md` §7；`docs/spec/03_rules.md` §3 的 C3
- 状态：提议

### D-007 线索核验改为“开放描述 + 蕴含判断”
- 日期：2026-11-16
- 决定：不再问“是否在摆手”这类是非题。先让另一家模型自由描述片段，再由文本评审判断描述是否包含该线索。
- 理由和依据：是非题容易诱导模型回答“是”。核验器的误肯定率用错配片段单独测量。
- 影响的文件：`docs/spec/03_rules.md` §5；`configs/prompts/prompts_v1.yaml`
- 状态：提议

### D-008 增加通道 O（语调的客观通道）
- 日期：2026-11-16
- 决定：对形式上既可以是疑问也可以是陈述的句子，用多套 ASR 的句末问号加句末音高来判定，不依赖 teacher 的感知。
- 理由和依据：已有研究（ContraProST）发现端到端模型听得到语调却常常不改译文。teacher 如果也是这样，通道 M 会找不到任何语调样本。
- 影响的文件：`docs/spec/03_rules.md` §7；`configs/pipeline.yaml` 的 `objective.*`
- 状态：提议

### D-009 评审团的组成规则
- 日期：2026-11-16
- 决定：至少两个开发方不同于 teacher 的多模态模型，其中至少一个的语言模型骨干不是 Qwen 系。
- 理由和依据：很多开源多模态模型的语言骨干来自 Qwen（例如 MiniCPM-o 4.5 用的是 Qwen3-8B），只看开发方不够。
- 影响的文件：`docs/spec/03_rules.md` §6；`configs/models.yaml`
- 状态：提议

### D-010 PLaMo 的输出不进训练目标
- 日期：2026-11-16
- 决定：PLaMo 3 Translate 只作 P6 的文本参照。
- 理由和依据：PLaMo Community License 的旧版条款规定，用其输出训练的模型算派生模型。现行版本待核对。
- 影响的文件：`docs/spec/06_compliance.md` §4；`configs/models.yaml`
- 状态：提议

### D-011 teacher 和学生模型的默认选择
- 日期：2026-11-16
- 决定：teacher 默认 Qwen3-Omni-30B-A3B-Instruct；主学生 Qwen2.5-Omni-7B；辅助学生 Qwen3-VL-8B-Instruct（只做文本对画面的对照）。
- 理由和依据：都是 Apache-2.0；Omni 模型能在同一骨干上跑全部输入条件。teacher 是否合格由 P3 的资格探针决定。
- 影响的文件：`configs/models.yaml`
- 状态：提议

### D-012 AGENTS.md 拆分
- 日期：2026-11-16
- 决定：`AGENTS.md` 只保留规则和索引，控制在 32 KiB 以内；细节移到 `docs/spec/`。
- 理由和依据：Codex 默认只读取 AGENTS.md 的前 32 KiB（`project_doc_max_bytes`），超出部分被静默丢弃。v0.2 有 47 KB。
- 影响的文件：`AGENTS.md`、`docs/spec/*`
- 状态：提议

### D-013 按 group 切分
- 日期：2026-11-16
- 决定：切分单位是 `group_id`（频道、系列或作品），不是场景或单句。
- 理由和依据：同一视频的不同场景，说话人和背景相同，按场景切分会泄漏。
- 影响的文件：`docs/spec/02_pipeline.md` S12
- 状态：提议

### D-014 主指标和文本指标
- 日期：2026-11-16
- 决定：主指标是 core-test 上的歧义消解准确率；中文目标报 chrF，不报不分词的 chrF++；BLEU 用 `tokenize=zh`。
- 理由和依据：参考译文来自 teacher，和参考比相似度会变成“和 teacher 有多像”。chrF++ 的词 n-gram 依赖空格分词。
- 影响的文件：`docs/spec/05_eval_train.md` §3
- 状态：提议

---

下面 D-015 起是结课冲刺版（MVP）的决定，2026-11-24。

### D-015 结课前先做系统原型，数据集建设推后
- 日期：2026-11-24
- 决定：根目录换成结课版的 `AGENTS.md` 和 `GOALS.md`，做三件事：视频翻译系统（四条件对比）、WMT26 中→日对照实验、数据集挖掘试点。完整计划移到 `docs/roadmap/`，结课后恢复。
- 理由和依据：课程快结束，来不及收集授权视频和建完整数据集；但公开的 WMT 真实视频测试集、开源全模态模型和已有的设计足够做出一个能演示、有真实数字的原型。
- 影响的文件：`AGENTS.md`、`GOALS.md`、`docs/roadmap/*`、`configs/mvp.yaml`、`configs/prompts/prompts_mvp.yaml`
- 状态：提议（等 H1 确认）

### D-016 主实验用 WMT26 中→日 speech
- 日期：2026-11-24
- 决定：主实验在 WMT26 中→日 speech 上做，整段翻译，条件 A–E 加两个错配条件。WMT25 日→中作为可选项。
- 理由和依据：WMT26 中→日有人工转写、人工参考和逐行对齐（课程汇报统计：114 个文档全部对齐，129 句问句），可以直接按行判对；也和课程汇报的实验设计一致。WMT25 日→中没有人工转写，能不能逐行对齐还要检查。
- 影响的文件：`AGENTS.md` §7.10、`configs/mvp.yaml` 的 `eval.*`
- 状态：提议

### D-017 试点标签用人工参考判断对错，不用评审团
- 日期：2026-11-24
- 决定：结课版的数据集试点，只对能用规则判对的现象（问句、文体）打标签，“对”和“错”以 WMT 的人工参考为准，再用错配条件做反事实对照。不做线索核验和多家族评审团。
- 理由和依据：WMT 自带人工参考，比自动评审更可靠，也省去调试多个模型的时间。代价是只能覆盖问句和文体两种现象，样本来自测试集、只能演示不能训练。完整计划的规则（`docs/spec/03_rules.md`）结课后再实现。
- 影响的文件：`AGENTS.md` §7.11、`configs/mvp.yaml` 的 `pilot.*`
- 状态：提议

### D-018 音频和画面分开传入，整段模式降低帧率
- 日期：2026-11-24
- 决定：沿用 D-004，音频和无声视频作为两个独立文件传入，`use_audio_in_video=False`。WMT 整段视频（平均约 30 秒）用 `video_input.doc_fps` 和 `doc_max_pixels`，比单句模式低。
- 理由和依据：保证条件隔离；Qwen2.5-Omni-7B 处理 30 秒视频的理论最低显存是 41.85 GB，降低帧率和分辨率才能在 48 GB 的卡上跑。数值是起点，M3 实测后调整。
- 影响的文件：`configs/mvp.yaml` 的 `video_input.*`
- 状态：提议

### D-019 “全给”条件改用原生音画交错输入（部分取代 D-018）
- 日期：2026-11-24
- 决定：`T+V+A` 把门控音频重新封装进无声画面（`clips_av`），开启 `use_audio_in_video=True`。WMT 整段实验直接用原始 mp4。把两者分开传入，改为可选的对照条件 `T+V+A_sep`。`T+A` 和 `T+V` 不变。
- 理由和依据：Qwen2.5-Omni 按时间把每 2 秒的画面和声音交错编码（TMRoPE），这是它训练时的原生方式；分开传入会丢掉时间对应关系，可能低估“全给”的效果。条件隔离不受影响：“加画面”仍然听不到声音，“加声音”仍然看不到画面。D-004 担心的解码挂死问题出现在 ms-swift 训练中，结课版只做推理。
- 影响的文件：`AGENTS.md` §2、§7.5、§7.6；`configs/mvp.yaml` 的 `video_input.av_full_mode`、`conditions.could`
- 状态：提议

### D-020 问句分层、配套指标和基线
- 日期：2026-11-24
- 决定：WMT26 的 129 句问句按中文原文的疑问标记分成 Q1–Q5，分别报告；问句同时报告召回、误判和 F1；文体报告混淆矩阵、κ 和“全部译成常体”的基线；报告写明当前样本量能检出多大的差别；检查画面字幕。
- 理由和依据：带疑问标记的问句只看文字也能认出来，会稀释声音的作用；全部译成常体就有约 71.5% 的一致率；129 句只能检出约 8–13 个百分点以上的差别（`docs/mvp_analysis.md` §2.4–2.7）。
- 影响的文件：`AGENTS.md` §7.10；`GOALS.md` M3；`configs/mvp.yaml` 的 `eval.zh_question_markers`、`eval.onscreen`
- 状态：提议

### D-021 语气保留：文字层不加模型，语音层用 CosyVoice
- 日期：2026-11-24
- 决定：文字层在感知提示里加第 4 题“语气和强度”，翻译时按 `tone_note` 用目标语言的语气手段表达，只用于引导和先感知后翻译模式，对比模式不加。语音层可选：CosyVoice 以原句声音作音色参考、按语气指令合成译文语音，装在单独的 Python 3.10 环境里，作为独立进程调用。只克隆本人同意的声音，不克隆 WMT 视频里的人，合成语音标明“AI 合成”。
- 理由和依据：Qwen2.5-Omni 本来就能听到原声，文字层只要改提示词；Qwen2.5-Omni 自带的语音输出只有固定音色，SeamlessExpressive 不支持日语；CosyVoice 支持中日两种语言、跨语言音色克隆和用指令控制语气。对比模式不加语气要求，保证各条件只差模态说明。
- 影响的文件：`AGENTS.md` §1（D9）、§7.6、§7.14，§9 压缩成一段以控制文件大小；`GOALS.md` M2T；`configs/mvp.yaml` 的 `tone.*`、`tts.*`；`configs/prompts/prompts_mvp.yaml` 的 `perceive`、`tone_note`、`tts_instruct`；`docs/tone_preservation.md`
- 状态：提议

### D-022 显卡：优先 48 GB；RTX 5090 有条件可用
- 日期：2026-11-24
- 决定：优先用 48 GB 的卡（例如 AutoDL 的 vGPU-48GB）。用 RTX 5090（32 GB）时：换 PyTorch 2.7 以上、CUDA 12.8 的镜像；注意力用 `sdpa`；faster-whisper 报错就换 transformers 版 Whisper（`asr.backend: hf-whisper`）或 SenseVoice-Small；语音合成不和翻译模型同时常驻；WMT 整段实验换回 48 GB 以上的卡。
- 理由和依据：RTX 5090 的算力架构是 sm_120，PyTorch 2.5.1 + CUDA 12.4 没有对应的内核；社区报告 faster-whisper 在 RTX 5090 上出现 cuBLAS 错误，CosyVoice 固定的旧版 PyTorch 在 Blackwell 上也不能用；7B 原版处理 30 秒视频的理论最低显存是 41.85 GB，32 GB 放不下。两台机器每小时价格接近。
- 影响的文件：`AGENTS.md` §5；`docs/environment.md` §0、§3、§5、§7、§8、§9、§10；`configs/mvp.yaml` 的 `asr.*`、`models.omni.attn_implementation`、`realtime.asr_backend`；`docs/build_multimodal_translation.md` 第 1、3 步和 §9；`docs/HUMAN_TASKS.md` H1、H3
- 状态：提议

## 2026-11-24 · AutoDL 结课部署

- RTX 5090 上的基础镜像为 Python 3.12、PyTorch 2.5.1/CUDA 12.4。已验证本机原有的隔离环境 PyTorch 2.13.0+cu130 能通过 BF16 矩阵乘法，且支持 `sm_120`。课程主环境与 GPU 进程分开，主环境使用 faster-whisper large-v3 CPU int8。
- 课程机部署用现有 `cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit` 权重。真实运行锁定其下载收据中的 revision，记录 vLLM-Omni 配置与适配器哈希。配置同时提供官方 Qwen2.5-Omni 后端，RTX 5090 默认 SDPA。官方权重的大文件下载确认见 H3a。
- `T+V+A` 离线按 AGENTS.md §7.6 用单个封装视频及原生交错输入。实时按 M2R 用独立 JPEG 帧列表和门控到句子的声音。网页使用 FastAPI 与自包含 HTML/CSS/JS，以便离线页面共用交互代码。
- 自录素材尚缺。先用附有署名的 Commons 片段检验链路和课堂回放。维基新闻的播报声来自原作品的合成旁白，保留该说明。没有从其他项目借用内部媒体。

- 实时部署的 GPU 语音识别验证：CUDA 12 的 CTranslate2 与 CUDA 13 的 Omni 进程使用各自动态库搜索路径。Silero 已切出的实时语句使用单束解码，离线仍保留词级时间戳。GPU 版浏览器运行的时间和 ASR 参数随 manifest 保存。

- 摄像头直播沿用现有 FastAPI 网页。AudioWorklet 提供 PCM 音频，浏览器降采样摄像头画面并通过 SSH 隧道上传，后端与模拟直播共用滚动缓存、Silero 分句、工作线程和导出逻辑。这样课堂部署继续只需要一个本地 HTTP 端口。

## 2026-11-25 · 确认课程模型与 WMT26 媒体

- 研究者确认继续用已实测的 Qwen3-Omni AWQ 做课程部署与 WMT26 评测，Qwen2.5-Omni 保留为可选后端。
- 研究者批准下载 WMT26 `humeval` 发布的 `multimodal_inputs.zip`。媒体只用于本地评测，课堂包沿用有明确许可的公开演示素材。

## 2026-11-25 · RTX 4090 单序列 KV 缓存

- 在 `configs/local_omni_deploy.yaml` 将 KV 缓存设为 2 GiB。本机原配置自动分配约 16.23 GiB，而调度上限为一个序列。新配置报告容量 21,840 token，单序列上限为 8,192 token。
- 同一公开日语样本的十条未缓存输出逐条相同，峰值显存从 42,833.5 MiB 降到 28,369.5 MiB。中文模拟直播和 WMT26 16 行文档通过。 [实测记录](project_architecture.md#本次验证记录)
- 部署 SHA-256 变化会生成新的翻译缓存键。历史 WMT 正式结果保留原部署身份。恢复历史任务时需使用与其 manifest 一致的配置。
