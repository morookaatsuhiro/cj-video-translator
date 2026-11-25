# AGENTS.md — CJ-VMT：自动化构建的中日多模态视频翻译数据集

> 版本：v0.2（自动化优先） | 更新日期：2026-11-16 | 取代 v0.1（存档：`docs/archive/AGENTS_v0.1.md`）  
> 阅读顺序：本文件 → `PHASED_GOALS.md` → `reports/progress.md` 最后一条 → `docs/decision_log.md`  
> 修改原因与勘误：`docs/review_2026-11-16.md`  
> **本文件是工作指令，不代表已有数据、模型、GPU 或实验结果。所有状态以仓库里的实际文件和运行记录为准。**

---

## 0. 角色、目标与三条铁律

你是本项目的研究工程师兼多模态数据工程师。

**目标**：尽量少用人工，自动构建一个日→中（简体）多模态翻译数据集，满足四个条件：可以训练，可以评测，能证明“视频或音频确实改变了译法”，能用来微调多模态大模型。

**三条铁律**（任何阶段都不能违反）：

1. **答案必须“看过视频”。** 训练目标和参考译文由多模态 teacher 在 `T+V+A` 条件下生成。纯文本 MT 的输出只能作为对照，永远不能成为训练目标。原因：如果答案只由文字决定，`P(Y|X,V,A)=P(Y|X)`，模型学到的是“忽略视频”。
2. **“视频或音频有用”必须由自动对照实验证明。** 证明方法：同一个 teacher 在 `T / T+V / T+A / T+V+A` 下给出语义不同的译法，并依次通过线索核验、反事实测试和异构模型评审团（§6）。“片段里有视频”或“teacher 自己说有用”都不算证据。
3. **不逐条人工审核，但不等于免检。** 每个阶段都必须产出自动 QA 指标、拒收日志和可抽查的 HTML 预览。人工只做 §2 列出的事。

**工作原则**

- 先有证据，后生成。可观察事实和推断分开存放；不确定时写 `unknown` / `uncertain` / `not_tested`，不要猜。
- 先小后大：先跑通 20 条端到端，再做 2,000 条随机试点来测产出率，最后扩量。
- 评测先行。测试集在任何训练和提示词调优之前冻结。
- 可追溯、幂等、可缓存、可复现。原始文件只读；每次变换都生成新文件。
- 如实报告。没跑过就写“未运行”；不编造许可证、时间戳、译文、分数和进度。
- 对研究者用中文；代码、字段名、命令用英文；术语保留英文（MMT、VMT、ASR、VAD、QE、LoRA、COMET、chrF）。

---

## 1. 研究任务与边界

### 1.1 任务

- **主方向 Ja→Zh-Hans**：输入日语台词 `X`（自动转写）、片段视频 `V`、原始音频 `A`、按固定规则抽取的前文 `C`，输出简体中文 `Y`，即建模 `P(Y | X, V, A, C)`。
- **Zh-Hans→Ja**：P7 再扩展，单独构建数据，不能把日→中数据倒过来用。

### 1.2 研究问题

| 编号 | 问题 | 回答方式 |
|---|---|---|
| RQ1 | 真实日语对话中，需要视频或音频才能确定译法的台词占多少？集中在哪些歧义类型？ | 随机样本上的自动估计，加抽检校准，报告 95% 置信区间 |
| RQ2 | 不微调时，在同一骨干上，`T+V`、`T+A`、`T+V+A` 比 `T` 的歧义消解准确率高多少？ | 同骨干零样本消融 |
| RQ3 | 用自动数据微调后，学生模型是真的学会了使用非语言线索，还是只是文本翻译变好了？ | `S_T` vs `S_TVA` vs `S_TVA_shuf`（§12.4） |
| RQ4 | 给出无关或错配的视频、音频时，模型有多常被误导？ | control 集与反事实集 |

### 1.3 样本分桶

这套命名取代 v0.1 的 A/B/C，避免和输入条件混淆。

| bucket | 定义（精确规则见 §6） | 用途 |
|---|---|---|
| `core` | 文本加前文存在真实歧义；多模态改变了译法；线索经过核验；反事实敏感；评审团全票支持 | 核心 benchmark；训练时上采样 |
| `general` | 质量合格，但不满足 core 的全部条件（包括文本本身已经够用的样本） | 训练主体、一般评测 |
| `control` | 文本没有歧义，各条件的译法一致 | 评测时配无关视频，检验模型会不会被误导 |
| `rejected` | 任一硬性检查失败 | 写入 `reports/rejected/`，不删除 |

### 1.4 歧义类型（多标签）

`ellipsis`（省略）、`reference`（代词、こそあ指示词、零形式的所指）、`polysemy`（多义）、`speech_act`（接受/拒绝/讽刺/道谢/道歉等言语行为）、`question_intonation`（靠语调区分疑问和陈述）、`emotion_prosody`（情绪与语气）、`register_politeness`（语体与敬语）、`speaker_identity`（说话人及其关系）、`scene_event`（动作与场景事件）。

与旧语料库的对应：J1 → `question_intonation`，J2 → `reference`，J3 → `speech_act`，J4 → `ellipsis`。

### 1.5 不做的事情

- 不把纯文本 MT 的输出当参考译文或训练目标。
- 不把“有视频”等同于“视频有用”；不把 teacher 的自评当证据。
- 不为凑 core 数量放宽规则；不按模型表现挑测试样本。
- 不给随意交换的视频编造“正确译文”。反事实测试只测“译法有没有变”，不定义新答案。
- 不把目标语烧录字幕、teacher 线索、读法列表或参考译文放进学生模型或被评测模型的输入。
- 不对未经批准的来源下载或处理媒体；不把受限媒体上传到第三方 API；不公开发布受限媒体。
- 测试集冻结之前，不训练、不调参、不宣称收益。

---

## 2. 人工与自动的分工

| 编号 | 人工动作 | 时间点 | 规模 | 能否省略 |
|---|---|---|---|---|
| H1 | 确认研究协议、分桶规则、人工预算 | P0 Gate | 一次，约 30 分钟阅读 | 否 |
| H2 | 决定用哪些来源、在什么范围内使用（授权、平台条款、仅内部还是拟发布） | P1 | 每个来源一次 | 否 |
| H3 | 提供 GPU 或 API；接受模型和数据条款（gated 模型、WMT、EVA 申请等） | P1–P3 | 一次 | 否 |
| H4 | 审批阶段 Gate：阅读自动报告，可以随手翻看 HTML 预览 | 每个阶段 | 10–30 分钟 | 否 |
| H5 | 一次性抽检校准：150 条，网页上做选择题（§6.5） | P4 | 约 60–90 分钟 | **可以省略**，但数据集要标为 `unaudited`，论文不能声称有人工验证 |
| H6 | 冻结测试集；批准发布范围 | P5、P7 | 各一次 | 否 |

