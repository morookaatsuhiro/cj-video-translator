# PHASED_GOALS.md — CJ-VMT 阶段目标、里程碑与验收

> **存档说明（2026-11-24）**：这是数据集完整建设计划（v0.3），结课后继续用。现在根目录的 `AGENTS.md`、`GOALS.md` 是结课冲刺版（MVP）。
> 恢复方法见 `docs/roadmap/README.md`。本文件里提到的 `AGENTS.md`、`PHASED_GOALS.md`、`docs/HUMAN_TASKS.md`，指的是本目录下对应的 `*_full_v0.3.md`。

> v0.3 | 2026-11-16 | 上一版存档：`docs/archive/PHASED_GOALS_v0.2.md`  
> 当前阶段：**P0（未开始）**。下文的数量和指标都是目标，不代表已经完成。  
> 配套：`AGENTS.md`（规则）、`docs/spec/`（详细规范）、`configs/`（阈值、模型、提示词）。  
> 阅读方法：只读“怎么用”、总表和当前阶段那一节。

---

## 怎么用

1. **一次只开一个阶段，阶段内一次只做一个里程碑。** 把该阶段末尾的“任务提示”交给 Agent。用 Codex 时放在 `/goal` 后面（需要 0.128.0 以上；命令不出现时在 `~/.codex/config.toml` 的 `[features]` 下加 `goals = true`）；用 Claude Code 时直接粘贴。
2. **每个里程碑都有“完成证据”。** 证据是命令的输出或报告里的数字，不是 Agent 的口头说明。
3. **阶段结束后由你审批。** 读 `reports/progress.md` 和该阶段的 Gate 自检报告，大约 10–30 分钟，不需要逐条看样本。确认后，由你把下一阶段的状态改成“进行中”。
4. **状态只用四种**：`未开始`、`进行中`、`受阻`、`完成`。
5. **Gate 没通过时**：看该阶段的“没通过怎么办”。总原则是先查原因、改规则或换模型，然后在新数据上重测；不放宽判定规则，不带着问题进入下一阶段。

| 阶段 | 内容 | 状态 | 你要做的事 | 关键证据 |
|---|---|---|---|---|
| P0 | 协议冻结、骨架、schema、分桶函数、Mock 冒烟 | 未开始 | H1 | `make check`、`make smoke`、真值表测试 |
| P1 | 来源与授权、外部数据、模型清单 | 未开始 | H2、H3 | 台账、`approved_sources.yaml` |
| P2 | 预处理：S1–S6 | 未开始 | H4 | 20 条端到端、单来源 QA |
| P3 | 资格探针、规则挖掘、teacher 翻译与预筛：S7–S8 | 未开始 | H3、H4 | 探针结果、试点分歧率、成本 |
| P4 | 自动验证、分桶、校准：S9–S11 | 未开始 | H4、H5（可选） | 产出率、诊断指标、抽检精确率 |
| P5 | 扩量、切分、冻结、导出：S12–S13 | 未开始 | H3、H6 | 冻结清单、零泄漏、功效 |
| P6 | 评测与 LoRA | 未开始 | H3、H4 | 同骨干消融、三个学生的对照 |
| P7 | 定向扩量、中→日、发布 | 未开始 | H6 | 版本化数据、Data Card、复现包 |

**单个里程碑的任务提示模板**（阶段末尾的提示太大时用这个）：

```text
按 PHASED_GOALS.md 完成里程碑 [M?.?]，以该里程碑“完成证据”一栏列出的命令输出为证据。遵守 AGENTS.md。
只做这个里程碑；先读 AGENTS.md §2 指向的规范小节。
先用合成 fixture 和 Mock 跑通，再 --limit 小批量，再 --dry-run 看调用量，超过 budget 门槛先写进 docs/HUMAN_TASKS.md 等我确认。
结束时 make check 通过，并在 reports/progress.md 追加记录。受阻时停下来，写明试过的方法、证据、卡在哪里、需要我提供什么。
```

---

## A. North Star 与交付物

**研究问题**：日→中口语翻译遇到省略、指代、言语行为、语调等歧义时，真实视频里的动作、表情、场景和语音韵律，能不能提供文字上下文之外的信息？这种信息能不能通过**自动构建**的数据教给多模态模型？模型什么时候应该忽略视频？

**交付物**

1. 按来源管理权利的原始素材库（仅内部）。
2. 自动构建的数据集：片段、门控音频、双路转写、前文、各条件的候选译文、线索及核验、分桶、属性标记。
3. 自动验证的 core benchmark，加上 general、control、自然最小对；外加 WMT25 日→中这个带人工参考的外部测试集。
4. 可复现的流水线 S0–S13，带缓存、manifest 和 QA 报告。
5. 同骨干零样本消融；`S_T`、`S_TVA`、`S_TVA_shuf` 三个学生的对照；错误分析。
6. Data Card、自动判定说明、资格探针报告、抽检报告（如果做了）、授权台账、复现说明。

**成功标准不是数据量，而是这五点**：来源合法；训练目标“看过视频”；“有用”由对照、核验和反事实证明；测试集独立并已冻结；自动标签的准确率有数字可查。

---

## B. 依赖关系与规模估算

