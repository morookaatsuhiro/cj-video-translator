# PHASED_GOALS.md — CJ-VMT 阶段目标、依赖与验收（自动化版）

> 版本：v0.2 | 2026-11-16 | 取代 v0.1（存档：`docs/archive/PHASED_GOALS_v0.1.md`）  
> 当前阶段：**P0（待开始）**。下文的路径、数量和指标都是目标，不代表已经完成。  
> 配套指令：`AGENTS.md` v0.2（流水线 S0–S13、判定规则 §6、人工分工 §2）。修改原因见 `docs/review_2026-11-16.md`。  
> 负责划分：研究者负责授权、Gate 审批和可选的抽检；Agent 负责实现、运行和记录，但不能代替研究者做这些事。

---

## 怎么用

1. **一次只开一个阶段。** 把该阶段末尾的“任务提示”复制给 Agent，Codex 的 `/goal` 或 Claude Code 都可以。
2. **按规则执行。** Agent 按 `AGENTS.md` 工作，是否完成以本文件的 Gate 为准。
3. **阶段结束后审批。** 你阅读 `reports/progress.md` 和该阶段的 Gate 自检报告，大约 10–30 分钟，不需要逐条看样本。确认无误后，把下一阶段的状态改成“进行中”。
4. **状态只用四种**：`未开始`、`进行中`、`受阻`、`完成`。受阻时要写明缺什么，以及对应的 `HUMAN_TASKS` 编号。

| 阶段 | 内容 | 状态 | 人工动作（AGENTS §2） | 关键 Gate 证据 |
|---|---|---|---|---|
| P0 | 研究协议、schema、自动判定规则 | 未开始 | H1 | validator 和分桶真值表测试通过 |
| P1 | 来源与授权台账（按来源审批） | 未开始 | H2、H3 | `approved_sources.yaml` 和台账齐全 |
| P2 | 预处理、双路 ASR、片段（S1–S6） | 未开始 | H4 | 20/20 端到端跑通；完成一个来源的全量 QA |
| P3 | 文本分析、teacher 翻译与证据（S7–S8） | 未开始 | H3、H4 | JSON 合规率、条件隔离、吞吐、各条件分歧率 |
| P4 | 自动验证、分桶、校准（S9–S11） | 未开始 | H4、H5（可选） | 产出率及其置信区间、评审一致性、抽检精确率 |
| P5 | 扩量、去重、切分、冻结、导出（S12–S13） | 未开始 | H6 | test 冻结、零泄漏、功效分析 |
| P6 | Baseline 与 LoRA | 未开始 | H3、H4 | 同骨干消融；`S_T` / `S_TVA` / `S_TVA_shuf` 对照 |
| P7 | 定向扩量、Zh→Ja、发布 | 未开始 | H6 | 版本化数据、Data Card、复现包 |

---

## A. North Star 与交付物

**研究问题**：日→中口语翻译中，遇到省略、指代、言语行为、语调、语体等歧义时，真实视频的动作、表情、场景和语音韵律，能不能提供文本上下文之外的增量信息？这种信息能不能通过**自动构建**的数据教会多模态模型？模型又在什么时候应该忽略视频？

**交付物**

1. 按来源管理权利的原始素材库，仅内部使用，不预设公开再分发。
2. 自动构建的 canonical 数据集：片段、音频、双路转写、前文、各条件下的候选译文、线索、核验结果、分桶、标签等级。
3. 自动验证的 **core benchmark**，以及 general 集和 control 集；外加 WMT25 日→中这个带人工参考的外部测试集。
4. 可复现的流水线 S0–S13，含缓存、幂等、stage_manifest 和 QA 报告。
5. 同骨干的零样本消融、`S_T` / `S_TVA` / `S_TVA_shuf` 三组 LoRA 对照，以及错误分析。
6. Data Card、自动判定说明、抽检报告（如果做了）、授权台账、复现说明。

**成功标准不是数据量，而是以下五点**：输入合法；训练目标“看过视频”；“视频有用”由对照、核验和反事实自动证明；测试集独立且已冻结；自动标签的准确率有数字可查（来自抽检或外部校准）。

---