- Agent 不能代做 H1–H6。遇到这些事项时，写进 `docs/HUMAN_TASKS.md`，然后继续做不依赖它的部分。
- Agent 不能要求研究者逐条审核转写、译文或标签。

---

## 3. 参考资料

核对日期 2026-11-16。使用前请再次确认版本和许可。**论文公开不等于视频可以再分发。**

| 资料 | 链接 | 与本项目的关系 |
|---|---|---|
| EVA（EMNLP 2023） | https://aclanthology.org/2023.emnlp-main.260/ ；https://github.com/ku-nlp/video-helpful-MMT | 方法参考：视频有用的歧义评测集。语向为日英和中英；视频需发邮件申请，仅限研究，禁止再分发 |
| VISA（LREC 2022） | https://github.com/ku-nlp/VISA | 日英歧义字幕，可参考其歧义类别 |
| BigVideo（Findings ACL 2023） | https://aclanthology.org/2023.findings-acl.535/ | 英中，450 万句；有歧义/无歧义测试集的设计可以参考 |
| TriFine（COLING 2025） | https://aclanthology.org/2025.coling-main.547/ | 英↔中三模态，7 类细粒度标签；不含日语 |
| SHIFT（EMNLP 2025） | https://aclanthology.org/2025.emnlp-main.161/ | 只在需要时选关键帧，可降低成本 |
| CoMMuTE（ACL 2023） | https://aclanthology.org/2023.acl-long.295/ | 对比式评测：同一句歧义原文配不同图片 |
| ContraProST（WMT 2024） | https://arxiv.org/abs/2410.24019 | 语调对比评测 |
| WMT25 General MT | https://github.com/wmt-conference/wmt25-general-mt | **外部测试集**：日→中 speech（带视频和人工参考），附人工评分 |
| WMT26 General MT | https://github.com/wmt-conference/wmt26-general-mt | **外部测试集**：中→日 speech（带视频、ASR 转写、人工转写、人工参考） |
| WMT26 视频字幕翻译 | https://aclanthology.org/2026.wmt-main.58/ | 语向为简体中文→英/泰/印尼/马来/繁中，不含日语；可参考其评测方式 |
| Qwen2.5-Omni | https://github.com/QwenLM/Qwen2.5-Omni | 学生模型；15 s 视频 BF16 理论最低 31.11 GB，实际约 ×1.2 |
| Qwen3-Omni | https://github.com/QwenLM/Qwen3-Omni | teacher；Instruct 版 15 s 视频理论最低 78.85 GB；语音输入支持日语 |
| ms-swift | https://github.com/modelscope/ms-swift （文档按实际安装的版本） | SFT 和 LoRA；JSONL 格式为 `messages` + `videos`/`audios` |
| sacreBLEU / COMET / MetricX | https://github.com/mjpost/sacreBLEU ；https://github.com/Unbabel/COMET ；https://github.com/google-research/metricx | 评测指标 |
| YODAS | https://arxiv.org/abs/2406.00899 | CC BY 3.0 的 YouTube 语音数据（含人工字幕子集），可作候选来源，需评估平台条款 |
| CEJC | https://chunagon.ninjal.ac.jp/static/CEJC_TermsOfService.pdf | 日语日常会话（含映像），研究用途需申请，禁止整体复制 |

---

## 4. 仓库结构

```text
cj-vmt/
├── AGENTS.md  PHASED_GOALS.md  README.md  pyproject.toml  Makefile  .env.example  .gitignore
├── configs/
│   ├── pipeline.yaml          # 全部阈值：默认值 + 校准记录（谁、何时、依据哪个报告）
│   ├── models.yaml            # 模型角色、id、revision、解码参数、帧率/分辨率
│   ├── prompts/               # 带版本号的提示词模板
│   └── approved_sources.yaml  # 只列研究者已批准的来源（H2）
├── docs/  research_scope.md  annotation_guideline.md  data_card.md  decision_log.md
│          HUMAN_TASKS.md  review_2026-11-16.md  archive/
├── schemas/  sample.schema.json  evidence.schema.json  teacher_output.schema.json  source_registry.schema.json
├── manifests/  source_registry.csv  videos.jsonl  clip_manifest.jsonl  split_registry.jsonl
├── data/                      # 不进 git
│   ├── raw/                   # 只读
│   ├── interim/<stage>/       # 每个阶段一个目录，内含 stage_manifest.json
│   ├── processed/  clips/  clips_noaudio/  audio/  frames/
│   ├── dataset/               # canonical JSONL（带版本号）
│   ├── exports/               # ms-swift 等训练格式
│   └── external/              # WMT25/26 等外部测试集（仅研究用）
├── src/cjvmt/  cli.py  schema.py  io.py  cache.py  stages/  judges/  metrics/  stats.py  report/
├── scripts/                   # 下载和批处理，只处理 approved_sources
├── tests/  fixtures/          # 合成测试数据，文件名必须含 synthetic
├── reports/  progress.md  <stage>_qa.md  bucket_report.md  audit/  rejected/
└── experiments/<run_id>/      # manifest.json  predictions.jsonl  scores.jsonl  summary.csv
```

`.gitignore` 必须排除：`data/`、`experiments/*/checkpoints`、`.env`、媒体文件（`*.mp4`、`*.wav`）、模型权重和缓存。Git LFS 不是存放受限视频的理由。

---

## 5. 自动化构建流水线（S0–S13）

### 5.0 所有阶段的通用规则

- 每个阶段是一个 CLI 子命令：

  ```bash
  cjvmt <stage> --input <path> --output <dir> --config configs/pipeline.yaml [--limit N] [--dry-run] [--resume] [--seed 2026]
  ```

- 输入和输出都是 JSONL。每个输出目录都写一个 `stage_manifest.json`，记录：输入文件的 sha256、git commit、配置哈希、模型 id 和 revision、提示词版本、计数（输入、输出、按原因分类的拒收数）、起止时间、GPU 型号。
- **幂等**：`sample_id = "ja-" + sha1(parent_video_sha256 + t_start_abs_ms + t_end_abs_ms)[:12]`。已完成的 id 直接跳过。已有字段不覆盖，只追加新版本。
- **缓存键** = `model_id@revision + prompt_version + input_sha256 + decoding_params`。
- 失败样本写入 `reports/rejected/<stage>.jsonl`，附原因代码，不删除。
- 每个阶段都生成 `reports/<stage>_qa.md`：统计数字，加上随机 20 条的 HTML 预览（片段、文本、各条件译文、线索）。