```text
P0 ─Gate0→ P1 ─Gate1→ P2（20 条 → 1 个来源）─Gate2→ P3（探针 → 2,000 条随机试点 + 规则挖掘样本）
   ─Gate3→ P4（验证、分桶、校准）─Gate4→ P5（扩量、冻结）─Gate5→ P6（评测、LoRA）─Gate6→ P7
```

**规模怎么定**：不预设产出比例，用 P2–P4 的实测值倒推。

| 量 | 记号 | 在哪里测 |
|---|---|---|
| 每小时视频的可用台词数 | `u` | P2 |
| 候选比例（预筛为 `different` 或 `unsure`） | `p` | P3 |
| 随机样本上的 core 产出率 | `y` | P4，附 95% 置信区间 |
| 规则挖掘样本上的 core 产出率 | `y_m` | P4 |
| teacher 每条样本的耗时和显存 | — | P3 |

- 进入 test 的视频小时数 ≈ `split.core_test_target ÷ (u × y_eff)`，其中 `y_eff` 是 test 组实际处理的样本上的产出率。
- 举例（**假设值，仅说明算法**）：`u = 500`、`y_eff = 1.5%` 时，每小时约 7.5 条 core，250 条 core-test 需要约 33 小时视频进入 test 组。
- test 组占多大比例由这个倒推结果决定，不必固定在 `split.test_frac`。
- 产出不够时如实报告数量和“当前规模能检出的最小差异”，不放宽规则。

---

## P0 — 协议冻结、骨架、schema、分桶函数

**目的**：把规范变成能跑、能测的代码骨架。这个阶段不碰真实数据和真实模型。

**进入条件**：仓库里已有 `AGENTS.md`、本文件、`docs/spec/`、`configs/`。

| 里程碑 | 内容 | 交付物 | 完成证据 |
|---|---|---|---|
| M0.1 骨架 | `git init`；`pyproject.toml`、`Makefile`、`.gitignore`、`.env.example`、`README.md`、`NOTICE.md`；`src/cjvmt/` 骨架；`cjvmt` 的全部子命令先做成占位 | 这些文件 | `make setup`、`make check` 通过；`cjvmt --help` 列出 AGENTS §8 的全部子命令；`git check-ignore data/x .env` 都被忽略 |
| M0.2 配置与提示词 | 加载并校验 `pipeline.yaml`、`models.yaml`；提示词加载、渲染、算哈希；一致性检查器 | `config.py`、`prompts.py`、`scripts/check_docs.py` | 测试通过：文档里用反引号写的配置键都存在；引用的提示词 id 都存在；`AGENTS.md` 小于 32768 字节；各翻译条件的提示词只差 `modality_note` |
| M0.3 schema 与 validator | 按 SPEC 04 写 pydantic 模型，导出 JSON Schema；`cjvmt validate`；合成 fixture | `schema.py`、`schemas/*.json`、`tests/fixtures/*synthetic*` | 合法 fixture 不少于 6 条（覆盖通道 M 的 core、通道 O 的 core、陈述对照、普通 control、general、rejected），全部通过；非法 fixture 不少于 10 条，SPEC 04 §7 的每条规则各对应一条，全部被拒且原因正确 |
| M0.4 分桶函数 | 纯函数 `bucketize(sample, config)` | `stages/bucket.py` | SPEC 03 §14 的 T01–T24 每行至少一个测试，全部通过 |
| M0.5 Mock 冒烟 | 确定性的 Mock 后端（ASR、teacher、评审、核验）；`make smoke` 用合成 fixture 跑通 S4–S13 | `backends/mock.py`、冒烟配置 | `make smoke` 生成各阶段的 `stage_manifest.json`、分桶报告和导出文件；测试证明：`T+V` 拿不到音频、`T+A` 拿不到画面；第二次运行后端调用数为 0；导出的用户消息里没有答案、读法、标签、线索；重跑输出的 sha256 不变 |
| M0.6 文档 | `docs/research_scope.md`；`docs/annotation_guideline.md`（自动判定的通俗说明、每类歧义的虚构正反例、抽检页面操作说明）；`docs/data_card.md` 模板 | 这些文档、`reports/p0_gate.md` | 文档里的例子都标明“虚构”；Gate 自检报告逐条列出证据 |

**Gate 0**

- [ ] `make check` 和 `make smoke` 通过。
- [ ] M0.2–M0.5 的测试全部通过，测试数量和覆盖的规则写在 `reports/p0_gate.md`。
- [ ] schema 能表达：文本已经够用、视觉有用、音频有用、两者都需要、仅靠屏幕文字、中性译法存在、通道 O 的疑问和陈述、`label_tier` 的四个取值。
- [ ] 没有下载任何模型或数据，没有调用任何付费 API，没有启动 GPU 任务。
- [ ] H1：你确认五条铁律、分桶规则、人工预算，以及是否做 H5 抽检。

**没通过怎么办**：这个阶段只有工程问题，修到通过为止。如果你对规则本身有异议（例如不想要通道 O），在 H1 里提出；Agent 修改 `configs/pipeline.yaml` 和规范，并记入 `docs/decision_log.md`。

**任务提示**

