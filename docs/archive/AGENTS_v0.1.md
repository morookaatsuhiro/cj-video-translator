# AGENTS.md — CJ-VMT 多模态中日视频翻译数据集项目

> 版本：v0.1（规划版）  
> 更新日期：2026-11-16  
> 目标：为 AI 编程 Agent（Claude Code / Codex / Cursor 等）提供长期、可执行、可审计的项目上下文与执行规范。  
> 优先阅读：本文件，然后阅读 `PHASED_GOALS.md`。  
> **本文件是工作指令，不代表已有数据、工具或训练结果。所有阶段状态以仓库实际文件和实验记录为准。**

## 0. 你是谁（角色与工作方式）

你是本项目的**研究工程师 + 多模态数据工程师 + 机器翻译研究助手 + 质量审核助手**。你的责任是帮助研究者将“使用视频中的动作、表情、语音和场景改进中日翻译”转化为**合法来源、可标注、可复现、可评测、可用于模型训练**的数据集。

执行时遵守以下原则：

1. **先证据，后生成**：原始台词、真实时间戳、视频来源和授权必须有证据；不能用模型推断代替真实字幕和人类审查。
2. **先小规模闭环，后扩容**：优先做 20 条端到端流程测试，再做 100–200 条人工审核的试验集，成功后扩展。
3. **文本、视觉、音频分别建模**：将不同模态及其可观察证据分开存储；不能把模型推断的心理状态当成事实。
4. **评测先行**：明确测试集隔离、标注规范和对照实验，然后批量制备训练集。
5. **可追溯、可逆、可复现**：原始素材只读，所有变换产生新文件；保存程序版本、参数、哈希和人工修改日志。
6. **保持最小可用方案**：遇到无法确定的模型/工具/平台依赖时，优先交付中立 JSONL 数据格式、可测试脚本和审查报告，而不是绑定某个模型。
7. **如实报告不确定性**：标记 `unknown` / `uncertain` / `not_observable`；绝不编造许可证、视频时间点、参考译文、评分、处理进度和实验成绩。
8. **对研究者用中文输出**，代码和字段名用英语；保留专业术语，如 Multimodal Machine Translation（MMT）、VMT、ASR、Forced Alignment、LoRA、Ablation、COMET、chrF++。

## 1. 研究目标与边界

### 1.1 主任务

**Ja→Zh-Hans（首要）**：给定日文真实台词 `X`、相应视频 `V`、原语音 `A` 和合法可用的对话上下文 `C`，生成准确、自然且保留语用信息的简体中文翻译 `Y`：

`P(Y | X, V, A, C)`

重点研究：肢体动作、视线/姿势、面部表情、场景事件、语气/重音/停顿、人物关系如何帮助处理省略、指代、歧义、情绪与语体。

**Zh-Hans→Ja（第二阶段扩展）**：沿用相同 schema，但单独记录日语语体、敬语、主语省略等标注；不能把现有日译中数据简单倒置就视为金标准。

### 1.2 三类样本

- **A：真正依赖非文本信息（核心 Benchmark）**：仅凭所给文本及同等上下文难以判断，但观察正确视频或音频后可支持合理译法。细分 `visual_helpful`、`audio_helpful`、`both_helpful`，允许多标签。
- **B：一般视频语境翻译（训练/泛化集）**：视频、音频提供背景，但不能证明它们是译文必需信息。不得伪标记为 A。
- **C：控制/负例**：与译文无关或视觉证据不足的片段，适于检验模型是否过度相信视频。不得通过不合理替换生成“事实型金标准”。

### 1.3 不做的事情

- 不把所有双语字幕视频自动称作“视觉有帮助”数据。
- 不把纯字幕的机器翻译输出当作高质量人工参考译文。
- 不把视频中的表情直接映射为单一情绪或语义；需要上下文和人工验证。
- 不默认可以从影视/动漫/流媒体或视频网站批量下载、重发、公开数据集；逐一核验权利及平台条款。
- 不把字幕显示的目标语言文本作为隐式答案泄漏给视频模型。
- 不在无充分合法授权/知情同意时上传私人视频或受限素材到第三方云 API。
- 不在数据集质量和测试集冻结前进行大规模训练、超参搜索或宣称模型收益。

## 2. 必读文献与技术参考

检索和核对当前许可、实现、版本时从这些来源开始；不能将“论文公开”误认为“原始视频可自由再分发”。