### S0 来源登记（研究者做决定，Agent 准备材料）

- Agent 收集候选来源的元数据，填写 `manifests/source_registry.csv` 草稿：标题、权利人、许可证、平台条款要点、时长、有没有人工日语字幕、有没有硬字幕。再抽 1–2 分钟用 VAD 估计对话密度。
- **只有研究者写进 `configs/approved_sources.yaml` 的来源才能进入 S1。**
- 来源优先级：
  1. 研究者自有或有书面授权的视频；
  2. 许可证明确覆盖预期用途的开放许可视频（例如 CC BY），并同时评估平台条款；
  3. 研究许可语料（EVA 视频需申请，CEJC 含映像需申请），在许可范围内使用，禁止再分发；
  4. 外部测试集（WMT25/26），只用于评测。
- 不默认使用影视剧、动漫或平台爬取的内容。

### S1 导入

- 下载或复制到 `data/raw/<source_id>/`，设为只读。计算 sha256，用 `ffprobe` 读取时长、fps、分辨率、声道、采样率、编码，写入 `manifests/videos.jsonl`。
- 检查：能解码、有音轨、时长大于 0。sha256 相同的文件只保留一份。

### S2 音频准备、VAD、镜头切分

- 用 `ffmpeg -vn -ac 1 -ar 16000` 生成 ASR 用的 wav。
- 可以用人声分离（例如 Demucs）提高 ASR 准确率，但分离结果只用于 ASR，**不作为模型输入**。模型输入一律用原始音轨。
- 用 Silero VAD 切出语音段，用 PySceneDetect 找镜头切换点。

### S3 双路 ASR

- 至少两套互相独立的日语 ASR。例如主路用 kotoba-whisper v2.x 或 Whisper large-v3，副路用 ReazonSpeech 或 SenseVoice。来源有人工日语字幕时，以人工字幕为 `manual_caption` 并做对齐。
- 保存原始输出：标点、词级时间戳、`avg_logprob`、`no_speech_prob`、`compression_ratio`。
- **幻觉过滤**（命中即标记或拒收）：
  - `compression_ratio > 2.4`；
  - `avg_logprob < -1.0` 且 `no_speech_prob > 0.6`；
  - 同一个 3-gram 重复 4 次以上；
  - 命中常见幻觉短语表（例如在非结尾的静音段出现「ご視聴ありがとうございました」）。
- **一致性**：先把两路结果都转成片假名读音（MeCab/UniDic），再计算 CER，记为 `asr_agreement_cer`。转读音是为了避免汉字和假名写法不同而虚增 CER。
- `transcript_tier` 从高到低：`manual_caption` > `auto_agreed`（CER ≤ τ）> `auto_single`。

### S4 切句、对齐、说话人

- 用 VAD 段和 ASR 句读切分台词，用 WhisperX 或同类工具做词级对齐，用说话人分离（例如 pyannote）得到 `speaker_id`；置信度低于阈值时写 `unknown`。
- **所有时间都用父视频的绝对毫秒，字段名以 `_abs_ms` 结尾。**
- 默认过滤（P2 校准）：时长 0.5–12 s，字符数 2–80。多人重叠说话时标 `overlap=true`。

### S5 片段与前文

- 片段窗口为 `[t_start − pre_pad, t_end + post_pad]`，默认 `pre_pad=3000 ms`、`post_pad=1000 ms`，总长不超过 15 s。窗口里有镜头切换时要记录下来。
- 每个样本生成三个媒体文件：
  - `clips/<id>.mp4`：原音视频，H.264，长边不超过 720；
  - `clips_noaudio/<id>.mp4`：去掉音轨，给 `T+V` 用；
  - `audio/<id>.wav`：16 kHz 单声道，给 `T+A` 用。
- 前文 `C`：默认取同一视频中 30 秒内的前 3 句，带 `speaker_id`。所有条件共用同一个 `C`，规则写进 `context.policy`。
- 文本保存两种形式：
  - `text_asr`：ASR 原文，保留其标点；
  - `text_neutral`：去掉句末的 ？/！/。，其余不变。

  原因：ASR 自动加的问号已经带有一部分语调信息。

### S6 屏幕文字检查（OCR）

- 每秒抽 2 帧，保存到 `frames/` 并记录时间戳，用 PaddleOCR（中、日）识别文字。识别结果分三类处理：

  | 类别 | 含义 | 处理 |
  |---|---|---|
  | `target_subtitle` | 中文字幕，持续出现在字幕区 | 测试集和 core 一律剔除；训练集可以遮挡后保留 |
  | `source_caption` | 和台词高度重合的日文テロップ | 标记 `onscreen_source_text=true` |
  | `scene_text` | 招牌、文件等场景文字 | 作为一种线索，`cue_type=scene_text` |

- 如果某个样本唯一有效的线索是屏幕文字，归入 `general` 并标 `ocr_dependent=true`。这类样本单独统计，不算作“非语言线索有用”。

### S7 纯文本分析（成本低，全量运行）

- 文本 LLM 读入 `X` 和 `C`，输出：可能的读法列表（每种读法给出中文译文和一句说明）、歧义类型（§1.4，多标签）、`text_ambiguous: yes/no/uncertain`。
- **强文本译文**：至少 2 个不同家族的文本系统，各给一个 `T_strong` 候选。
- **规则挖掘标签**（只用于排序优先级，不直接决定分桶）：
  - 8 个字以内的短句；
  - こそあ指示词；
  - 授受动词；
  - 主语省略（KWJA 或 GiNZA）；
  - 句末没有「か」但可能是疑问；
  - 高歧义表达：いい/いいです/結構/大丈夫/すみません/どうも/ちょっと/まあ/やばい/さすが/なるほど/本当。

### S8 多模态 teacher 翻译

- **全量**：每个样本跑一次 `T+V+A` 翻译，作为训练目标候选。
- **候选集**：满足以下任一条件的样本，再用同一个 teacher、同一个提示词模板、同一组解码参数跑 `T`、`T+V`、`T+A`：
  - S7 的 `text_ambiguous ≠ no`；
  - 命中规则挖掘标签；
  - 被随机抽中（约 10% 的基线样本，用于估计真实产出率和 S7 的召回率）；
  - **试点阶段（P3–P4）的随机试点样本，全部都跑**，保证产出率是无偏估计；
  - **control 候选**：从 `text_ambiguous = no` 的样本中抽取，数量与 core 候选同一量级。没跑过四种条件的样本不能进入 control。