```text
按 PHASED_GOALS.md 完成 P0，逐个里程碑推进（M0.1 → M0.6），每完成一个就在 reports/progress.md 记录并停下来检查 make check。
以 make check、make smoke 的输出，以及 fixture 校验和分桶真值表测试的结果为证据。
只在本仓库内工作：不下载模型和数据，不调用付费 API，不启动 GPU。不要修改 docs/spec/ 里的规则；发现规范自相矛盾时写进 docs/decision_log.md 并停下来问我。
受阻时写明试过的方法、证据、卡在哪里、需要我提供什么（写进 docs/HUMAN_TASKS.md）。
```

---

## P1 — 来源、授权、外部数据、模型清单

**目的**：确定哪些视频可以合法处理，登记外部测试集和探针数据，选定每个角色用什么模型。按来源审批，不按样本审批。

**进入条件**：Gate 0 通过。

| 里程碑 | 内容 | 交付物 | 完成证据 |
|---|---|---|---|
| M1.1 候选来源 | 按 SPEC 06 §3 的类型列出候选；只收集元数据和公开说明，不下载媒体 | `manifests/source_registry.csv` 草稿 | 每个候选的台账字段（除审批列）填写完整；证据存入 `docs/license_evidence/` |
| M1.2 可行性 | 估计每个来源的总时长、对话密度、说话人数、有没有人工日语字幕、烧录字幕和日文字幕卡的大致比例 | `reports/source_feasibility.md` | 报告里每个数字都注明是怎么得到的（元数据、公开说明，或合法取得的样本） |
| M1.3 审批材料 | 在 `docs/HUMAN_TASKS.md` 的 H2 里列出每个来源的建议、风险和九项操作的状态 | `docs/data_sources.md` | 你逐个来源做出决定，写入审批列和 `configs/approved_sources.yaml` |
| M1.4 外部数据 | 登记 WMT25 日→中、WMT26 中→日（仅评测）和 CoMMuTE 英→中（仅探针）；写下载脚本，带哈希校验 | `scripts/download_*.sh`、`docs/data_cards/` 下的数据卡 | 下载超过 `budget.download_confirm_mb` 的已请你确认；数据卡写明用途和限制 |
| M1.5 模型清单 | 为 `configs/models.yaml` 的每个角色选定候选：查模型卡，核对许可、输入模态、是否支持日语、显存；确认评审团满足“至少两个非 teacher 开发方、至少一个非 Qwen 骨干” | 更新后的 `models.yaml`、`NOTICE.md` | 每个角色有 id、许可和核对日期；需要你接受条款或提供算力的项目列在 H3 |

**Gate 1**

- [ ] `configs/approved_sources.yaml` 由你批准。建议总量不少于 10 小时含对话的视频，来自不少于 5 个 group（建议值，可按实际授权调整）。
- [ ] 每个批准的来源在台账里字段完整、有授权证据；`allow_train` 不是 `yes` 的来源标为仅评测。
- [ ] 能明确回答：原视频、音轨、字幕能不能再分发。不能的只规划内部使用。
- [ ] 外部数据已登记为“仅评测”或“仅探针”。
- [ ] `models.yaml` 里 ASR、teacher、文本评审、多模态核验、评审团、QE、学生各角色都有候选，许可已核对。
- [ ] H3：算力方案（本地 GPU 型号和数量，或 API）已由你确认。
- [ ] `license_check` 通过：`data/raw/` 里没有未批准来源的文件。

**没通过怎么办**

| 情况 | 处理 |
|---|---|
| 暂时没有可批准的训练来源 | 三个选项由你决定：自己录制小规模对话（参与者书面同意）；申请研究许可语料（EVA 视频、CEJC），等待期间继续开发工具；先只做评测向的 benchmark |
| 申请还在等待 | P2–P4 的工具可以先在外部测试集上开发和调试，因为这些数据本来就要过同一套流水线来做外部评测；它们的样本永远不进训练集 |
| 找不到满足“非 Qwen 骨干”的本地评审模型 | 列出闭源 API 选项和费用，按 SPEC 06 §5 核对后由你决定；或接受评审团只有一个家族，并在数据卡里写明这个局限 |

**任务提示**

```text
按 PHASED_GOALS.md 完成 P1（M1.1 → M1.5）。只收集元数据和公开说明，不下载任何未获批来源的媒体。
产出台账草稿、source_feasibility.md、data_sources.md、外部数据的下载脚本和数据卡、更新后的 configs/models.yaml 和 NOTICE.md。
把需要我决定的事写进 docs/HUMAN_TASKS.md（H2 来源审批、H3 算力和条款）。以台账字段完整性检查和 license_check 的输出为证据。
许可和模型能力以官方页面为准，查不到就写“未核实”，不要猜。
```

---

## P2 — 自动预处理：S1–S6

**目的**：把获批的长视频变成标准样本：无声画面、门控音频、两种文本、前文、屏幕文字标记，彼此对得上。

**进入条件**：Gate 1 通过；ASR 所需的算力可用。