## B. 依赖关系与规模路径

```text
P0 协议/schema/判定规则 ──Gate0──▶ P1 来源审批 ──Gate1──▶ P2 S1–S6（20条 → 1个来源）
  ──Gate2──▶ P3 S7–S8（2,000条随机 + 定向试点）──Gate3──▶ P4 S9–S11 + 校准
  ──Gate4──▶ P5 扩量 + 切分冻结 ──Gate5──▶ P6 Baseline/LoRA ──Gate6──▶ P7 扩展/发布
```

**规模路径**

1. 先跑通 20 条端到端。
2. 再在至少 5 个 group 上做 2,000 条**随机**试点，外加最多 2,000 条规则挖掘样本。
3. 用试点结果测出 core 产出率，再按 core-test 的功效目标（默认约 250 条，见 AGENTS §11）倒推需要处理多少小时视频、多少算力。
4. 最后扩量。训练用的 silver 数据量由预算决定，例如 2 万–10 万条台词。

**不预设固定的产出比例。** 产出不足时，如实报告数量和筛选率，不放宽规则。

---

## P0 — 研究协议与设计冻结

**目标**：把研究问题、分桶、输入条件和自动判定规则写成可测试的代码和文档，防止后面出现“视频有了但没用”或“自动标签说不清”的情况。

**前置条件**：研究者确定三件事：主方向 Ja→Zh-Hans；预期用途（仅内部、受限研究共享，或拟公开）；是否做 H5 抽检。

**要做的事**

1. 按 AGENTS §17 建立项目骨架，`make check` 能运行。
2. 编写 `docs/research_scope.md`：研究问题 RQ1–RQ4、分桶定义、输入条件表（含与旧 A–F 的对照）、人工预算、不做的事。
3. 编写 `docs/annotation_guideline.md`：本项目的标注全部由模型自动完成，这份文件是自动判定说明，内容包括：
   - C1–C8 的通俗解释，每条配一个正例和一个反例（虚构）；
   - 歧义类型定义；
   - `cue_type` 定义；
   - 抽检页面的操作说明。
4. 编写 `schemas/` 下四个 JSON Schema，以及对应的 pydantic 模型和 `cjvmt validate`。
5. 准备至少 12 条合成 fixture，文件名带 `synthetic`：
   - 至少 6 条合法，覆盖 core、general、control、rejected 各一条以上；
   - 至少 6 条非法，分别是：时间越界、相对时间与绝对时间混用、缺 evidence 时间、`translation.target` 来自纯文本条件、`data/` 里出现 synthetic 样本、`clips_noaudio` 路径缺失。
6. 把 §6 的分桶规则写成纯函数 `bucketize(sample) -> (bucket, reasons)`，并写真值表测试。
7. 写条件隔离和导出防泄漏的测试，用 fixture 和 Mock 即可。
8. 编写 `configs/pipeline.yaml` 和 `configs/models.yaml` 初稿。所有阈值标“默认值，待校准”；模型一栏只写候选，不下载。
9. 在 `docs/HUMAN_TASKS.md` 里写 H1–H3。

**交付物**：`docs/research_scope.md`、`docs/annotation_guideline.md`、`docs/decision_log.md`、`schemas/*.json`、`src/cjvmt/schema.py`、`src/cjvmt/stages/bucket.py`、`tests/`、`configs/*.yaml`、`reports/p0_gate.md`。

**Gate 0 验收**

- [ ] `make check` 通过。
- [ ] 合成 fixture：合法的全部通过，非法的全部被拒，每种错误都能给出正确的原因代码。
- [ ] 分桶真值表：C1–C8 每条规则都有至少 1 个通过用例和 1 个失败用例；control 和 rejected 也有用例。
- [ ] 测试能证明：`T+V` 条件拿不到音频；各条件的提示词只差模态说明；导出的 user 消息里没有 target、readings、bucket。
- [ ] schema 能表达以下几种情况：文本已经够用、视觉有用、音频有用、两者都需要、证据不足、仅靠屏幕文字（OCR）、`label_tier` 的四个取值。
- [ ] 研究者完成 H1：确认铁律、分桶规则、人工预算，以及是否做 H5。

