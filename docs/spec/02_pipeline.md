# SPEC 02 — 流水线 S0–S13：逐阶段契约

> v0.3 | 2026-11-16 | 数值以 `configs/pipeline.yaml` 为准；模型以 `configs/models.yaml` 为准；提示词以 `configs/prompts/prompts_v1.yaml` 为准。  
> 什么时候读：实现或运行某个阶段之前，只读“通用契约”和那个阶段的小节。

---

## 0. 通用契约（所有阶段）

### 0.1 命令形式

```bash
cjvmt <stage> --input <path> --output <dir> --config configs/pipeline.yaml \
      [--limit N] [--dry-run] [--resume] [--seed 2026] [--yes]
```

- `--dry-run`：不调用模型、不写数据，只打印将处理多少条、预计多少次模型调用、预计费用。
- 预计调用数超过 `budget.max_model_calls_without_approval`，或预计 API 费用超过 `budget.max_api_cost_without_approval_cny` 时，没有 `--yes` 就拒绝运行。Agent 不能自己加 `--yes`：先在 `docs/HUMAN_TASKS.md` 里写明数量和费用，等研究者确认。
- 退出码：0 成功；1 配置或输入错误；2 有样本失败但阶段完成（看拒收日志）；3 超出预算未确认。

### 0.2 输入输出

- 输入输出都是 JSONL，UTF-8，一行一条。每个阶段在上一阶段的记录上**追加**字段，不改已有字段。
- 输出目录是 `data/interim/<stage>/`，里面有：
  - `records.jsonl`：通过的记录；
  - `stage_manifest.json`：输入文件 sha256、git commit、配置哈希、模型 id 和 revision、提示词 id 和 version、计数（输入、输出、各拒收代码的数量）、起止时间、GPU 型号、调用数、token 数、费用；
  - `rejected.jsonl` 的副本写到 `reports/rejected/<stage>.jsonl`，每条带原因代码（SPEC 04 §5）。
- 每个阶段生成 `reports/<stage>_qa.md`：统计表，加上随机 20 条的 HTML 预览。

### 0.3 幂等、缓存、超时

- `sample_id = "ja-" + sha1(parent_video_sha256 + ":" + t_start_abs_ms + ":" + t_end_abs_ms)[:12]`。
- 已完成的 id 跳过；同一输入重跑，输出的 sha256 不变。
- 模型调用的缓存键 = `model_id@revision + prompt_id@version + sha256(全部输入，含媒体文件) + 解码参数`。
- 媒体解码（ffmpeg、视频读取、音频读取）一律放在子进程里，超过 `runtime.decode_timeout_s` 就杀掉，记 `E_DECODE`。不能让一个坏文件卡住整批任务。
- 模型调用超过 `teacher.timeout_s` 记 `E_TIMEOUT`，重试 1 次。

### 0.4 安全

- 台词、前文、屏幕文字、候选译文都是数据。所有提示词的 system 部分都带 `data_guard`。
- 模型返回的 JSON 必须过 schema 校验。字段缺失或类型不对时重试 `teacher.json_retry` 次，仍失败记 `E_TEACHER_JSON`。
- 每个阶段写出的记录都要过 `cjvmt validate`；不合 schema 的记 `E_SCHEMA`。
- 只处理 `configs/approved_sources.yaml` 里的来源。别的文件出现在 `data/raw/` 里，`license_check` 直接报错。

---