| 里程碑 | 内容 | 完成证据 |
|---|---|---|
| M2.1 S1–S2 | 导入、sha256、`ffprobe`、`group_id`；音轨、VAD、镜头切换 | `manifests/videos.jsonl`；解码放在带超时的子进程里（有测试） |
| M2.2 S3 | 双路 ASR；幻觉过滤；读音 CER；登记每套 ASR 的标点来源 | 读音 CER 的测试；`reports/asr_qa.md` 有 CER 分布和句末问号一致率 |
| M2.3 S4 | 切句、对齐、说话人、`text_asr` 和 `text_neutral` | 时间单调、`sample_id` 稳定的测试 |
| M2.4 S5 | 四类媒体文件、前文和后文 | `ffprobe` 验证 `clips_v` 无音轨；门控区间外采样为 0；`audio_gated` 与画面等长 |
| M2.5 S6 | OCR 三分类；训练集字幕打码并复查 | 中日文判别的正反例测试；打码后残留率 |
| M2.6 20 条端到端 | 至少来自 3 个来源，覆盖：多人对话、很短的句子、静音、快切、背景音乐、日文字幕卡 | 20 条全部通过 `schema_check`、`media_check`、`alignment_check` |
| M2.7 单来源全量 | 一个完整来源跑 S1–S6；校准阈值；验证幂等 | `reports/preprocessing_qa.md` 和 20 条 HTML 预览 |

**Gate 2**

- [ ] 20 条端到端全部成功；每条都能追溯到父视频、授权条目和绝对时间。
- [ ] `clips_v` 100% 没有音轨；`audio_gated` 100% 通过门控检查。
- [ ] QA 报告包含：各拒收代码的数量、读音 CER 的中位数和 P90、`transcript_tier` 分布、OCR 三类比例、片段时长分布、说话人 `unknown` 的比例、每小时视频的可用台词数 `u`、处理速度。
- [ ] 如果有带人工日语字幕的来源：报告自动 ASR 相对人工字幕的 CER。
- [ ] `asr.tau_core`、`asr.tau_train`、`segment.*`、`ocr.*` 已按 QA 结果校准，记入 `decision_log.md`。
- [ ] 幂等：同一输入重跑，新增样本数为 0，输出 sha256 不变。
- [ ] `reports/cost_ledger.csv` 已记录本阶段的 GPU 小时。

**没通过怎么办**

| 现象 | 先查什么 | 处理 |
|---|---|---|
| 两路 ASR 一致的台词不到一半 | 背景音乐、多人重叠、音质 | 打开人声分离再识别；换副路 ASR；这个来源降低优先级 |
| 幻觉标记率高 | 静音段、片头片尾 | 收紧 VAD；扩充幻觉短语表 |
| OCR 把日文误判成中文字幕 | 只有汉字的短语 | 调整判别规则并补测试 |
| 片段常被镜头切换打断 | 剪辑节奏快的来源 | 记录 `scene_cut_in_window`，不拒收；这类来源的视觉线索可能少，写进可行性结论 |

**任务提示**

```text
按 PHASED_GOALS.md 完成 P2（M2.1 → M2.7）：实现 docs/spec/02_pipeline.md 的 S1–S6。
先用合成 fixture 和 Mock 测试，再用 --limit 跑通 20 条端到端，最后对一个获批来源跑全量。
以 schema_check、media_check、alignment_check 的输出，clips_v 无音轨和门控检查的结果，以及 reports/preprocessing_qa.md 为证据。
只处理 configs/approved_sources.yaml 里的来源，不修改 data/raw/。阈值的调整记入 docs/decision_log.md。
```

---

## P3 — 资格探针、规则挖掘、teacher 翻译与预筛：S7–S8

**目的**：先确认 teacher 会用声音和画面；再生成“看过视频”的训练目标和各条件的对照译文；测出候选比例和成本。

**进入条件**：Gate 2 通过；H3 已提供 teacher 所需的算力，模型条款已接受。

| 里程碑 | 内容 | 完成证据 |
|---|---|---|
| M3.1 teacher 部署 | 本地部署或接入 API；10 条冒烟；比较媒体输入方式（分开传入 / 内嵌音轨）和帧率（2 / 4 fps） | 峰值显存、每条耗时；选定的输入方式和帧率记入 `decision_log.md` |
| M3.2 资格探针 | A1（合成语调最小对）、A2（有人工字幕来源时）、V1（CoMMuTE 英→中）；校准音高阈值；核对每套 ASR 的标点是不是听出来的 | `reports/teacher_qualification.md`：各探针的成对准确率和置信区间；`teacher_qualified.visual` 和 `.audio` 的结论 |
| M3.3 S7 | 词表标签、日语形式分析、句末音高测量 | 每条词表规则的正反例测试；合成升降调信号上的分类测试 |
| M3.4 试点 | 从不少于 5 个 group 随机抽 2,000 条，另加最多 2,000 条规则挖掘样本；全部跑 `TARGET`、`T` 和预筛；随机试点样本全部跑消融，挖掘样本只对候选跑消融 | `reports/teacher_qa.md`；`condition_isolation_check` 通过 |
| M3.5 外部评估 X1 | 在 WMT25 日→中 speech 上评估 teacher（文本条件和 `T+V+A`），与一个强文本系统比较 | `reports/external_wmt25_teacher.md` |
| M3.6 成本 | 每条样本的耗时、峰值显存、每 1,000 条的 GPU 小时或 API 费用；推算 P4 和 P5 的开销 | `reports/cost_estimate.md` |