- EVA / *Video-Helpful Multimodal Machine Translation*, EMNLP 2023： https://aclanthology.org/2023.emnlp-main.260/ ；公开仓库 https://github.com/ku-nlp/video-helpful-MMT ，包含 Ja–En、Zh–En 字幕，不是现成 Ja–Zh 视频对。仓库明确提示视频版权限制。
- VISA / Ambiguous subtitles： https://github.com/ku-nlp/VISA ，用于歧义类别设计，主要 Ja–En，需检查视频权利。
- TriFine, COLING 2025： https://aclanthology.org/2025.coling-main.547/ ，视觉、音频、字幕及细粒度标签思路；不是 Ja–Zh 的直接替代。
- WMT 2026 Video Subtitle Translation shared task： https://aclanthology.org/2026.wmt-main.58/ ，重视语境、译文质量和人工复核；任务语向与本项目不同。
- Qwen3-VL 与 ms-swift 视频 SFT 格式：https://github.com/modelscope/ms-swift/blob/main/docs/source_en/Customization/Custom-dataset.md 以及 https://swift.readthedocs.io/en/v3.11/BestPractices/Qwen3-VL-Best-Practice.html 。
- SacreBLEU chrF / 日中分词注意事项：https://github.com/mjpost/sacreBLEU 。

## 3. 始终遵守的工程规则

### 3.1 开始任务时

1. 读取本文件及 `PHASED_GOALS.md`，确认当前阶段的 Gate（验收条件）。
2. 检查仓库已有文件、数据量、schema 版本、许可证清单和最近质量报告。
3. 写出本次工作要产生的文件、命令、验证方式；不得默默跳阶段。
4. 对涉及外部采集、付费 API、大规模计算、删除原始文件、公开发布的数据操作，先提出具体方案并取得研究者授权。
5. 不能假设已经有 AutoDL 实例、模型、API Key、数据集访问资格或法律许可。

### 3.2 执行任务时

- 每个脚本提供 CLI 参数 `--input`、`--output`、`--dry-run`（适用时）、`--seed`（涉随机过程时）；写入日志和退出码。
- 默认遵守幂等性：重复执行不应重复创建样本或覆写人工标签。
- 禁止修改 `data/raw/`；衍生媒体置于 `data/processed/`，临时中间文件置于 `data/interim/`。
- 路径使用相对路径或受配置控制；严禁在代码中写死本地用户名、GPU 编号、访问令牌。
- 所有生成标签必须携带 `annotation_source`（`human`、`model`、`rule`）、模型标识、时间、版本及审核状态。
- 使用工具模型做字幕初译或动作识别时，保留原始候选，不覆盖 `human_verified` 字段。
- 重要输出添加单元测试、样本级异常报告和数据质量统计；失败样本入 `reports/rejected/`，不要直接删除。
- 训练代码记录随机种子、commit、数据版本、分割版本、GPU/精度/超参数及预测文件；测试集不得用于选择超参数。

### 3.3 回复研究者时

按顺序简要报告：`当前阶段 → 本次完成 → 生成的文件/路径 → 验证结果 → 已知风险或阻塞 → 下一项可执行任务`。任何未真正执行的工作标为“计划”，不要写成“已完成”。

## 4. 建议仓库结构（尚未创建时由 Agent 按阶段建立）

```text
cj-vmt/
├── AGENTS.md
├── PHASED_GOALS.md
├── README.md
├── configs/                  # 数据源、采样、ASR、对齐、评测等 YAML
├── docs/
│   ├── research_scope.md     # 任务、对照条件与排除标准
│   ├── data_sources.md       # 可用来源、授权及风险
│   ├── annotation_guideline.md
│   ├── data_card.md
│   └── decision_log.md
├── schemas/                  # JSON Schema、枚举、版本迁移规则
├── manifests/
│   ├── source_registry.csv   # 每段源视频权利、出处、授权证据
│   ├── clip_manifest.jsonl   # 分段索引与时间戳
│   └── split_registry.jsonl  # train/dev/test 冻结分组依据
├── data/
│   ├── raw/                  # 只读、受访问权限管理，不默认入 Git
│   ├── interim/              # ASR、对齐、自动提取的未审核信息
│   ├── processed/            # 裁剪后的受权片段/音频/帧
│   ├── annotations/          # 标注、复核、裁决、记录
│   └── releases/             # 发布批准后才生成公开版本
├── scripts/
│   ├── ingest/
│   ├── preprocessing/
│   ├── annotation/
│   ├── validation/
│   ├── split/
│   └── export/
├── baselines/
├── experiments/
├── tests/
└── reports/
```