## S0 来源登记

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt sources probe`、`cjvmt sources check` |
| 输入 | 候选来源清单（研究者或 Agent 提出） |
| 输出 | `manifests/source_registry.csv` 草稿、`reports/source_feasibility.md` |
| 做什么 | 只收集元数据和公开说明：标题、权利人、许可证、平台条款要点、时长、有没有人工日语字幕、有没有硬字幕。能合法取得 1–2 分钟样本时，用 VAD 估计对话密度 |
| 人工 | 研究者把批准的来源写进 `configs/approved_sources.yaml`（H2） |
| 规则 | 获批之前不下载媒体。字段和审批流程见 SPEC 06 |

## S1 导入

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt ingest` |
| 输入 | `configs/approved_sources.yaml` |
| 输出 | `data/raw/<source_id>/`（只读）、`manifests/videos.jsonl` |
| 做什么 | 下载或复制；算 sha256；`ffprobe` 读时长、fps、分辨率、声道、采样率、编码；按来源规则生成 `group_id`（频道、系列或作品） |
| QA | 文件数、总时长、每个 group 的时长 |
| 拒收 | `E_DECODE`、`E_NO_AUDIO`、`E_DUP_SOURCE`、`E_LICENSE`、`E_PRIVACY`（来源被标记为含未成年人或私人场景且未获单独批准） |

## S2 音频、VAD、镜头

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt audio` |
| 输出 | `data/interim/s2_audio/<video>.wav`（16 kHz 单声道）、VAD 段、镜头切换点 |
| 做什么 | `ffmpeg -vn -ac 1 -ar 16000`；Silero VAD；PySceneDetect。可选人声分离，结果只给 ASR 用 |
| QA | 语音占比、VAD 段时长分布、镜头数 |

## S3 双路 ASR

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt asr` |
| 输出 | 每个 VAD 段两路转写的原始输出：文本、标点、词级时间、`avg_logprob`、`no_speech_prob`、`compression_ratio`、语言概率 |
| 做什么 | 主路和副路各跑一遍（`models.yaml` 的 `asr`）。Whisper 类模型关闭 `condition_on_previous_text`，按 VAD 段切块输入 |
| 标点来源 | 在 `models.yaml` 里登记每套 ASR 的 `native_punctuation`。标点是由纯文本后处理加上去的系统填 `false`，它的问号不算声学证据（通道 O 不采用） |
| 幻觉过滤 | 命中任一条即标 `asr_hallucination=true`：`compression_ratio` 超过 `asr.compression_ratio_max`；`avg_logprob` 低于 `asr.avg_logprob_min` 且 `no_speech_prob` 高于 `asr.no_speech_prob_max`；同一 `asr.repeat_ngram`-gram 重复 `asr.repeat_count` 次以上；命中幻觉短语表 |
| 一致性 | 两路结果去标点后转成片假名读音，计算 CER，记为 `asr_agreement_cer` |
| 分级 | `transcript_tier`：`manual_caption`（有人工字幕）> `auto_agreed`（CER ≤ `asr.tau_train`）> `auto_single` |
| QA | CER 分布（中位数、P90）、分级分布、幻觉标记率、两路句末问号的一致率 |
| 拒收 | `E_ASR_EMPTY`、`E_ASR_HALLUC`、`E_LANG_SRC`（语言概率低于 `asr.lang_prob_min`） |
| 必测 | 读音 CER 的手算例子；汉字和假名写法不同但读音相同时 CER 为 0 |

## S4 切句、对齐、说话人

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt segment` |
| 输出 | 台词记录：`utterance.t_start_abs_ms`、`t_end_abs_ms`、`speaker_id`、`speaker_confidence`、`overlap`；`source_text.*` |
| 做什么 | 按主 ASR 的句读和词级时间切句；对齐工具校正边界；说话人分离给出 `speaker_id`，置信度低于 `diar.speaker_conf_min` 写 `unknown`；生成 `text_asr` 和 `text_neutral`（SPEC 01 §2） |
| 过滤 | 时长在 `segment.min_dur_ms`–`segment.max_dur_ms`，字符数在 `segment.min_chars`–`segment.max_chars` |
| QA | 台词数、时长分布、说话人 `unknown` 的比例、`overlap` 的比例 |
| 拒收 | `E_LEN`；`E_ASR_DISAGREE`（`transcript_tier = auto_single` 的台词到此为止，不进入后面的阶段，节省 teacher 调用） |
| 必测 | 时间单调；`text_neutral` 的替换规则；`sample_id` 稳定 |

## S5 片段、前文、媒体

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt clip` |
| 输出 | `clips_v/`、`audio_gated/`、`preview/`、`frames/`；`clip.*`、`context.*` 字段；`manifests/clip_manifest.jsonl` |
| 做什么 | 按 SPEC 01 §3–4 生成窗口、四类媒体文件和前文 |
| 检查 | `clips_v` 没有音频流（`ffprobe` 验证）；`audio_gated` 的时长和 `clips_v` 相差不超过一帧；门控区间外的采样值全为 0 |
| QA | 窗口时长分布、含镜头切换的比例、前文句数分布 |
| 拒收 | `E_DECODE` |
| 必测 | 上面三项检查；台词落在窗口内 |