**Gate 3**

- [ ] 资格探针：V1 达到 `qualification.*` 的门槛。A1 和 A2 达标，或已记录“teacher 听不出语调，语调类样本靠通道 O”的决定。
- [ ] teacher 输出的合规率不低于 `diagnostics.teacher_json_ok_min`。
- [ ] `condition_isolation_check` 100% 通过。
- [ ] 随机试点样本上：预筛三类的比例、满足通道 O 形式条件的比例，都附 95% 置信区间。前者是通道 M 产出率的上界。
- [ ] 训练目标通过语言和格式检查的比例已报告。
- [ ] X1：teacher 的 chrF 和 COMET，以及与强文本系统的差，已报告。
- [ ] 成本表完成，并给出 P4（验证）和 P5（扩量）的开销估算。

**没通过怎么办**

| 现象 | 处理 |
|---|---|
| V1 不达标 | 停止试点。换更强的 teacher（更大的本地模型或 API），重跑 M3.1–M3.2 |
| A1 或 A2 不达标 | 可以继续。在 `decision_log.md` 记录；通道 M 的音频归因样本在报告里单独标注 |
| 合规率低 | 修提示词和解析；检查是否因帧数太多被截断 |
| 随机样本上 `different` 加 `unsure` 不到 1% | 说明这些来源里文字很少有歧义。先看规则挖掘样本的比例；再考虑换成互动更多的日常对话来源；把情况报告给你 |
| teacher 在 X1 上明显不如强文本系统 | teacher 的翻译质量不足以当训练目标。换 teacher，或由你决定是否接受 |
| 显存不够 | 缩短片段、减少帧数、降低分辨率、关闭语音输出；仍不够则用多卡或 API |

**任务提示**

```text
按 PHASED_GOALS.md 完成 P3（M3.1 → M3.6）：实现 docs/spec/02_pipeline.md 的 S7–S8 和 docs/spec/05_eval_train.md §1 的资格探针。
先做 M3.1 和 M3.2；V1 不达标就停下来报告，不要继续试点。
严格执行条件隔离：T+V 只给无声画面，T+A 只给门控音频。每次批量运行前先 --dry-run，超过 budget 门槛先等我确认。
以 teacher_qualification.md、teacher_qa.md、external_wmt25_teacher.md、cost_estimate.md 里的实际数字为证据。不用任何 test 数据调参。
```

---

## P4 — 自动验证、分桶与校准：S9–S11

**目的**：自动判定哪些样本确实需要视频或音频，并测出这些判定有多可靠。

**进入条件**：Gate 3 通过；文本评审、多模态核验、评审团所需的模型可用。

| 里程碑 | 内容 | 完成证据 |
|---|---|---|
| M4.1 读法 | S9a 读法归纳、S9b 强文本译文、S9c 读法映射和提前退出、S9d 归因 | 各降级代码的数量；映射评审一致率 |
| M4.2 线索 | S9e 线索提取、S9f 两步核验（开放描述 + 蕴含判断；可测量的音频线索用测量值） | 核验通过率；核验器误肯定率 |
| M4.3 反事实 | S9g：选取 `CF_V_far`、`CF_V_near`，制作 `CF_A_flat`；重跑并映射 | 反事实敏感率；control 上的反事实噪声底 |
| M4.4 评审团 | S9h：两种顺序；S9i 属性标记 | 位置一致性、评审间 κ、错配视频下的全票率 |
| M4.5 通道 O | S9j：ASR 问号一致性 + 句末音高 + 目标一致性 | 通道 O 的疑问和陈述数量；`teacher_perceived` 的比例 |
| M4.6 分桶 | S9k 诊断、S10 分桶 | `reports/bucket_report.md`：漏斗、四个 bucket 的比例和置信区间、分层表、诊断表 |
| M4.7 QE 校准 | 用 WMT25 日→中人工评分校准 `target.qe_min` 和评审模型；S11 过滤 | `reports/calibration_wmt25.md`：Kendall τ、成对准确率、选定的阈值 |
| M4.8 抽检（可选） | `cjvmt audit-page` 生成页面；你完成后 `cjvmt audit-score` | `reports/audit/`：core 精确率、目标可接受率、线索真实率及置信区间 |
| M4.9 扩量计划 | 按 §B 的算法，用实测的 `u`、`p`、`y`、`y_m` 和成本，算出需要的视频小时数、GPU 小时或费用 | `reports/scale_plan.md` |

**Gate 4**

- [ ] S9–S11 的单元测试通过，试点数据上全流程跑通。
- [ ] 诊断指标达到 `diagnostics.*` 的建议门槛：评审位置一致性、错配视频下的全票率、反事实噪声底、核验器误肯定率。达不到的逐项说明原因和处理。
- [ ] 随机试点样本上，四个 bucket 的比例及 95% 置信区间；通道 M 和通道 O 分开报告；按歧义类型、归因、来源分层；同时报告 `core_strict` 和 `core_beyond_asr` 的数量。
- [ ] 报告预筛漏判率和读法归纳漏判率。
- [ ] 自动检查确认：每条 core 都满足所在通道的全部规则，违规数为 0。
- [ ] QE 阈值和评审模型的选择有 WMT25 人工评分作依据。
- [ ] 抽检：做了就报告精确率，达到 `audit.*` 的门槛，或给出改进措施并在**新抽的样本**上复测；没做就在数据卡里标 `unaudited`。
- [ ] `scale_plan.md` 给出：需要的视频小时数、算力和费用、预计的 core-test 条数、能检出的最小差异。