**任务提示**

```text
按 PHASED_GOALS.md 完成 P0 的全部 Gate 0 验收条件。以 make check 的输出、fixture 校验结果和分桶真值表测试为证据。只在本仓库内工作：不下载模型和数据，不调用付费 API，不启动 GPU。
每轮结束在 reports/progress.md 记录改了什么、命令输出摘要和下一步。受阻时写明试过的方法、证据、卡在哪里，以及需要研究者提供什么（写进 docs/HUMAN_TASKS.md）。
```

---

## P1 — 来源发现与授权台账

**目标**：找到合法、可处理、对话密度高、有可观察动作和语气变化的日语视频来源，**按来源审批，不按样本审批**。同时登记外部测试集。

**输入**：P0 的台账 schema 和研究者给出的用途范围。

**要做的事**

1. 列出候选来源，按 AGENTS §5 S0 的优先级排序：
   1. 自有或有书面授权的视频；
   2. 许可证匹配用途的开放许可视频（例如 YODAS 中的 CC BY 视频），需评估平台条款；
   3. 研究许可语料：EVA 视频（发邮件申请）、CEJC（申请，含映像）；
   4. 外部测试集：WMT25 日→中 speech、WMT26 中→日 speech。
2. 写元数据收集脚本。**在获批之前不下载媒体，只取元数据或公开的说明页。**
3. 对每个候选来源做可行性估计：总时长、对话密度（如果能拿到 1–2 分钟样本，就用 VAD 估计）、说话人数、是否有人工日语字幕、硬字幕和テロップ的大致比例。
4. 填写 `manifests/source_registry.csv`，字段包括：
   - 权利人、来源 URI、许可证和授权证据（文件或链接）；
   - 九项操作各自的许可状态：观看、下载、缓存、裁剪、转写、翻译、训练、衍生、发布；
   - 平台条款评估、是否可以再分发；
   - 撤回联系人和处理流程；
   - 审批人和审批日期（由研究者填写）。
5. 外部测试集登记为“仅评测、不再分发媒体”，写好下载脚本。下载超过 500 MB 前，在 HUMAN_TASKS 里请研究者确认。
6. 生成 `docs/data_sources.md` 和 `reports/source_feasibility.md`。

**Gate 1 验收**

- [ ] `configs/approved_sources.yaml` 由研究者批准。批准的来源足够跑完 P2–P4：建议总计至少 10 小时含对话的视频，来自至少 5 个不同的 group。这是建议值，可以按实际授权情况调整。
- [ ] 每个批准的来源在 `source_registry.csv` 里都有完整字段和授权证据。缺少证据的来源不进入批处理。
- [ ] 能明确回答：原视频、音轨和字幕能不能再分发。如果不能，就只规划内部使用或受限使用。
- [ ] 外部测试集已登记，用途写明“仅评测”。
- [ ] `license_check`：`data/raw/` 中没有任何来自未批准来源的文件。

**任务提示**

```text
按 PHASED_GOALS.md 完成 P1。只收集元数据和公开说明，不下载未批准来源的媒体。产出 source_registry.csv 草稿、data_sources.md 和 source_feasibility.md，把需要研究者决定和申请的事项写进 docs/HUMAN_TASKS.md（H2、H3）。
以台账字段完整性检查和 license_check 的输出为证据。每轮结束在 reports/progress.md 记录。
```

---

## P2 — 自动预处理、双路 ASR、片段（S1–S6）

**目标**：把获批的长视频自动转换成“片段、原音视频、无音轨视频、16 kHz 音频、两种形式的文本、前文、屏幕文字标记”都能对应上的标准样本。

**要做的事**

1. 实现 S1–S6：导入、音频、VAD、镜头切分、双路 ASR、幻觉过滤、读音 CER、切句、对齐、说话人、片段、前文、OCR 三分类。
2. 先挑 20 条台词跑通端到端。这 20 条至少来自 3 个来源，并且要覆盖这些情况：多人对话、很短的句子、静音、快切、背景音乐、テロップ。
3. 再对 1 个完整来源跑全量，生成 `reports/preprocessing_qa.md` 和 HTML 预览。
4. 如果有带人工日语字幕的来源，用人工字幕计算自动 ASR 的 CER，作为 ASR 质量的外部估计。
5. 根据 QA 报告校准 τ、时长、字符数等阈值，写进 `decision_log.md`。