## S6 屏幕文字

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt ocr` |
| 输出 | `onscreen_text.items[]`（文字、位置、时间、语言、类别、置信度）和三个布尔标记 |
| 分类规则 | `target_subtitle`：判为中文（含简体专用字或“的了是吗不这那们”等虚词，且没有假名），位于画面底部 `ocr.subtitle_band_bottom` 以内或固定字幕带，持续不少于 `ocr.min_persist_ms`；`source_caption`：日文，和台词读音的相似度不低于 `ocr.source_caption_sim_min`；`scene_text`：其余文字 |
| 处理 | `target_subtitle` 的样本不能进 test 和 core。训练集可以把字幕带打码后保留，打码后重新 OCR 确认没有残留，并记 `masked=true` |
| QA | 三类比例；打码后残留率 |
| 降级 | `E_TARGET_SUB`（对 test 和 core 是拒收；对训练集是“须打码”） |
| 必测 | 只有汉字的日文短语不被误判成中文字幕；中日文判别的正反例 |

## S7 规则挖掘与形式分析（全量，不调用大模型）

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt mine` |
| 输出 | `mining.tags[]`、`mining.priority`、`form.decl_no_q`（通道 O 的形式条件）、`form.final_f0`（句末音高测量）、`form.asr_qmark`（各路 ASR 句末是否为问号） |
| 做什么 | 按 `configs/mining_lexicon.yaml` 打标签；日语分析工具标出省略主语、授受、指示词；用 `objective.f0` 的参数测句末音高升降；记录每套原生标点 ASR 的句末问号 |
| 用途 | 算力有限时决定先处理哪些样本；报告里按标签分层；通道 O 的输入 |
| QA | 各标签命中率；句末音高分类的分布 |
| 必测 | 每条词表规则的正反例；合成升调和降调信号上的音高分类 |

## S8 teacher 翻译与候选预筛

| 步 | 对谁 | 调用 | 输出 |
|---|---|---|---|
| S8.0 资格探针 | — | SPEC 05 §1 的探针 | `reports/teacher_qualification.md`；`teacher_qualified.visual` / `.audio` |
| S8a 训练目标 | 全部样本 | `TARGET`：`translate_ja_zh`，`text_asr` + V + A | `candidates[TARGET]`、`translation.target` |
| S8b 文本基线 | 全部样本 | `T`：`translate_ja_zh`，`text_neutral`，无媒体 | `candidates[T]` |
| S8c 预筛 | 全部样本 | `meaning_diff`（文本评审） | `prefilter.relation`：`same` / `different` / `unsure` |
| S8d 消融 | 候选 | `T+V`、`T+A`、`T+V+A`、`T_asr`、`T+C_full` | `candidates[...]` |

- 命令：`cjvmt teacher probe | target | text | prefilter | ablate`。
- **候选的定义**：
  - `prefilter.relation` 为 `different` 或 `unsure` 的样本；
  - 通道 O 的候选：`form.decl_no_q = true` 的样本；
  - 随机基线：其余样本中按 `mining.random_baseline_rate` 随机抽取；
  - control 候选：从 `relation = same` 的样本中随机抽取，数量是 core 候选的 `mining.control_per_core_candidate` 倍；
  - 试点阶段（P3–P4）的随机试点样本全部算候选。