- **证据调用**：和翻译调用分开进行。teacher 输出 JSON，内容包括选择的读法和线索列表，每条线索含 `modality`、`cue_type`、`t_start_abs_ms`、`t_end_abs_ms`、`observation`、`interpretation`、`confidence`。线索时间超出片段窗口的作废。
- **条件隔离**（必须遵守）：
  - `T+V`：用 `clips_noaudio`，并关闭 `use_audio_in_video`；
  - `T+A`：只给 wav；
  - `T+V+A`：用原片段并开启 `use_audio_in_video`，或者用“无音轨视频 + wav”。两种方式选一种，全项目固定不变；
  - 帧率、最大帧数、分辨率在所有条件下保持一致。
- **解码**：贪心解码（temperature=0），`max_new_tokens` 固定。输出必须通过 `schemas/teacher_output.schema.json` 校验，失败重试 1 次，仍失败就拒收。

### S9 自动验证（只对候选集运行）

1. **读法映射**：一个文本 LLM 评审（不能和 teacher 同家族）把每个候选译文对应到 S7 的读法编号。如果出现 S7 没列出的新读法，就追加到读法列表并标注来源。
2. **语义差异**：判定 `T+V+A` 译文和其他候选的关系，取值为 `same / wording_only / different_meaning / different_speech_act / different_register / different_referent / different_polarity`。
3. **线索核验**：
   - 视觉线索：另一家族的视频模型看同一片段，回答封闭问题，例如“片段内 3.2–4.1 秒，右侧人物是否在摆手？是/否/看不清”。核验时在线索区间内按每秒 4 帧加密采样，避免漏掉短手势。
   - 音频线索：优先用可测量的特征，例如句末 F0 斜率（Parselmouth）、停顿、语速、能量。情绪类线索用另一家族的音频模型判断，只算弱证据。
   - 只有回答为“是”才算通过。
4. **反事实测试**：

   | 符号 | 替换方式 |
   |---|---|
   | `CF_V` | 把画面换成同一视频中相隔至少 30 s、同一说话人（如已知）、时长相同的无音轨画面；没有合适的就换其他 group 的片段 |
   | `CF_A` | 用 Parselmouth/Praat 把同一段音频的音高曲线拉平，保留词语、去掉语调 |
   | `CF_VA` | 同时替换画面和音频 |

   `X` 和 `C` 不变，teacher 重新翻译。只记录“读法有没有改变”，不为反事实输入定义新的正确答案。
5. **评审团**：至少 2 个不同家族的多模态评审模型。每个模型都以 AB、BA 两种顺序观看片段、`X`、`C`，以及两个译文：`T+V+A` 译文，和表达另一读法的最强文本译文。评审在两个译文中选“哪个更符合视频/音频”，也可以选“都可以 / 都不对 / 看不出”。评审看不到模型名和条件名。
6. **模态归因**：比较 `T+V`、`T+A` 是否给出和 `T+V+A` 相同的读法：
   - 只有 `T+V` 能给出 → `visual`；
   - 只有 `T+A` 能给出 → `audio`；
   - 两者各自都能给出 → `either`；
   - 两者都给不出，只有 `T+V+A` 能 → `both_needed`；
   - 无法判断 → `unclear`。

### S10 分桶

按 §6 的规则执行。

### S11 训练目标与质量过滤

- 训练目标就是 teacher 的 `T+V+A` 译文，**不改写**。规范化后的版本（全角标点、空白）另存一个字段。
- 检查项：
  - 语言：必须是简体中文，不能含假名；繁体字比例超过 5% 时用 OpenCC 转换并标记；
  - 没有解释性文字、引号或前缀；
  - 长度比（中文字符数 / 日文字符数）在 [0.3, 3.0] 之间（P3 校准）；
  - QE：用 CometKiwi 或 MetricX QE 打分，阈值在 WMT25 日→中人工评分上校准；低于阈值的拒收或降级。

### S12 去重、切分、冻结

- **近重复检测**：音频指纹（chromaprint）、关键帧感知哈希、文本 MinHash。互为重复的样本归入同一个 group。
- **分组切分**：按 `group_id`（频道、系列或作品）切分，**不能按场景或单句切分**。顺序是先划 test，再划 dev，最后是 train。比例和 core 的数量在 P5 决定，种子 2026。
- **冻结**：写出 `manifests/split_registry.jsonl`，以及 `data/dataset/test_v1.jsonl` 的 sha256 清单。冻结后不能修改，要改只能发布新版本。

### S13 导出

- 从 canonical JSONL 导出 ms-swift JSONL（每个条件一个文件），以及评测包（core/general/control 和反事实对）。
- 导出器只能写入该条件允许的输入。user 消息里不能出现 teacher 线索、读法列表、分桶标签或参考译文。
- 每次导出随机抽 10 条，用所用 ms-swift 版本做数据加载和模板编码的 dry-run（不训练）。要确认 `T+V+A` 样本里确实编码进了音频 token。

---

## 6. 自动判定规则

参数放在 `configs/pipeline.yaml`。下面是默认值，只能在 dev 集或 P2–P4 的试点数据上调整，每次调整都写进 `docs/decision_log.md`。

### 6.1 core：必须同时满足 C1–C8

| 规则 | 内容 |
|---|---|
| C1 | `transcript_tier` 为 `manual_caption` 或 `auto_agreed`，且 CER ≤ τ_core（默认 0.08）；没有幻觉标记 |
| C2 | 没有 `target_subtitle` |
| C3 | 文本有歧义：S7 判定 `text_ambiguous` 为 `yes` 或 `uncertain`，且读法列表中既有 `T+V+A` 的读法，也有至少一个不同的读法 |
| C4 | 多模态改变了译法，两条都要满足：<br>(a) teacher 自己的 `T` 给出的读法和 `T+V+A` 不同；<br>(b) 强文本系统**并非全部**给出 `T+V+A` 的读法。<br>如果所有强文本系统都已经给出这个读法，说明文本已经够用，归入 `general` |
| C5 | 至少 1 条线索核验结果为“是”，并且这条线索不能只是 `scene_text` 或 `onscreen_source_text` |
| C6 | 反事实敏感：按归因模态做对应的反事实测试后，读法发生改变。<br>`visual` → `CF_V` 改变；`audio` → `CF_A` 改变；`either` → `CF_VA` 改变；`both_needed` → `CF_V` 和 `CF_A` 都改变 |
| C7 | 评审团全票：所有评审在两种顺序下都选 `T+V+A` 译文 |
| C8 | 训练目标通过 S11 的全部检查 |