**交付物**：`manifests/videos.jsonl`、`manifests/clip_manifest.jsonl`、`data/interim/s1…s6/`、`data/processed/{clips,clips_noaudio,audio,frames}/`、`reports/preprocessing_qa.md`、`reports/rejected/`。

**Gate 2 验收**

- [ ] 20 条端到端全部生成以下 5 样产物，并通过 `schema_check`、`media_check`、`alignment_check`：
  1. `clips`；
  2. `clips_noaudio`；
  3. wav；
  4. `text_asr` 和 `text_neutral`；
  5. 前文。
- [ ] 用 ffprobe 验证，`clips_noaudio` 100% 没有音轨。
- [ ] 全量来源的 QA 报告包含：拒收率及原因分布、双路读音 CER 的分布（中位数和 P90）、`transcript_tier` 分布、OCR 三类比例、片段时长分布、说话人为 `unknown` 的比例。
- [ ] 阈值已校准并记录在 `decision_log.md`。
- [ ] 幂等：同一输入重跑，新增样本数为 0，输出的 sha256 不变。
- [ ] 每个样本都能追溯到 `parent_video_id`、授权条目和绝对时间。

**任务提示**

```text
按 PHASED_GOALS.md 完成 P2：实现 AGENTS.md 的 S1–S6，先用 --limit 跑通 20 条端到端，再对一个获批来源跑全量。
以 schema_check、media_check、alignment_check 的输出，clips_noaudio 无音轨验证，以及 reports/preprocessing_qa.md 为证据。只处理 approved_sources.yaml 中的来源，不修改 data/raw/。每轮结束在 reports/progress.md 记录。
```

---

## P3 — 纯文本分析、teacher 多模态翻译与证据（S7–S8）

**目标**：自动生成“看过视频”的训练目标，以及四种条件下的对照译文和带时间的线索，并测量吞吐和成本。

**前置条件**：H3 已完成（有 GPU 或 API，模型条款已接受）；P2 已通过。

**要做的事**

1. 部署 teacher：默认用本地的 Qwen3-Omni-30B-A3B-Instruct，或用研究者批准的 API。先跑 10 条做冒烟测试，并记录峰值显存。
2. 试点样本：从至少 5 个 group 中**随机**抽 2,000 条台词，用来做无偏估计；另外加最多 2,000 条规则挖掘样本，用来提高产出。两类样本分开统计。
3. 对全部试点样本跑 S7：读法列表、歧义类型，以及至少 2 个不同家族系统给出的 `T_strong`。
4. 对全部试点样本跑 S8 的 `T+V+A` 翻译。之后：
   - **随机试点的 2,000 条全部再跑 `T`、`T+V`、`T+A`**，用于无偏估计产出率和 S7 的召回率；
   - 规则挖掘样本只对候选集跑这三种条件；
   - 两类样本都做证据调用。
5. 跑 `condition_isolation_check` 和 `language_check`。
6. **外部校准**：在 WMT25 日→中 speech 上，用同一个 teacher 分别跑文本条件和 `T+V+A`，以人工参考为准计算 chrF 和 COMET，并和一个强文本系统比较。这一步回答“teacher 本身靠不靠谱”。
7. 统计吞吐和成本：每个样本耗时多少秒、峰值显存、每 1,000 条需要的 GPU 小时或 API 费用。据此估算 P5 需要的算力。

**交付物**：`data/interim/s7_text/`、`data/interim/s8_teacher/`、`reports/teacher_qa.md`、`reports/external_wmt25_teacher.md`、`reports/cost_estimate.md`。

**Gate 3 验收**