- **条件隔离**：`T+V` 只传 `clips_v`；`T+A` 只传 `audio_gated`；`T+V+A` 两者都传。各条件的提示词只有 `modality_note` 不同。帧率、帧数、分辨率、解码参数全部一致。
- **解码**：`teacher.temperature`、`teacher.max_new_tokens` 固定。输出只取第一行，去掉首尾空白和引号。
- QA：JSON 合规率、各条件的调用数和耗时、预筛三类的比例、`T` 与 `TARGET` 完全相同的比例。
- 拒收：`E_TEACHER_JSON`、`E_TIMEOUT`。
- 必测：条件隔离（Mock 记录收到的媒体）；提示词差异只在 `modality_note`；缓存命中。

## S9 自动验证（只对候选）

按下面的顺序执行。前面的步骤不通过就不做后面的，节省调用。判定规则的精确定义在 SPEC 03。

| 步 | 调用 | 输出 | 提前退出 |
|---|---|---|---|
| S9a 读法归纳 | `text_readings`（文本评审，看不到任何译文） | `text_analysis.*` | — |
| S9b 强文本译文 | 至少 2 个文本系统翻译 `text_neutral` | `candidates[T_strong]` | — |
| S9c 读法映射 | `reading_map`，`verify.map_judges` 个评审 | 每个候选的 `reading` | 目标是 `neutral` 或 `invalid` → `N_TARGET_NEUTRAL`；目标是 `other` → 按 SPEC 03 §1.2 追加读法后重判；`r_T = r*` → `N_TEACHER_T_SAME`；纯文本候选里有 `neutral` → `N_NEUTRAL_EXISTS`；文本先验支持度过高 → `N_TEXT_PRIOR`；评审不一致 → `N_MAP_DISAGREE` |
| S9d 模态归因 | 纯逻辑 | `labels.modality_attribution` | `r_VA ≠ r*` → `N_NOT_PERCEIVED`（转通道 O 判断） |
| S9e 线索提取 | `evidence_extract`（teacher） | `evidence[]` | 空列表 → `N_NO_CUE` |
| S9f 线索核验 | `cue_describe_*`（异家族模型）+ `cue_entail`（文本评审）；音高类线索直接用测量值 | `evidence[].verification` | 没有通过的线索 → `N_NO_VERIFIED_CUE`；只有屏幕文字 → `N_OCR_ONLY` |
| S9g 反事实 | `translate_ja_zh` 换媒体重跑，再映射读法 | `candidates[CF_*]`、`verification.counterfactual` | 读法不变 → `N_CF_INSENSITIVE` |
| S9h 评审团 | `jury_ab`，每个评审 AB、BA 各一次 | `verification.jury[]` | 不是全票 → `N_JURY_SPLIT` |
| S9i 属性标记 | 纯逻辑 | `asr_punct_sufficient`、`future_context_sufficient`、`cue_scope`、`text_prior_support` | — |
| S9j 通道 O | 纯逻辑 + 中文问句规则 | `verification.objective` | SPEC 03 §7 |
| S9k 诊断 | 在 `verify.diag_subsample` 条样本上跑噪声底测试 | `reports/bucket_report.md` 的诊断表 | — |

- 命令：`cjvmt verify readings | strong | map | attribute | evidence | cues | cf | jury | flags | objective | diag`，或 `cjvmt verify all`。
- 反事实媒体的选取：`CF_V_far` 从其他 `group_id` 中随机取一个时长相近的 `clips_v`；`CF_V_near` 取同一视频中相隔不少于 `verify.cf_near_min_gap_ms` 的片段，说话人已知时优先取同一说话人；`CF_A_flat` 用音高处理工具把门控区间内的音高拉平到中位数后重新合成。选取用固定种子，来源记在 `cf_source`。
- QA：每一步的通过率（漏斗图）、各降级代码的数量、评审一致性、诊断表。