### 6.2 其他分桶

| bucket | 条件 |
|---|---|
| `general` | C2（训练集允许遮挡后保留）和 C8 通过；C1 按 τ_train（默认 0.15）判定并通过；但 core 的条件不全满足 |
| `control` | C1 通过；`text_ambiguous = no`；四种条件都跑过，且所有条件的译文都映射到同一个读法 |
| `rejected` | 任一硬性检查失败：解码、语言、泄漏、幻觉、QE 过低、时长超限等 |

### 6.3 标签等级 `label_tier`

| 取值 | 含义 |
|---|---|
| `auto_raw` | 模型直接输出，尚未验证 |
| `auto_verified` | 通过了 §6.1–6.2 中所有适用的规则 |
| `human_audited_pass` | 在 H5 抽检样本中，人工同意 |
| `human_audited_fail` | 在 H5 抽检样本中，人工不同意。这类样本不能进入 test |

### 6.4 每次分桶都要输出的统计

- 评审之间的 Cohen's κ；AB 与 BA 两种顺序的一致率（衡量位置偏差）；线索核验通过率；反事实敏感率。
- 各 bucket 的比例及 95% Wilson 置信区间。
- 以上统计都要按歧义类型、模态归因、来源分层，并单独统计随机基线样本，作为对总体产出率的无偏估计。

### 6.5 抽检校准（H5，推荐做）

- **抽样**：150 条，其中 100 条 core（按歧义类型和归因分层），25 条 general，25 条 control。这些样本只从 dev 和随机池里抽，不从 test 里抽。
- **页面**：由 `cjvmt audit-page` 生成一个静态 HTML，答案保存在本地 JSON 里。每条样本的流程：
  1. 播放片段，显示 `X` 和 `C`；
  2. 随机顺序显示两个译文；
  3. 研究者选择“左更好 / 右更好 / 都可以 / 都不对 / 看不出”；
  4. 选完后再显示 teacher 给出的线索，问一句：“这条线索在视频里确实存在吗？是 / 否 / 看不清”。
- **指标**：
  - core 精确率 = 研究者选“多模态译文更好”的比例。选“都可以”算作 core 不成立；
  - 参考译文可接受率；
  - 线索真实率；
  - 以上都给出 Wilson 95% 置信区间。
- **建议门槛**：core 精确率不低于 0.75，且置信区间下界不低于 0.65。达不到就在 dev 上修改规则，然后用**新抽的**样本重新估计。不能拿同一批抽检样本反复调参。
- **零人工的补充校准**：用 WMT25 日→中的人工评分，检验 QE 和评审模型与人工的一致程度（Kendall τ、成对准确率），作为选择 QE 阈值和评审模型的依据。

---

## 7. 提示词与模型调用规则

- 模板放在 `configs/prompts/`，每个模板有 `prompt_id` 和版本号。只要改动就升版本，旧缓存不能复用。
- 翻译调用的模板在各条件之间只能相差一句“模态说明”（例如“请参考随附视频”），其余文字逐字相同，system prompt 也相同。
- teacher 的翻译调用只输出译文；证据调用另外单独进行。这样可以避免“先解释再翻译”改变训练目标的分布。
- 评审和核验模型必须和 teacher 来自不同家族。评审看不到模型名和条件名，两个译文的位置随机，并且两种顺序都要跑。
- 任何被评测模型的输入里，都不能出现参考译文、分桶标签、读法列表，或 OCR 识别出的中文字幕文本。
- 使用闭源 API 之前，要确认三件事：条款是否允许把输出用作训练目标；数据是否跨境、隐私怎么处理；受限媒体一律不上传。
- 每次调用都要记录：模型、revision、时间、延迟、输入和输出 token 数（拿得到的话），以及 API 费用。

---

## 8. 模型角色

以下于 2026-11 核对。使用前请复核 revision、许可证和显存。

| 角色 | 默认候选 | 备选 | 注意 |
|---|---|---|---|
| ASR（双路） | kotoba-whisper v2.x / Whisper large-v3（-turbo） | ReazonSpeech、SenseVoice-Small | 两路必须独立；SenseVoice 还会给出情绪和事件标签，只作弱证据 |
| 对齐 / 说话人 | WhisperX（日语 wav2vec2 对齐模型） | stable-ts；pyannote（gated，需接受条款） | 说话人不确定时写 `unknown` |
| VAD / 镜头 | Silero VAD；PySceneDetect | — | — |
| OCR | PaddleOCR（中文、日文） | — | — |
| 日语分析 | fugashi + UniDic / SudachiPy / GiNZA；KWJA（省略主语） | — | 用于规则挖掘和读音转换 |
| 强文本 MT（对照） | Apache-2.0 的通用 LLM（例如 Qwen3.5 系列）；Hy-MT2（先核实日语覆盖范围和许可证） | PLaMo 3 Translate 8B/31B | **PLaMo 的输出默认不进入训练目标**：PLaMo Community License 有“用输出训练的模型视为派生模型”的条款，使用前核对现行版本 |
| 多模态 teacher | Qwen3-Omni-30B-A3B-Instruct（本地运行，Apache-2.0，能跑全部四种条件） | API：Qwen3.5-Omni-Plus、Qwen3.8-Omni-Flash（仅 API）、Gemini 等，使用前先核对条款 | 15 s 视频 BF16 理论最低 78.85 GB。不需要语音输出时按官方 README 关闭 talker；片段控制在 15 s 以内 |
| 线索核验 / 评审（必须异家族） | 非 Qwen 家族的视频模型，最好也能处理音频（例如 MiniCPM-o、InternVL、GLM-V 系列） | 闭源 API | 至少 2 个家族。只能看视频的模型只用来核验视觉线索 |
| QE | CometKiwi（CC BY-NC-SA，仅研究） | MetricX-24 的 QE 模式（Apache-2.0）；MetricX-25（先核对许可） | 阈值在 WMT25 日→中人工评分上校准 |
| 学生（主，同骨干消融） | Qwen2.5-Omni-7B（Apache-2.0） | Qwen3-Omni-30B-A3B（需要多卡） | 15 s 视频推理理论最低 31.11 GB，实际约 ×1.2 |
| 学生（辅，只做 T vs T+V） | Qwen3-VL-8B-Instruct | Qwen3.5 小模型（确认支持视频后再用） | 不能处理音频 |
| 训练框架 | ms-swift（以实际安装版本的文档为准） | — | JSONL：`messages` + `videos` / `audios` |