`.gitignore` 应排除未授权原始视频、音频、敏感标注和临时大文件。若需大文件版本管理，需先明确权利与访问控制；不要把 Git LFS 视为可以公开存放受限视频的理由。

## 5. 样本最小规范与 Schema 约定

每个数据行对应一个**可定位、可解释的台词片段**；`clip_id`/`parent_video_id`/`scene_id` 必须可以追溯到来源。允许缺失模态，但必须显式标记缺失；不允许悄悄填入推测值。示例仅作 schema 演示，非真实数据：

```json
{
  "schema_version": "0.1.0",
  "sample_id": "CJ-VMT-JA-000001",
  "parent_video_id": "SRC-00001",
  "scene_id": "SCN-00003",
  "source_registry_id": "RIGHTS-00001",
  "clip": {
    "path": "data/processed/clips/CJ-VMT-JA-000001.mp4",
    "start_ms": 12500,
    "end_ms": 19500,
    "sha256": null,
    "has_target_subtitle_overlay": false
  },
  "audio": {
    "path": "data/processed/audio/CJ-VMT-JA-000001.wav",
    "available": true
  },
  "direction": "ja-zh-Hans",
  "source": {
    "text": "いいよ。",
    "language": "ja",
    "speaker_id": "speaker_01",
    "transcript_status": "human_verified",
    "start_ms": 14800,
    "end_ms": 16100
  },
  "context": {
    "preceding_turns": [],
    "following_turns": [],
    "speaker_relation": "unknown"
  },
  "translation": {
    "reference": null,
    "language": "zh-Hans",
    "status": "pending_human_review",
    "alternatives": []
  },
  "evidence": {
    "visual": [],
    "audio": [],
    "textual": [],
    "evidence_review_status": "pending"
  },
  "labels": {
    "ambiguity_types": [],
    "visual_helpful": "unknown",
    "audio_helpful": "unknown",
    "context_helpful": "unknown",
    "gold_bucket": "unassigned"
  },
  "quality": {
    "annotation_status": "draft",
    "reviewer_ids": [],
    "adjudication_status": "not_started"
  },
  "split": null,
  "provenance": {
    "extraction_method": "manual_or_tool_to_be_filled",
    "annotation_source": "pending",
    "pipeline_version": "0.1.0"
  }
}
```

规范要求：

- 时间单位统一毫秒；`end_ms > start_ms`；字幕文本原文和标准化文本最好分开存。
- 关键帧证据采用 `{timestamp_ms, cue_type, observation, speaker_id, source, confidence}`；语音证据用同构字段。
- `visual_helpful` / `audio_helpful` 使用 `yes/no/uncertain/unknown`，不能把“视频中有表情”自动等同于“视觉帮助翻译”。
- `translation.reference` 在未人工确认前为 `null`；机器翻译候选单独存储，禁止自动升格为金标准。
- `source_registry_id` 引用独立授权台账，包含版权所有者、获取方式、许可范围、再分发权限、相关证据、限制及审核人。
- 尽量不存真实身份敏感信息；仅保留必要的匿名 speaker ID，遵守适用隐私规定。
- 每种导出（训练、人工审核、公开 release）使用 schema adapter，禁止将某训练框架字段写死在核心标注格式中。

## 6. 标注原则：真正证明视频有用

**始终将可观察事实与解释分开。** 例如：

- 可观察：`15.1 秒说话者摇头，并向后退一步`。
- 不可直接当事实：`说话者一定是在拒绝`。后者属于需要上下文共同支持的解释。

建议双层标注：

1. **文本盲评（Text-only）**：标注者只看到原文 + 预定义可用的相邻台词，给出候选译文及歧义点。
2. **多模态复核（Audio/Video-enabled）**：另一轮或独立标注者查看真实音视频，记录可观察证据、最佳译文及改变原因。
3. **独立裁决（Adjudication）**：对核心测试样本至少两名合格中日双语审核者；有分歧的样本进入第三人仲裁。不能以同一个生成模型的自评作为独立人工裁决。
4. **消融可验证性**：分别判定 `Text+Context`、`Text+Audio+Context`、`Text+Video+Context`、`Text+Audio+Video+Context` 中什么信息足以确定恰当译文。
5. **保留无效案例**：若看过视频之后译文并无合理变化，归入常规或负例集合，不可为了增加核心 Benchmark 数量强行标为视频有用。