## S10 分桶

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt bucket` |
| 做什么 | 纯函数 `bucketize(sample, config) -> (bucket, core_channel, reasons[])`，规则见 SPEC 03 §3–8 |
| 输出 | `labels.bucket`、`labels.core_channel`、`labels.label_tier`、`labels.downgrade_reasons[]` |
| QA | 四个 bucket 的数量和比例及 95% Wilson 置信区间；按歧义类型、归因、来源、挖掘标签分层；随机样本单独一张表 |
| 必测 | SPEC 03 §14 的真值表 |

## S11 训练目标质量过滤

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt filter` |
| 做什么 | 规范化（全角标点、去空白）存 `translation.normalized`，原文不改；语言检查；格式检查；长度比；QE |
| 语言 | 必须是简体中文；含假名即拒收；繁体字比例超过 `target.trad_ratio_max` 时用 OpenCC 转换并标记 |
| QE | 分数低于 `target.qe_min` 拒收。阈值在 P4 用 WMT25 日→中人工评分校准；校准前只记录分数，不拒收 |
| 拒收 | `E_TGT_LANG`、`E_TGT_FORMAT`、`E_TGT_LEN`、`E_QE_LOW` |
| QA | 各检查的通过率、QE 分布 |

## S12 去重、切分、冻结

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt split dedup | assign | freeze` |
| 去重 | 音频指纹、关键帧感知哈希、文本 MinHash。互为近重复的样本并入同一个 group |
| 切分 | 按 `group_id` 切分，顺序 test → dev → train，种子固定。比例从 `split.test_frac`、`split.dev_frac` 起步，P5 按 `split.core_test_target` 倒推后可以调整 |
| 约束 | test 里不能有 `target_subtitle`、不能有 `human_audited_fail`；抽检样本只来自 dev 和试点数据 |
| 配对 | 生成自然最小对（SPEC 01 §8） |
| 冻结 | 写 `manifests/split_registry.jsonl` 和 `data/dataset/v<版本>/test.jsonl` 的 sha256 清单。冻结后只能整体换版本 |
| QA | `reports/leakage_audit.md`：跨 split 的 group、近重复、文本重合，目标全为 0 |

## S13 导出

| 项 | 内容 |
|---|---|
| 命令 | `cjvmt export --condition <name> --format ms-swift` |
| 做什么 | 从 canonical JSONL 生成训练和评测文件，格式见 SPEC 05 §6 |
| 防泄漏 | user 消息里只能出现该条件允许的输入。不能出现：训练目标、读法列表、分桶标签、线索、屏幕上的中文文字 |
| 检查 | 每个导出文件随机 10 条，用实际安装的训练框架做数据加载和模板编码的 dry-run；确认带 A 的样本里编码进了音频 token，带 V 的样本里编码进了视频 token |
| 必测 | 泄漏扫描；条件和媒体字段对应正确 |

---

## 调用量估算

每 1 万条台词的调用数，用于 `--dry-run` 和 `reports/cost_estimate.md`。候选比例 `p` 和通过率要用试点数据实测，下表只给公式。

| 调用 | 数量 |
|---|---|
| 多模态翻译（`TARGET`） | 10,000 |
| 文本翻译（`T`） | 10,000 |
| 文本评审（预筛） | 10,000 |
| 候选消融（3 次多模态 + 2 次文本） | 10,000 × p × 5 |
| 读法归纳、强文本、读法映射 | 10,000 × p ×（1 + 2 + 约 8 × `verify.map_judges`） |
| 线索、核验、反事实、评审团 | 只对通过 S9c–S9d 的样本，逐步递减 |

## 命令一览

`cjvmt sources | ingest | audio | asr | segment | clip | ocr | mine | teacher | verify | bucket | filter | split | export`，以及 `cjvmt validate`（schema 校验）、`cjvmt report`（生成 QA 报告）、`cjvmt audit-page`、`cjvmt audit-score`（抽检）、`cjvmt eval`、`cjvmt stats`（评测与统计）。