**没通过怎么办**

| 现象 | 说明什么 | 处理 |
|---|---|---|
| 错配视频下评审团仍经常全票 | 评审偏向某个译文，与画面无关 | 检查两个译文是否文风差异大；改评审提示词；换评审模型 |
| 反事实噪声底高 | 换媒体本身就让译文乱变 | 检查解码参数是否固定；teacher 可能对媒体过于敏感，考虑换 teacher |
| 核验器误肯定率高 | 描述模型在编内容，或蕴含判断太松 | 收紧蕴含提示词；换描述模型 |
| 抽检精确率不达标 | 某几条规则把关不严 | 按降级原因和抽检结果找出问题最多的类型和规则，在 dev 上收紧，然后重新抽样复测 |
| core 产出率很低（随机样本上不到 0.5%，挖掘样本上不到 3%） | 这些来源里确需视频的台词很少 | 调整挖掘词表；换互动更多的来源；接受较小的 core 并报告实际功效；由你决定是否自录一批受控的对照片段 |
| 通道 M 几乎没有音频归因的样本 | teacher 听不出语调，与资格探针结论一致 | 如实报告；语调类靠通道 O |

**任务提示**

```text
按 PHASED_GOALS.md 完成 P4（M4.1 → M4.9）：实现 docs/spec/03_rules.md 的全部规则和 docs/spec/02_pipeline.md 的 S9–S11。
评审和核验模型不能与 teacher 同一开发方；评审两种顺序都要跑；线索核验用“开放描述 + 蕴含判断”，不要用诱导式提问。
先在 200 条上跑通并看诊断指标，再跑完整试点。生成 bucket_report.md、calibration_wmt25.md、scale_plan.md。
如果我选择做 H5，生成 150 条的抽检页面，等我做完后计算精确率和 Wilson 置信区间。
阈值只在试点和 dev 数据上调，并记入 docs/decision_log.md；同一批抽检样本不能反复用来调规则。
```

---

## P5 — 扩量、去重、切分、冻结、导出：S12–S13

**目的**：按扩量计划处理全部获批来源，形成可训练、可严格评测的数据集，并在任何训练之前冻结测试集。

**进入条件**：Gate 4 通过；`scale_plan.md` 的预算已由你确认（H3）。

| 里程碑 | 内容 | 完成证据 |
|---|---|---|
| M5.1 扩量 | 全部获批来源跑 S1–S11；算力有限时按 `mining.priority` 排序；用缓存和 `--resume` | 各阶段的 `stage_manifest.json`；`cost_ledger.csv` |
| M5.2 切分与冻结 | 近重复检测；按 `group_id` 切分（先 test，再 dev，最后 train）；生成自然最小对；冻结 | `manifests/split_registry.jsonl`；test 的 sha256 清单；`reports/leakage_audit.md` |
| M5.3 导出 | 每个评测条件一份；`train_T`、`train_TVA`、`train_TVA_shuf`；评测包 | 每份导出 10 条 dry-run 通过，确认音频和视频 token 都编码进去了；泄漏扫描违规数为 0 |
| M5.4 文档 | Data Card、统计、权利审计 | `docs/data_card.md`、`reports/dataset_statistics.md`、`reports/rights_audit.md` |

**Gate 5**

- [ ] test、dev、train 之间 group 交叉为 0，近重复交叉为 0，文本完全相同的跨集合样本已列出并说明。
- [ ] test 里没有 `target_subtitle`，没有 `human_audited_fail`，没有抽检用过的样本。
- [ ] test 已冻结；`make check` 能检出冻结之后的任何改动。
- [ ] 报告 core-test 的实际条数（通道 M、通道 O 分开）、`core_strict` 的条数、自然最小对的对数，以及按实测不一致率算出的“能检出的最小差异”。
- [ ] 每份导出通过 dry-run 和泄漏扫描。
- [ ] Data Card 写明：来源和许可、规模、各 bucket 和歧义类型的分布、`label_tier` 分布、资格探针结论、抽检情况、已知局限（自动银标、teacher 能力下界、蒸馏上限）。
- [ ] 受限媒体只在受控目录里；`data/releases/` 为空。
- [ ] H6：你确认冻结。

**没通过怎么办**

| 现象 | 处理 |
|---|---|
| core-test 不够 | 把更多 group 划给 test（在冻结之前）；或接受现有规模并写明功效 |
| 发现跨集合的近重复 | 合并 group 后重新切分；冻结之前可以反复做 |
| dry-run 里没有音频 token | 检查导出的 `audios` 字段和模板；确认训练框架版本支持该模型的音频输入 |

**任务提示**