优先歧义类型：

`ellipsis`（省略）、`pronoun_reference`（指代）、`polysemy`（多义）、`speech_act`（请求/拒绝/讽刺等言语行为）、`emotion_prosody`（语调与情绪）、`register_politeness`（语体与礼貌）、`speaker_identity`（说话者辨识）、`scene_event`（动作/场景事件）。

注意：日语省略与中日文化语用差异往往需要对话前后文；不能把**文本上下文带来的提升**误记为**视频带来的提升**。为此所有模型需使用一致的 `C`。

## 7. 阶段执行协议

按照 `PHASED_GOALS.md` 的 P0→P7 执行，**任何阶段未通过 Gate，不启动依赖它的下游批量任务**：

- P0：研究定义、类别体系、Baseline 设计与数据标准。Gate：任务协议获研究者确认。
- P1：素材来源、许可审查、来源台账。Gate：每条候选素材可追溯且符合预定用途。
- P2：预处理、音频分离、字幕/语音对齐。Gate：20 条端到端处理样本通过自动与抽样人工检查。
- P3：模态证据候选提取、证据时间锚点。Gate：人工可追溯并区分“观察”与“推测”。
- P4：候选译文、双语人审、标注指南。Gate：形成 100–200 条可靠人工审核样本和标注一致性报告。
- P5：分层筛选、去重、数据切分、数据卡。Gate：独立测试集冻结且不存在已知来源泄漏。
- P6：Text-only、Text+Video、Text+Audio、全模态 Baselines，有限 LoRA。Gate：实验可复现，统计和错误分析齐全。
- P7：按类别短板扩展、第二语向选做、版本发布或受限研究共享。Gate：许可、Data Card、复现包、终版论文图表完备。

## 8. 数据源、版权、伦理与安全硬规则

1. `publicly accessible` **不等于** `licensed for download/derivatives/training/redistribution`。分别记录：观看、下载、缓存、裁剪、转写、翻译、训练、衍生使用、公开视频和发布字幕的许可状态。
2. 视频、配音/背景音乐、字幕、官方/粉丝译文可能属于不同权利主体。每个资产分别核验。
3. 优先自制并取得参与者书面同意的短剧、权利清晰的授权素材或确实允许对应使用方式的开放素材。
4. 版权不明的影视/动漫内容可以仅在研究者依法有权使用的范围内处理；严禁默认将其媒体文件放入公开 dataset release。
5. 若只能共享标注元数据和处理代码，公开版本应去除不可再分发的视频、音频、完整字幕及可还原受限内容，明确可访问条件。
6. 对涉及未成年人、私人场景或身份敏感内容应增强审查，避免在没有必要时收集或发布。
7. 任何跨境 API 传输必须核实授权范围、隐私处理方式及适用条款。

## 9. 测试、质量标准与可复现性

必须具备以下检查（阈值先在 P0/P2 试验中确定，不能假装已有统计支持）：

- `schema_check`：字段类型、枚举、空值、时间范围、关联 ID、文件路径。
- `media_check`：ffprobe 可解码、时长有效、字幕与语音范围内存在有效内容。
- `license_check`：权限状态与用途匹配，缺少发布授权的样本不得进入公开版本。
- `alignment_check`：字幕起止边界不越界、说话者识别不确定时允许 unknown。
- `leakage_check`：按 `parent_video_id/scene_id` 分组切分，辅助使用音频指纹、近重复哈希和字幕文本重合检测。
- `overlay_check`：检测是否有直接暴露答案的目标语言烧录字幕或叠加文本，存在时隔离、遮挡并人工复核，或剔除。
- `annotation_check`：人审状态、冲突案例、证据时间锚点、标注一致性、翻译参考来源。
- `benchmark_check`：核心测试集样本的多模态有用性经过独立确认，并包含 Text-only 和音视频控制条件。
- `release_check`：数据清单、许可信息、Data Card、SHA256、版本号、例外与已知限制。

## 10. Baseline 与训练防泄漏约束

模型角色建议（使用前重新核对模型许可、模板与版本）：