**GPU 规划**（AutoDL；以下是估计值，P3 和 P6 实测后替换）

| 任务 | 起步配置 | 备注 |
|---|---|---|
| ASR、OCR、对齐 | 1× 24 GB | — |
| teacher：Qwen3-Omni-30B-A3B（BF16） | 1× 80 GB | 片段 10–15 s，关闭 talker；显存不够就用 2 卡或减少帧数。优先用支持该模型的批量推理框架，实测吞吐 |
| 学生：Qwen2.5-Omni-7B LoRA | 1× 80 GB | 先在 80 GB 上试；用 48 GB 需要减少帧数或降低分辨率。记录峰值显存 |

模型可以从 ModelScope 或镜像下载，要记录 revision 和 sha。

---

## 9. Canonical Schema（v0.2.0）

一行对应一条台词样本。下面的示例是**虚构的 schema 演示，不是真实数据**。

```json
{
  "schema_version": "0.2.0",
  "sample_id": "ja-3f9a1c2b7d10",
  "is_synthetic": true,
  "src_lang": "ja",
  "tgt_lang": "zh-Hans",
  "parent_video_id": "SRC-000123",
  "group_id": "GRP-000045",
  "source_registry_id": "RIGHTS-000012",
  "utterance": {"t_start_abs_ms": 754300, "t_end_abs_ms": 755600,
                "speaker_id": "SRC-000123_spk2", "speaker_confidence": 0.71, "overlap": false},
  "clip": {"t_start_abs_ms": 751300, "t_end_abs_ms": 756600,
           "path_av": "data/processed/clips/ja-3f9a1c2b7d10.mp4",
           "path_v_noaudio": "data/processed/clips_noaudio/ja-3f9a1c2b7d10.mp4",
           "path_audio": "data/processed/audio/ja-3f9a1c2b7d10.wav",
           "sha256_av": null, "scene_cut_in_window": false},
  "source_text": {
    "asr": [{"system": "asr-primary@<rev>", "text": "いいよ。", "avg_logprob": -0.21,
             "no_speech_prob": 0.02, "compression_ratio": 1.1},
            {"system": "asr-secondary@<rev>", "text": "いいよ"}],
    "asr_agreement_cer": 0.0,
    "transcript_tier": "auto_agreed",
    "text_asr": "いいよ。",
    "text_neutral": "いいよ"
  },
  "context": {"policy": "prev3_within30s_v1",
              "turns": [{"speaker_id": "SRC-000123_spk1", "text": "これ、運ぶの手伝おうか",
                         "t_start_abs_ms": 751800, "t_end_abs_ms": 753200}]},
  "onscreen_text": {"target_subtitle": false, "source_caption": false, "scene_text": false},
  "text_analysis": {"model": "<text-llm>@<rev>", "text_ambiguous": "yes",
                    "ambiguity_types": ["speech_act"],
                    "readings": [{"reading_id": "r1", "gloss": "接受帮助", "zh": "好啊，谢谢。"},
                                 {"reading_id": "r2", "gloss": "谢绝帮助", "zh": "不用了。"}]},
  "candidates": [
    {"condition": "T",        "model": "<teacher>@<rev>",   "prompt_id": "translate_ja_zh@1", "text": "好啊。",     "reading_id": "r1"},
    {"condition": "T_strong", "model": "<text-mt-1>@<rev>", "prompt_id": "translate_ja_zh@1", "text": "好的。",     "reading_id": "r1"},
    {"condition": "T_strong", "model": "<text-mt-2>@<rev>", "prompt_id": "translate_ja_zh@1", "text": "没事，不用。", "reading_id": "r2"},
    {"condition": "T+V",      "model": "<teacher>@<rev>",   "prompt_id": "translate_ja_zh@1", "text": "不用了。",   "reading_id": "r2"},
    {"condition": "T+A",      "model": "<teacher>@<rev>",   "prompt_id": "translate_ja_zh@1", "text": "好啊。",     "reading_id": "r1"},
    {"condition": "T+V+A",    "model": "<teacher>@<rev>",   "prompt_id": "translate_ja_zh@1", "text": "不用了。",   "reading_id": "r2"},
    {"condition": "CF_V",     "model": "<teacher>@<rev>",   "prompt_id": "translate_ja_zh@1", "text": "好啊。",     "reading_id": "r1",
     "cf_source": "SRC-000123@812000-817300"}
  ],
  "evidence": [{"evidence_id": "ev1", "modality": "visual", "cue_type": "gesture",
                "t_start_abs_ms": 754500, "t_end_abs_ms": 755400,
                "observation": "说话者一边摆手一边后退半步",
                "interpretation": "可能表示谢绝",
                "source": "model", "model": "<teacher>@<rev>", "confidence": 0.8,
                "verification": {"verifier": "<other-family-vlm>@<rev>",
                                 "question": "片段内 3.2–4.1 秒，右侧人物是否在摆手？",
                                 "answer": "yes"}}],
  "verification": {"divergence": "different_speech_act",
                   "jury": [{"judge": "<family-A>@<rev>", "order": "AB", "choice": "mm"},
                            {"judge": "<family-A>@<rev>", "order": "BA", "choice": "mm"},
                            {"judge": "<family-B>@<rev>", "order": "AB", "choice": "mm"},
                            {"judge": "<family-B>@<rev>", "order": "BA", "choice": "mm"}],
                   "jury_unanimous": true, "counterfactual_sensitive": "yes"},
  "translation": {"target": "不用了。", "target_condition": "T+V+A", "target_model": "<teacher>@<rev>",
                  "normalized": "不用了。",
                  "qe": {"metric": "<qe-model>@<rev>", "score": 0.81},
                  "checks": {"lang_zh_hans": true, "no_kana": true, "length_ratio_ok": true}},
  "labels": {"bucket": "core", "label_tier": "auto_verified",
             "visual_dependency": "yes", "audio_dependency": "no",
             "modality_attribution": "visual", "ocr_dependent": false,
             "contrast": {"target_reading": "r2", "distractor_readings": ["r1"]}},
  "split": null,
  "provenance": {"pipeline_version": "0.2.0", "git_commit": null,
                 "stage_manifests": ["data/interim/s8_teacher/stage_manifest.json"],
                 "created_at": "2026-11-16T00:00:00Z"}
}
```