```text
按 PHASED_GOALS.md 完成 P5（M5.1 → M5.4）：按 reports/scale_plan.md 扩量，执行 S12–S13。
扩量前先 --dry-run 并把调用量和费用写进 docs/HUMAN_TASKS.md 等我确认。切分一律按 group_id，先 test 再 dev 再 train。
以 leakage_audit.md、split_registry 的 sha256 清单、每份导出的 10 条 dry-run 和泄漏扫描结果为证据。
冻结 test 前在 docs/HUMAN_TASKS.md 请求 H6；冻结之后不能再改 test。
```

---

## P6 — 评测与小规模 LoRA

**目的**：回答三个问题。强文本模型已经能译到什么程度？不微调时，画面和声音在同一骨干上有没有真实增益？用自动数据微调后，模型是否学会了用非语言线索？

**进入条件**：Gate 5 通过，test 已冻结；训练算力可用（H3）。

| 里程碑 | 内容 | 完成证据 |
|---|---|---|
| M6.1 评测工具 | `cjvmt eval`（DisAcc 四分类、陈述误判率、跟随率、成对准确率、chrF、COMET）和 `cjvmt stats`（Wilson、McNemar、Holm、bootstrap） | 统计函数的手算例子测试；`question_intonation` 上规则判定与评审判定的一致率 |
| M6.2 零样本消融 | 主学生骨干不微调，跑 SPEC 05 §2 的全部条件；先在 dev 上确认流程，再在 test 上跑一次 | `experiments/<run_id>/`；SPEC 05 §5 的表 1–4 |
| M6.3 级联与文本参照 | `T_asr+desc_auto`；强文本系统（只作参照） | 同上 |
| M6.4 三个学生 | 50 步冒烟（SPEC 05 §9）→ 在 dev 上确定共用的训练设置 → 训练 `S_T`、`S_TVA`、`S_TVA_shuf`（预算允许时各 3 个种子）→ test 上各评一次 | 冒烟检查四项通过；SPEC 05 §5 的表 5；峰值显存和耗时 |
| M6.5 外部测试 | 零样本和三个学生在 WMT25 日→中上的结果；可选：辅助骨干上的 `T` 对 `T+V` | 表 6 |
| M6.6 错误分析 | 按歧义类型和归因，自动抽取成功和失败的典型案例；单独列出被错配媒体带偏的案例 | `reports/error_analysis.md` |

**Gate 6**

- [ ] 每个实验都有 `manifest.json`；做不了的条件写明原因。
- [ ] 同骨干零样本消融表完整，带置信区间、McNemar 和 Holm 校正；按归因和歧义类型分层。
- [ ] 三个学生的对照完成，并按 SPEC 05 §8 的结论条件给出判断。否定的结论同样如实写出。
- [ ] 报告副作用：`S_TVA` 在 control-test 上的跟随率、在 general-test 和 WMT25 上的整体质量。
- [ ] 报告里的每个数字都能在某个 `run_id` 的 `summary.csv` 或 `comparisons.csv` 里找到（脚本抽查）。
- [ ] 成本表：峰值显存、吞吐、每条样本的帧数和 token 数。

**结果不如预期时**

| 现象 | 可以查的方向（都只在 dev 上试） |
|---|---|
| 零样本下 `T+V+A` 不比 `T` 好 | 帧率和分辨率是否太低；提示词是否让模型去看；与资格探针结论是否一致 |
| `S_TVA` 不比 `S_T` 好 | 训练集里 core 的比例；训练时的分辨率；目标里的噪声（看抽检结果）；训练步数 |
| `S_TVA_shuf` 和 `S_TVA` 一样好 | 提升来自训练量而不是媒体，结论是“没有学会用线索” |
| `S_TVA` 的跟随率升高 | 模型变得过度相信视频；增加 control 样本的比例 |

不允许的做法：回头修改 test、按结果挑样本、只报告有利的子集。

**任务提示**

```text
按 PHASED_GOALS.md 完成 P6（M6.1 → M6.6）。评测条件、指标、统计和训练配方以 docs/spec/05_eval_train.md 为准。
主消融在同一个 Omni 骨干上做。三个学生用同一组训练目标、步数、种子和采样设置。
每次正式训练前先跑 50 步冒烟检查。超参数只看 dev；test 只在最后各跑一次，run_id 记进 reports/progress.md。
以 experiments/<run_id>/ 下的 manifest、summary.csv、comparisons.csv 为证据。做不了的条件和否定的结果如实写明。
```

---

## P7 — 定向扩量、中→日、发布

**目的**：按 P6 暴露的短板补数据；把方法扩展到中→日；按授权决定发布范围。

| 里程碑 | 内容 | 完成证据 |
|---|---|---|
| M7.1 定向扩量 | 针对 P6 里容易错的类型和线索调整挖掘词表的优先级，补充数据；新数据与旧 test 严格隔离 | 新版本的数据统计；旧 test 不变（需要更新 test 时升主版本，保留旧版） |
| M7.2 中→日 | 用同一套流水线处理中文视频来源（重新走 P1 审批）；歧义类型增加敬语、授受、こそあ、彼/彼女 等日语必须表达的信息；通道 O 的音高规则针对中文重新校准；WMT26 中→日 speech 作外部测试 | 中→日的试点分桶报告 |
| M7.3 发布决策 | 按 SPEC 06 §7 的分级，逐个来源决定公开范围（H6） | `docs/decision_log.md` 里的发布决定 |
| M7.4 复现包与论文材料 | `docs/reproducibility.md`、`reports/final_dataset_report.md`、`reports/final_experiment_report.md`、`docs/paper_outline.md` | `make reproduce-mini`（Mock 加合成数据）在新环境里通过 |