- [ ] teacher 输出 JSON 的合规率不低于 98%，报告失败原因。
- [ ] `condition_isolation_check` 100% 通过。
- [ ] 在随机试点样本上报告“各条件读法不一致”的比例，附 95% 置信区间。这个比例是 core 产出率的上界。
- [ ] 线索统计：时间落在窗口内的比例、各 `cue_type` 的分布、屏幕文字类线索的占比。
- [ ] teacher 在 WMT25 日→中上的结果，以及与强文本系统的对比，都已报告。
- [ ] 吞吐、显存、成本表已完成，并据此给出 P5 算力估算。
- [ ] `T+V+A` 训练目标通过 `language_check` 的比例已报告，失败样本已写入 rejected。

**任务提示**

```text
按 PHASED_GOALS.md 完成 P3：实现 S7–S8，先跑 10 条冒烟测试，再跑 2,000 条随机试点和最多 2,000 条规则挖掘样本，两类样本分开统计。
严格执行条件隔离（T+V 用无音轨视频，并关闭 use_audio_in_video）。另外在 WMT25 日→中 speech 上评估 teacher。以 teacher_qa.md、external_wmt25_teacher.md、cost_estimate.md 中的实际数字为证据。不用任何 test 数据调参。每轮结束在 reports/progress.md 记录。
```

---

## P4 — 自动验证、分桶与校准（S9–S11）

**目标**：自动判定哪些样本真正需要视频或音频，并用外部数据（零人工）以及可选的小样本抽检，测出这些自动判定有多准。

**要做的事**

1. 实现 S9：
   - 读法映射；
   - 语义差异判定；
   - 线索核验：视觉线索由异家族模型回答封闭问题；音频线索优先用 F0 等可测量特征；
   - 反事实测试：`CF_V`（同一视频的其他时段）、`CF_A`（F0 拉平）、`CF_VA`；
   - 评审团：至少 2 个家族，各跑 AB 和 BA 两种顺序。
2. 实现 S10 分桶和 S11 质量过滤。
3. **零人工校准**：用 WMT25 日→中的人工评分，计算 QE 和评审模型与人工的一致程度（Kendall τ、成对准确率），据此选定 QE 阈值和评审模型。
4. 在试点数据上运行，生成 `reports/bucket_report.md`，内容包括 AGENTS §6.4 的全部统计，并按歧义类型、归因、来源分层，随机样本和挖掘样本分开报告。
5. **H5 抽检（可选，推荐做）**：用 `cjvmt audit-page` 生成 150 条的抽检页面，抽样方法见 AGENTS §6.5。研究者做完后，计算各项精确率和置信区间。
6. 如果需要调整阈值，只能在 dev 或试点数据上调整，再在**新抽**的样本上重新估计；调整写进 `decision_log.md`。
7. 用 P3 测出的吞吐和本阶段测出的产出率，计算要得到约 250 条 core-test 和对应的 dev 与 train，需要处理多少小时视频、多少算力。

**交付物**：`src/cjvmt/judges/`、`data/interim/s9_verify/`、`data/interim/s10_bucket/`、`reports/bucket_report.md`、`reports/calibration_wmt25.md`、`reports/audit/`（如果做了抽检）、`reports/scale_plan.md`。

**Gate 4 验收**

- [ ] S9–S11 都有单元测试，并在试点数据上跑通。
- [ ] 评审团的位置一致性（同一评审在 AB 和 BA 两种顺序下选择一致的比例）达到 0.8 以上（建议值），并报告评审之间的 κ。
- [ ] 报告 QE 和评审与 WMT25 人工评分的相关，以及阈值的选定依据。
- [ ] 报告随机试点样本上的 core、general、control、rejected 比例及 95% 置信区间，并按类型和归因分层。
- [ ] 所有 core 样本都满足：至少 1 条核验通过的线索、反事实敏感、评审团全票。由自动检查确认，违规数为 0。
- [ ] 抽检：
  - 如果做了，报告 core 精确率和置信区间。达到建议门槛（精确率 ≥ 0.75，置信区间下界 ≥ 0.65），或者给出改进计划，并在新样本上复测；
  - 如果没做，在数据卡里标注 `unaudited`。
- [ ] `scale_plan.md` 给出扩量所需的视频小时数、GPU 小时或费用，以及预计能得到的 core-test 条数和可检出的最小差异。

**任务提示**