**字段规则**

- **时间**：一律是父视频的绝对毫秒，字段名以 `_abs_ms` 结尾，要求 `t_end > t_start`。台词和证据都必须落在片段窗口内。片段内的相对时间只在导出或核验提问时计算。
- **枚举**：`yes / no / uncertain / unknown / not_tested`。不允许用空字符串代替。
- **evidence 统一结构**：`evidence_id, modality, cue_type, t_start_abs_ms, t_end_abs_ms, observation, interpretation, source, model, confidence, verification`。其中 `observation` 写可观察的事实，`interpretation` 写推断，两者不能合并。
- **`cue_type` 取值**：`gesture`、`head_motion`、`facial_expression`、`gaze`、`body_posture`、`object_interaction`、`scene_event`、`scene_text`、`onscreen_source_text`、`speaker_identity_visual`、`prosody_final_rise`、`prosody_emphasis`、`pause`、`speech_rate`、`voice_emotion`、`nonspeech_audio_event`。
- **`translation.target`** 必须来自 `T+V+A` 条件下的 teacher；纯文本候选只能放在 `candidates` 里。
- **`is_synthetic=true`** 只能出现在 `tests/fixtures/` 里。validator 如果在 `data/` 里发现合成样本，直接报错。
- **隐私**：不存真实姓名，`speaker_id` 一律匿名。
- **训练格式适配**：每种训练格式都通过 exporter 适配，核心 schema 里不写任何训练框架专用的字段。

---

## 10. 自动质量检查

每个阶段都要跑，结果写进对应的报告。

| 检查 | 内容 |
|---|---|
| `schema_check` | 类型、枚举、必填字段、时间区间、引用 ID、文件存在 |
| `media_check` | ffprobe 能解码；时长合理；**`clips_noaudio` 确实没有音轨**；wav 为 16 kHz 单声道 |
| `asr_check` | 幻觉标记、双路 CER 分布、`transcript_tier` 分布 |
| `alignment_check` | 台词在片段窗口内；时间单调；说话人置信度分布 |
| `overlay_check` | 三类屏幕文字的比例；test 和 core 里 `target_subtitle` 必须为 0 |
| `language_check` | 简体、无假名、长度比、没有解释性文字 |
| `condition_isolation_check` | 各条件的提示词只差模态说明；媒体文件和条件对应正确；缓存键不同 |
| `judge_check` | 评审 κ、位置偏差、核验通过率、反事实敏感率 |
| `leakage_check` | 跨 split 的 group、近重复、音频指纹、文本重合都为 0；导出的 user 消息里没有答案字段 |
| `license_check` | 每个样本的来源都在 `approved_sources.yaml` 里，并且用途匹配 |
| `audit_check` | 抽检精确率和置信区间；`human_audited_fail` 的样本不在 test 里 |
| `release_check` | 版本号、sha256 清单、Data Card、许可文件、受限媒体排除清单 |

---

## 11. 切分与防泄漏

- 按 `group_id` 切分。先冻结 test，再划 dev 和 train。冻结后 test 只能整体换版本，不能增删单条。
- 阈值、提示词、超参数只在 dev 和试点数据上调整，test 只在最后评测时跑一次，并记录 run_id。
- 外部测试集（WMT25 日→中、WMT26 中→日）永远不参与训练和调参，不再分发其中的视频和截图。
- core 测试集的规模由统计功效决定。在不一致率约 30% 的假设下，要以 80% 功效检出 10 个百分点的差异，约需 250 条；实际数量按测出的不一致率重算，写进 P5 报告。凑不够就如实报告，不放宽规则。

---

## 12. 评测与训练

### 12.1 输入条件

使用显式命名，下表附与旧材料 A–F 的对照。旧材料中 PPT 第 21 页和 `SPEC.md` 的 A–F 定义不一致，新项目统一用下面的名称。

| 名称 | 输入 | 旧 PPT p21 | 旧 SPEC |
|---|---|---|---|
| `T_neutral` | 前文 + 台词（句末标点中性化） | A | A |
| `T_asr` | 前文 + ASR 原文（含其标点） | B | B |
| `T+desc_auto` | `T_asr` + 自动生成的描述（异家族 VLM 给出的线索、F0 标签）；即级联做法 | C | — |
| `A_only` | 只给音频，不给文字 | D | — |
| `V+A_only` | 给视频和音频，不给文字 | E | — |
| `T+A` | T + 音频 | — | C |
| `T+V` | T + 无音轨视频 | — | D |
| `T+V+A` | T + 视频 + 音频 | — | E |
| `T+desc_oracle` | T + 人写的描述（只用于已有人工描述的外部集） | F | F |
| `T+V_shuf` / `T+A_shuf` | T + 错配的媒体 | — | 扰动检验 |

注：旧材料中的 A 组用的是人工转写。本项目没有逐条人工转写，`T_neutral` 用 `manual_caption` 或 `auto_agreed` 的转写来近似，报告中要写明这一差别。

### 12.2 指标

- **主指标：core 上的歧义消解准确率（DisAcc）。** 判断模型输出表达的是目标读法还是干扰读法。判定方法：
  - 文本 LLM 评审，需用抽检样本或外部集校准；
  - 可以规则化的类型用规则判定，例如“吗/？”、他/她、敬语标记；
  - CoMMuTE 式对比打分：比较模型对两个候选译文的对数似然。
- **成对准确率**：在“自然最小对”上计算。自然最小对指规范化后台词相同、片段不同、core 读法不同的两条样本；两条都对才算对。
- **鲁棒性**：
  - control 上，`T+V_shuf` 相对 `T` 的译文改变率，越低越好；
  - core 上，`T+V_shuf` 的“跟随率”。
- **整体质量**：
  - chrF（字符级，为主）；
  - BLEU（`tokenize=zh`，仅供参考）；
  - COMET（wmt22-comet-da）：silver 参考和 WMT25 人工参考分开报告；
  - QE。
  - 中文目标不报不分词的 chrF++。
- **成本**：延迟、峰值显存、每个样本的帧数和 token 数。

### 12.3 统计

- 配对的对错：McNemar 精确检验，多重比较用 Holm 校正。
- 连续分数：配对 bootstrap 10,000 次，给出 95% 置信区间，种子 2026。
- 分层报告：按歧义类型、模态归因、来源。样本少于 20 的格子标“样本太少”。

### 12.4 微调对照（回答 RQ3）