**Gate 7**

- [ ] 每个数据版本都能追溯到来源、权利和每次处理的参数。
- [ ] 公开的文件符合各自的许可，不含受限媒体；附 sha256、Data Card、许可文件和撤回渠道。
- [ ] 质量统计、切分、自动标签的准确率、模态贡献和负例结果都可以复现。
- [ ] 写明局限：来源类型的覆盖范围、自动银标、teacher 能力下界、蒸馏上限。
- [ ] 中→日数据没有用日→中数据倒置得到。

---

## C. 进度看板

看板在 `reports/progress.md` 里，**只填实测值**，没测的写“未统计”。至少包括：

| 指标 | 阶段 |
|---|---|
| 获批来源数和总时长 | P1 |
| 每小时可用台词数 `u`；`transcript_tier` 分布 | P2 |
| 资格探针：V1、A1、A2 的成对准确率 | P3 |
| 随机样本的候选比例 `p`（95% CI） | P3 |
| 四个 bucket 的数量；随机样本的 core 产出率 `y`（95% CI）；通道 M 和通道 O 各多少 | P4 |
| 诊断：位置一致性、错配视频全票率、反事实噪声底、核验器误肯定率 | P4 |
| 抽检 core 精确率（95% CI），或“未做” | P4 |
| core-test 条数、`core_strict` 条数、能检出的最小差异 | P5 |
| 跨集合泄漏；test 里的目标语字幕 | P5，目标都是 0 |
| `S_TVA` 与 `S_T` 在 core-test 上的 DisAcc 差（95% CI）；`S_TVA_shuf` 与 `S_T` 的差 | P6 |

三组容易混淆的概念要分开写：候选数和 core 数；`auto_verified` 和人工验证；“片段有视频”和“视频有用”。

---

## D. 现在就能开始的事（P0）

- [ ] M0.1 骨架和工具链。
- [ ] M0.2 配置、提示词的加载和一致性检查。
- [ ] M0.3 schema、validator、合成 fixture。
- [ ] M0.4 `bucketize()` 和真值表测试。
- [ ] M0.5 Mock 冒烟。
- [ ] M0.6 文档和 Gate 自检报告。
- [ ] 你来做 H1：读 `docs/HUMAN_TASKS.md` 里 H1 列出的几个决定。

P0 不下载模型和数据，不调用付费 API，不启动 GPU 任务。

---

## E. 风险与对策

| 风险 | 严重性 | 对策 |
|---|---|---|
| 训练目标只依赖文字 | 极高 | 铁律 1；validator 检查 `target_condition` |
| teacher 不会用声音或画面 | 极高 | 资格探针先行；视觉不达标就停；音频不达标靠通道 O |
| teacher 编造线索 | 高 | 两步核验（不诱导）；核验器误肯定率；抽检里的线索真实率 |
| 评审偏向某个译文，与画面无关 | 高 | 两个译文出自同一模型；两种顺序；错配视频下的全票率 |
| 存在不表态的中性译法，benchmark 测不出差别 | 高 | 中性译法检验（C3） |
| “看后文就够了” | 中高 | `future_context_sufficient` 标记和 `core_strict` 子集 |
| 识别出的问号吃掉了音频的作用 | 高 | `text_neutral`；`asr_punct_sufficient` 标记 |
| 窗口里的声音带进别人的台词 | 高 | 音频门控 |
| 自动评测循环论证 | 高 | 主指标 DisAcc 不依赖参考措辞；WMT25 人工参考；teacher 家族不当评审 |
| 版权和平台条款 | 极高 | 按来源审批；内部版本和公开版本隔离 |
| core 产出率低 | 中高 | 规则挖掘加随机基线；如实报告功效 |
| ASR 错误污染原文 | 高 | 双路读音 CER；对不上的台词拒收 |
| 中文字幕泄漏答案；日文字幕卡造成混淆 | 极高 | OCR 三分类；只靠屏幕文字的样本不进 core |
| 同一来源进了 train 和 test | 高 | 按 `group_id` 切分；近重复检测 |
| 训练时模型忽略媒体 | 中高 | core 上采样；句末标点丢弃；三个学生的对照 |
| 训练框架的音视频解码问题 | 中 | 音视频分开传入；带超时的子进程；50 步冒烟检查 |
| AGENTS.md 过长被截断 | 中 | 32 KiB 检查；细节放 `docs/spec/` |

---

## F. 参考资料

论文和数据集的链接见 `docs/review_2026-11-16.md` 和 `docs/review_2026-11-16_round2.md`；模型和工具的许可见 `docs/spec/06_compliance.md` §4。文献里的语向、发布状态和权利限制与本项目不一定相同，使用前先核实。