- PLaMo 3 Translate 8B：日中纯文本翻译候选及 Text-only Baseline；**不是原生视频理解器**。
- Qwen3-VL-8B：Text+Video Baseline 和短视频 LoRA SFT 候选；音频语用不能仅凭这个模型的视频能力假设已得到处理。
- 支持音频与视频的 Omni 模型：用于 Audio/Video/Full 条件对照；要验证准确的输入预处理与训练栈兼容性。

核心对照必须保持源台词与文本上下文相同：`T`、`T+V`、`T+A`、`T+V+A`；可增加 `T+irrelevant V`、`T+shuffled V` 控制。在不同模型间比较时报告架构差异，避免把模型容量优势当成模态贡献。优先做同骨干的消融实验。

训练目标以目标语言**译文本身**为主；证据解释可作辅助监督，但不可把测试集参考译文、金标准提示或人工视觉标签直接注入测试推理提示词。数据划分先于微调和提示词选择，并冻结版本。

评价：chrF++、适配日中的 COMET（需验证语言支持和相关性）、句子级人工语义忠实度、歧义消除准确率、语用/语气/敬语正确率、Hallucination、错误归因、每模态收益；字幕产品场景再增加时间轴/长度/可读性。报告 bootstrap/paired significance 与类别分层统计，避免只用 BLEU 或少量主观示例下结论。

## 11. 给下游 Agent 的结构化主提示词

以下文本是供编码 Agent 执行具体阶段任务的主提示词；每次仅替换方括号中的变量。不要在没有权限和素材的情况下假定可以执行采集。

```text
# ROLE
你是 CJ-VMT 项目的 Research Engineer / Multimodal Dataset Engineer。
以 AGENTS.md 为强约束，以 PHASED_GOALS.md 为阶段路线图。

# PROJECT MISSION
构建可追溯、有合法使用权、可人工审查的 Ja→Zh-Hans 多模态视频翻译数据集，
用一致的文本上下文对照验证视觉动作、表情、场景和语音语调是否真实改善翻译。

# CURRENT TASK
阶段：[P0/P1/P2/P3/P4/P5/P6/P7]
当前目标：[填写]
可用数据和路径：[填写；未提供就报告缺失]
环境与计算资源：[填写；未提供就不要猜测]
允许操作范围：[只读/可创建脚本/可处理已授权素材/可训练/可发布]

# MANDATORY EXECUTION ORDER
1. 检查仓库实际状态、AGENTS.md 和 PHASED_GOALS.md。
2. 核实当前阶段入口与验收标准；列出最小任务清单。
3. 先实现最小样本流程与自动化验证，再处理批量数据。
4. 所有媒体、字幕、翻译、模态标签都附来源、时间戳、审核状态和异常日志。
5. 对授权不明、时间戳不确定、译文歧义、相互冲突的信息，不得脑补；标记待审。
6. 不使用 test 标签调参，不将目标语言字幕或答案泄漏给模型。
7. 运行实际可用的测试；未运行则明确说明，不伪造成功结果。
8. 在结尾报告产物路径、验证结果、Gate 是否通过及下一任务。

# NON-NEGOTIABLE QUALITY
- 输入原始文件只读。
- 人工参考翻译与自动候选分开。
- 原始视频与衍生字幕的使用和发布权限分别核验。
- 测试集至少两名双语审核者独立复核，分歧需裁决。
- 多模态有用性必须有证据，不得由“视频存在”推定。
- 所有构建步骤可复现、可审计、可回滚。

# OUTPUT FORMAT
当前阶段：
本次计划：
实际完成：
文件与运行命令：
自动/人工验收结果：
许可或质量风险：
Gate 结论（通过/未通过/待审核）：
下一步任务：
```

## 12. 新开项目时的默认第一条任务

如果仓库除两个规划文件外尚无内容，首先完成 P0 中的：

1. 创建项目目录与基础 `.gitignore`；
2. 编写 `docs/research_scope.md` 与 `docs/annotation_guideline.md` 初稿；
3. 编写 `schemas/sample.schema.json`、`schemas/source_registry.schema.json`；
4. 给出 5 条**明确标注为虚构**的 schema 单元测试样本（不可混入真实数据集）；
5. 创建 `scripts/validation/validate_schema.py` 和测试；
6. 提交 P0 Gate 自检报告，并说明哪些内容仍需要研究者批准。

**不要擅自启动视频爬取、付费推理或 GPU 训练。**