- 三个学生模型保持以下条件全部相同：骨干、训练目标（teacher 的 `T+V+A` 译文）、步数、种子（预算允许时跑 3 个种子）。

  | 学生 | 训练输入 |
  |---|---|
  | `S_T` | 只给文本 |
  | `S_TVA` | 文本 + 视频 + 音频 |
  | `S_TVA_shuf` | 文本 + 训练时随机错配的媒体；用来排除“只是多了 token 或训练量”的影响 |

- **结论条件**：`S_TVA` 在 core-test 上显著优于 `S_T`，同时 `S_TVA_shuf` 与 `S_T` 相当，才能写“模型学会了使用非语言线索”。
- 训练只用 train，超参数只看 dev，test 只在最后跑一次。
- 每次训练都要记录：commit、数据版本、split 版本、种子、GPU、精度、LoRA 配置、帧数和分辨率、峰值显存、耗时、预测文件。
- 可选的级联对照：`T+desc_auto` 对比端到端的 `T+V+A`，对应课程汇报里“级联 vs 端到端”的问题。

---

## 13. 合规、版权、隐私（硬规则）

- 公开可看，不等于可以下载、训练或再分发。以下每一项要分别记录许可状态：观看、下载、缓存、裁剪、转写、翻译、训练、衍生使用、公开发布。
- 视频、音轨、字幕、译文可能属于不同的权利人，要分别登记。
- 开放许可（例如 CC BY）之外，还要评估平台的服务条款。由研究者决定，并记录在 `source_registry.csv` 里。
- 研究许可语料（EVA、CEJC、WMT 媒体）只在许可范围内使用，不再分发媒体。
- 默认只公开代码、schema、统计数据，以及不能还原出受限内容的标注和索引。媒体是否公开，由 H6 决定。
- **隐私**：
  - 不收集真实姓名；
  - 人脸和声音属于个人信息，受限媒体不传给第三方 API；
  - 涉及未成年人或私人场景时加强审查；
  - 性别相关信息只作为翻译线索（他/她）记录为“外观呈现”，不推断身份。
- **撤回**：`source_registry` 里记录联系方式和处理流程。收到撤回请求时，删除对应的媒体和衍生样本，并发布新版本。

---

## 14. 工程规范

- **技术栈**：Python 3.11；pydantic v2；typer（命令行入口 `cjvmt`）；ruff（行宽 120）；pytest（`gpu`、`slow`、`network` 三种标记默认跳过）；YAML 配置；logging；pathlib。密钥只放在 `.env` 里。
- **常用命令**：
  - `make check`：lint 加测试；
  - `make smoke`：用合成 fixture 和 Mock 模型跑通 S4–S13。
- 所有外部依赖（模型、ffmpeg、OCR、ASR、API）都通过适配层调用，测试时用 Mock 替换。
- **必须有测试的内容**：
  - schema 的正例和反例；
  - 时间区间规则；
  - §6 分桶规则的真值表：每条规则至少一个通过用例、一个失败用例；
  - 条件隔离：`T+V` 的文件没有音轨，各条件提示词只差模态说明；
  - 缓存命中；
  - 导出不泄漏：user 消息里不含 target、readings、bucket；
  - 统计函数：用能手算的小例子验证。
- 不删除 `data/raw/` 和人工文件；不覆盖已有标签，只追加新版本。

---

## 15. 回复研究者的格式

依次写：

1. 当前阶段
2. 本次完成
3. 生成的文件和运行的命令
4. 自动 QA 结果（数字必须来自 stage_manifest 或报告）
5. 风险与阻塞（附 HUMAN_TASKS 编号）
6. Gate 结论：通过 / 未通过 / 待审
7. 下一步

没有实际执行的工作写成“计划”，不能写成“已完成”。

---

## 16. 下游 Agent 主提示词（模板）

```text
# ROLE
你是 CJ-VMT 项目的 Research Engineer / Multimodal Dataset Engineer。以 AGENTS.md 为强约束，以 PHASED_GOALS.md 为阶段路线图。

# MISSION
在最少人工的前提下，自动构建可追溯、有合法使用权的 Ja→Zh-Hans 多模态视频翻译数据集。
训练目标由多模态 teacher 在 T+V+A 下生成；视频或音频是否有用，由同 teacher 的四条件对照、线索核验、反事实测试和异构评审团自动判定。

# CURRENT TASK
阶段：[P0–P7]
目标：[填写]
可用数据和路径：[填写；没提供就报告缺失]
算力与 API：[填写；没提供就不要猜]
允许的操作：[只读 / 可写代码 / 可处理已批准来源 / 可调用模型 / 可训练]

# MANDATORY ORDER
1. 读 AGENTS.md、PHASED_GOALS.md、reports/progress.md 最后一条；确认阶段的 Gate。
2. 先用合成 fixture 和 Mock 跑通，再处理真实数据；先 --limit 小批量，再全量。
3. 每个阶段写 stage_manifest.json 和 reports/<stage>_qa.md；失败样本写入 reports/rejected/。
4. 只处理 approved_sources.yaml 里的来源；需要人做的事写进 docs/HUMAN_TASKS.md 后继续。
5. 条件隔离、强文本对照、异家族评审、反事实测试，缺一不可。
6. 不用 test 调参；不把答案、线索、bucket 泄漏进模型输入。
7. 只报告实际跑出的数字；没跑就写“未运行”。

# OUTPUT
当前阶段 / 本次完成 / 文件与命令 / 自动 QA 结果 / 风险与阻塞 / Gate 结论 / 下一步
```

---

## 17. 新开项目时的默认第一条任务（P0）

如果仓库里除规划文件外还没有其他内容，按顺序做：

1. 建立项目骨架：`pyproject.toml`、`Makefile`、`.gitignore`、`.env.example`、`configs/`、`schemas/`、`src/cjvmt/`、`tests/`。
2. 编写 `docs/research_scope.md`（研究问题、分桶、输入条件、人工预算）和 `docs/annotation_guideline.md`（自动判定说明和抽检说明）。
3. 编写 `schemas/*.json`、对应的 pydantic 模型和 `cjvmt validate`。
4. 准备至少 12 条合成 fixture（至少 6 条合法、6 条非法），文件名含 `synthetic`。
5. 把 §6 的分桶规则实现为纯函数，并写真值表测试。
6. 编写 `configs/pipeline.yaml` 和 `configs/models.yaml` 初稿，阈值标注“默认值，待校准”。
7. 写 P0 Gate 自检报告，在 `docs/HUMAN_TASKS.md` 里列出 H1–H3。

P0 阶段**不下载模型和数据，不调用付费 API，不启动 GPU 任务。**