```text
按 PHASED_GOALS.md 完成 P4：实现 S9–S11。评审和核验模型必须和 teacher 不同家族，两种顺序都要跑；反事实使用 CF_V、CF_A、CF_VA。
用 WMT25 日→中人工评分校准 QE 和评审，生成 bucket_report.md、calibration_wmt25.md、scale_plan.md。如果研究者选择做 H5，就生成 150 条的抽检页面，并在完成后计算精确率和 Wilson 置信区间。阈值只在 dev 或试点上调，并记录在 decision_log.md。每轮结束在 reports/progress.md 记录。
```

---

## P5 — 扩量、去重、切分、冻结、导出（S12–S13）

**目标**：按 `scale_plan.md` 扩量，形成可以训练、也能严格评测的数据集，并在任何训练开始前冻结测试集。

**要做的事**

1. 对获批来源全量运行 S1–S11。用缓存和 `--resume` 保证不重复调用模型。
2. 跑 S12：近重复检测（音频指纹、帧哈希、文本 MinHash）、按 `group_id` 分组切分（先 test，再 dev，最后 train）、冻结。
3. 跑 S13：
   - 为每个条件导出一份 ms-swift JSONL：`T_neutral`、`T_asr`、`T+A`、`T+V`、`T+V+A`、`T+V_shuf`、`T+A_shuf`；
   - 训练集导出：`train_T`、`train_TVA`、`train_TVA_shuf`；
   - 评测包：core、general、control、自然最小对、反事实对。
4. 用所用 ms-swift 版本对 10 条样本做 dry-run，确认 `T+V+A` 样本里编码进了音频 token。
5. 写 `docs/data_card.md`、`reports/dataset_statistics.md`、`reports/leakage_audit.md`、`reports/rights_audit.md`。
6. 研究者完成 H6：确认冻结 test。

**Gate 5 验收**

- [ ] test、dev、train 之间 group 交叉为 0，近重复交叉为 0。
- [ ] test 中 `target_subtitle` 为 0，`human_audited_fail` 为 0。
- [ ] test 已冻结：有 `split_registry.jsonl` 和 sha256 清单，`make check` 能检出之后的任何改动。
- [ ] 报告 core-test 的实际条数和可检出的最小差异（功效分析）。不足约 250 条时，说明原因和影响。
- [ ] 每个导出文件都通过 dry-run，泄漏检查违规数为 0。
- [ ] `data_card.md` 包含：来源、许可、规模、各 bucket 分布、歧义类型分布、`label_tier` 分布、抽检情况、已知局限。
- [ ] 受限媒体只存放在受控目录；release 目录为空，或只含获准公开的内容。

**任务提示**

```text
按 PHASED_GOALS.md 完成 P5：按 scale_plan.md 扩量，执行 S12–S13。先冻结 test，再划分 dev 和 train，一律按 group_id 切分。
以 leakage_audit.md、split_registry 的 sha256 清单、10 条 ms-swift dry-run 和导出泄漏检查为证据。冻结 test 前在 docs/HUMAN_TASKS.md 请求 H6。每轮结束在 reports/progress.md 记录。
```

---

## P6 — Baseline 与小规模 LoRA

**目标**：回答三个问题。第一，强文本模型已经能翻译到什么程度？第二，不微调时，视频和音频在同一骨干上有没有真实增益？第三，用自动数据微调后，模型是否学会了使用非语言线索？

**前置条件**：P5 通过，test 已冻结；H3 的 GPU 已就绪。

**要做的事**

| 编号 | 实验 | 内容 |
|---|---|---|
| E1 | 同骨干零样本消融 | 用 Qwen2.5-Omni-7B 跑 `T_neutral`、`T_asr`、`T+A`、`T+V`、`T+V+A`、`T+V_shuf`、`T+A_shuf`、`A_only`、`V+A_only`，固定提示词、解码参数、帧率和分辨率 |
| E2 | 级联对照 | `T+desc_auto`，即先自动生成描述，再做文本翻译 |
| E3 | 强文本参照 | 只作参照，不进入训练目标，例如 PLaMo 3 Translate、Qwen3.5 |
| E4 | LoRA 微调 | `S_T`、`S_TVA`、`S_TVA_shuf`：同训练目标、同步数、同种子；预算允许时跑 3 个种子。超参数只看 dev |
| E5 | 外部测试 | WMT25 日→中（人工参考） |
| E6 | 辅助对照（可选） | Qwen3-VL-8B 上的 `T` vs `T+V` |
| E7 | 错误分析 | 按歧义类型和模态归因，自动抽取典型的成功和失败案例，由研究者阅读结论（不需要逐条看） |

**Gate 6 验收**

- [ ] 每个实验都有 `experiments/<run_id>/manifest.json`，记录：commit、数据和 split 版本、种子、GPU、精度、LoRA 配置、帧数和分辨率、峰值显存、耗时、预测文件。做不了的条件写明原因。
- [ ] 同骨干零样本消融表：主指标 DisAcc，加上 chrF、COMET 和鲁棒性指标；附 McNemar（Holm 校正）和 bootstrap 置信区间。
- [ ] 完成 `S_T`、`S_TVA`、`S_TVA_shuf` 三组，并按 AGENTS §12.4 的结论条件给出判断。否定的结论也必须如实写出。
- [ ] 报告外部 WMT25 日→中的结果。
- [ ] 错误分析覆盖每个歧义类型和每种归因，并单独报告被无关视频误导的案例。
- [ ] 成本表：峰值显存、吞吐、每个样本的帧数和 token 数。

**任务提示**

```text
按 PHASED_GOALS.md 完成 P6：先跑 E1 同骨干零样本消融，再跑 E4 的 S_T、S_TVA、S_TVA_shuf 三组 LoRA（同训练目标、同步数、同种子），超参数只看 dev，test 只在最后跑一次。
以 experiments/<run_id>/manifest.json、消融表、McNemar/Holm 和 bootstrap 结果为证据。不能实现的条件如实写明原因。每轮结束在 reports/progress.md 记录。
```

---

## P7 — 定向扩量、Zh→Ja、发布

**目标**：根据 P6 暴露的短板定向补充数据；把方法扩展到中→日方向；按授权情况决定发布范围。

**要做的事**

1. 定向扩量：针对 P6 中容易错的类型和线索（例如短手势、拒绝或道谢、省略主语、语调疑问）调整规则挖掘的权重，然后重新跑流水线。新数据和旧 test 严格隔离；如果需要更新 test，发布新版本，同时保留旧版本，方便比较。
2. Zh→Ja 方向：
   - 用同一套流水线处理中文视频来源（需重新走 P1 审批）；
   - 歧义类型增加敬语、授受、こそあ、彼/彼女等日语侧的强制信息；
   - 用 WMT26 中→日 speech 作外部测试集；
   - 不把日→中数据倒过来当作中→日的 gold。
3. 发布决策（H6）：根据视频、音轨、字幕和衍生物的权利，选择以下之一：全部公开、公开获许可的子集、只公开不能还原受限内容的标注和索引、仅提供受限研究访问。
4. 编写复现包和论文材料：`docs/reproducibility.md`、`reports/final_dataset_report.md`、`reports/final_experiment_report.md`、`docs/paper_outline.md`。

**Gate 7 验收**

- [ ] 数据集的每个版本都能追溯到具体来源、权利和每次处理的参数。
- [ ] 发布的文件符合相应许可，没有泄漏受限媒体，附有 sha256、Data Card、许可文件和撤回渠道。
- [ ] 质量统计、切分、自动标签的准确率（抽检或外部校准）、模态贡献和负例结果都可以复现。
- [ ] 写明数据覆盖范围和外部泛化的局限，例如只覆盖某些来源类型、使用自动银标、存在蒸馏上限。

---

## C. 进度看板

写在 `reports/progress.md` 里，**只填实测值**，没测的写“未统计”。

| 指标 | 当前实际值 | 说明 |
|---|---|---|
| 获批来源数 / 总时长 | 未统计 | P1 |
| S4 之后的台词数 | 未统计 | P2 |
| `transcript_tier` 分布 | 未统计 | P2 |
| 试点中各条件读法不一致的比例（随机样本，95% CI） | 未统计 | P3；core 产出率的上界 |
| core / general / control / rejected 数量 | 未统计 | P4–P5 |
| core 产出率（随机样本，95% CI） | 未统计 | P4 |
| 评审位置一致性 / 评审间 κ | 未统计 | P4 |
| 抽检 core 精确率（95% CI） | 未统计或“未做” | P4（H5） |
| QE 与 WMT25 人工评分的 Kendall τ | 未统计 | P4 |
| core-test 条数 / 可检出的最小差异 | 未统计 | P5 |
| 跨 split 的 group 或近重复泄漏 | 未统计 | P5，目标为 0 |
| test 中目标语烧录字幕 | 未统计 | P5，目标为 0 |
| `S_TVA` 与 `S_T` 在 core-test 上的 DisAcc 差（95% CI） | 未统计 | P6 |

注意区分：不要把“候选数”和“core 数”混为一谈，不要把“`auto_verified`”和“人工验证”混为一谈，也不要把“片段有视频”和“视频有用”混为一谈。

---

## D. 首轮任务清单（现在就能开始：P0）

- [ ] 建立骨架：`pyproject.toml`、`Makefile`、`.gitignore`、`.env.example`、`configs/`、`schemas/`、`src/cjvmt/`、`tests/`。
- [ ] 编写 `docs/research_scope.md`、`docs/annotation_guideline.md`、`docs/decision_log.md`、`docs/HUMAN_TASKS.md`（H1–H3）。
- [ ] 编写四个 JSON Schema、对应的 pydantic 模型和 `cjvmt validate`。
- [ ] 准备至少 12 条合成 fixture（至少 6 条合法、6 条非法）。
- [ ] 实现 `bucketize()` 纯函数，并写 C1–C8 的真值表测试。
- [ ] 写条件隔离和导出防泄漏的测试。
- [ ] 编写 `configs/pipeline.yaml` 和 `configs/models.yaml` 初稿。
- [ ] 写 `reports/p0_gate.md`，提交研究者确认（H1）。

**P0 不下载模型和数据，不调用付费 API，不启动 GPU 任务。**

---

## E. 风险与对策

| 风险 | 严重性 | 对策 |
|---|---|---|
| 训练目标只依赖文本，视频学不到 | 极高 | 铁律 1：训练目标只用 teacher 的 `T+V+A` 译文；纯文本译文只作对照 |
| teacher 编造线索，或看错动作 | 高 | 异家族模型核验、反事实测试、评审团全票、抽检或外部校准 |
| 自动评测循环论证（用 teacher 的译文评学生） | 高 | 主指标用对比式 DisAcc；另用 WMT25 人工参考做外部评测；teacher 家族的模型不担任评审 |
| 影视、动漫、平台内容版权不明 | 极高 | 按来源审批；优先使用自有、授权或研究许可的来源；内部版本和公开版本隔离 |
| core 产出率很低 | 中高 | 规则挖掘提高产出，同时用随机样本做无偏估计；如实报告，不放宽规则 |
| ASR 错误污染原文 | 高 | 双路 ASR 读音 CER 过滤；core 只用 `manual_caption` 或 `auto_agreed` 样本；用人工字幕估计 ASR 质量 |
| 中文烧录字幕泄漏答案；日文テロップ造成混淆 | 极高 | OCR 三分类：target 剔除或遮挡，source 打标记，只靠屏幕文字的样本不进 core |
| 把文本上下文的收益误记为视频的收益 | 极高 | 所有条件共用同一个 `C`；C4 要求 teacher 自身的 `T` 读法不同，且强文本系统不能全部给出该读法 |
| 同一来源切进了 train 和 test | 高 | 按 `group_id` 切分，并做近重复检测 |
| 使用模型或 API 的条款限制 | 中 | PLaMo 的输出默认不进训练目标；使用 API 前核对“输出能否用于训练”和数据跨境条款 |
| 显存或吞吐不足 | 中 | 片段不超过 15 s；关闭 talker；减少帧数；先用 P3 实测再定规模 |

---

## F. 参考资料

见 `AGENTS.md` §3，内容已于 2026-11-16 核对。文献中的语向、发布状态和权利限制和本项目不一定相同，Agent 必须先核实，不能把结论直接搬进本项目的实验。
