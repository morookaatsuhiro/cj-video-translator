# SPEC 03 — 自动判定规则：读法、分桶、诊断、抽检

> v0.3 | 2026-11-16 | 数值以 `configs/pipeline.yaml` 为准，文中写键名。  
> 什么时候读：实现 S9–S10、写 `bucketize()`、解释分桶结果、准备抽检时。

---

## 0. 总览

一条样本要进 core，必须有证据说明三件事：

1. 只看文字，这句话有两种以上的理解，而且中文译文必须选一种。
2. 正确的那种理解是由视频或音频决定的。
3. 这个判断不是某一个模型自说自话。

证据有两条独立的通道：

| 通道 | 证据来源 | 适用范围 |
|---|---|---|
| **M（模型通道）** | teacher 在不同输入下译法不同，再经线索核验、反事实测试、评审团确认 | 所有歧义类型 |
| **O（客观通道）** | 可测量的信号：多套 ASR 的句末问号加句末音高 | 只有 `question_intonation` |

通过任一通道即为 core。两个都通过记 `core_channel = both`。

**这套规则给出的是下界。** teacher 听不出来或看不出来的线索，通道 M 找不到。所以报告“有多少台词需要视频或音频”时，要写明这是在当前 teacher 能力下的估计，并附上资格探针的结果（SPEC 05 §1）。

---

## 1. 读法与映射

### 1.1 读法集合 R

- 由 S9a 的 `text_readings` 生成。做这一步的评审**看不到任何候选译文**，只看 `text_neutral` 和前文。这样“有没有歧义”的判断不会被 teacher 的输出带着走。
- 每个读法有 `reading_id`、`gloss`、`zh`、`cue_hint`。
- `question_intonation` 类型固定两个读法：`q`（疑问）和 `s`（陈述）。

### 1.2 映射

每个候选译文由 `reading_map` 判给下面之一：

| 结果 | 含义 |
|---|---|
| 某个 `reading_id` | 译文明确表达这种读法 |
| `neutral` | 译文在两种以上读法下都说得通 |
| `other` | 列表之外的意思 |
| `invalid` | 不是译文 |

- 用 `verify.map_judges` 个文本评审，它们不能是 teacher 本身。评审看不到译文来自哪个模型、哪个条件。
- 所有评审一致才采用；不一致时该候选的读法记 `uncertain`。
- 目标译文或 `T` 的读法是 `uncertain` → `N_MAP_DISAGREE`。
- 目标译文被判为 `neutral` 或 `invalid` → `N_TARGET_NEUTRAL`。
- 目标译文被判为 `other` 时：把评审写的 `new_gloss` 作为新读法追加进 R，标 `source: induced`；再换另一个文本评审重做一次读法归纳（仍然看不到译文）。
  - 第二次也认为没有歧义 → `N_TEXT_UNAMBIG`。
  - 第二次认为有歧义 → `text_ambiguous` 记 `uncertain`，把两次的读法合并后重新映射全部候选。

### 1.3 记号

| 记号 | 含义 |
|---|---|
| `r*` | 训练目标（`TARGET` 条件）的读法 |
| `r_T`、`r_V`、`r_A`、`r_VA` | teacher 在 `T`、`T+V`、`T+A`、`T+V+A` 下的读法（文本都是 `text_neutral`） |
| `r_Tasr`、`r_Tfull` | teacher 在 `T_asr`、`T+C_full` 下的读法 |
| `r_S1`、`r_S2` | 两个强文本系统的读法 |

---

## 2. 候选预筛

- S8c 对每条样本比较训练目标和 `T` 的译文，结果是 `same` / `different` / `unsure`。
- `same` 的样本不可能通过通道 M（因为 C4 要求两者读法不同），所以不再做昂贵的消融。没有被抽作随机基线或 control 候选的，记 `N_NOT_CANDIDATE`，进 general。
- 预筛宁可多放不可漏放：提示词要求拿不准时答 `unsure`。
- **漏判率**在随机试点样本上测：试点样本不管预筛结果如何都跑完整流程，统计“预筛判 `same` 但最终通过了通道 M”的比例。这个数写进 `reports/bucket_report.md`。

---

## 3. 通道 M：C1–C8

全部满足才通过。每条规则不通过时记对应的降级代码（SPEC 04 §5）。

| 规则 | 条件 | 不通过时 |
|---|---|---|
| **C1 转写可靠** | `transcript_tier` 是 `manual_caption`，或是 `auto_agreed` 且 `asr_agreement_cer ≤ asr.tau_core`；`asr_hallucination = false`；`overlap = false` | `N_TRANSCRIPT_TIER` / `N_OVERLAP` |
| **C2 没有目标语字幕** | `onscreen_text.target_subtitle = false` | `E_TARGET_SUB` |
| **C3 必须二选一** | (a) `text_ambiguous` 是 `yes` 或 `uncertain`；(b) `r*` 是 R 里的一个具体读法；(c) 没有中性译法：`neutral_exists ≠ yes`，且所有纯文本候选（`T`、`T_strong`、`T_asr`、`T+C_full`）都没有被判为 `neutral` | `N_TEXT_UNAMBIG` / `N_TARGET_NEUTRAL` / `N_NEUTRAL_EXISTS` |
| **C4 多模态改变了读法** | (a) `r_VA = r*`：teacher 只靠看和听（文本是中性的）也得到目标读法；(b) `r_T` 是 R 里的具体读法且 `r_T ≠ r*`；(c) `text_prior_support ≤ bucket.text_prior_support_max` | `N_NOT_PERCEIVED` / `N_TEACHER_T_SAME` / `N_TEXT_PRIOR` |
| **C5 线索经过核验** | 至少 1 条线索核验为 `yes`，模态与归因一致（§4），且不全是 `scene_text` 或 `onscreen_source_text` | `N_NO_CUE` / `N_NO_VERIFIED_CUE` / `N_OCR_ONLY` |
| **C6 反事实敏感** | 按归因做对应的反事实测试，读法发生改变（§4） | `N_CF_INSENSITIVE` / `N_CF_UNTESTED` |
| **C7 评审团全票** | 所有评审在两种顺序下都选训练目标（§6） | `N_JURY_SPLIT` / `N_JURY_UNAVAILABLE` |
| **C8 目标质量合格** | S11 的检查全部通过 | `E_TGT_LANG` / `E_TGT_FORMAT` / `E_TGT_LEN` / `E_QE_LOW` |

**`text_prior_support` 的算法**：在 teacher 的 `T`、强文本系统 1、强文本系统 2 这三个纯文本输出里，读法等于 `r*` 的占几分之几。C4(b) 已经要求 `r_T ≠ r*`，所以它只可能是 0、1/3、2/3。两个强文本系统都猜中（2/3）说明文字本身已经指向这个读法，样本进 general。等于 0 的样本另记 `difficulty = hard`。

**为什么 C4(a) 用 `r_VA` 而不是直接用训练目标**：训练目标是用 `text_asr` 生成的，里面可能有问号。只有当 teacher 在中性文本下靠看和听也得到同一读法，才能说“是视频或音频起了作用”，而不是识别出的标点起了作用。

---

## 4. 模态归因与反事实

### 4.1 归因

| `r_V = r*` | `r_A = r*` | `r_VA = r*` | `modality_attribution` |
|---|---|---|---|
| 是 | 否 | 是 | `visual` |
| 否 | 是 | 是 | `audio` |
| 是 | 是 | 是 | `either` |
| 否 | 否 | 是 | `both_needed` |
| — | — | 否 | 通道 M 不通过；看通道 O |

### 4.2 反事实测试

做法：文本和前文不变，只把媒体换掉，teacher 重新翻译，再映射读法。**只看读法有没有离开 `r*`**，不给反事实输入定义“正确答案”。

| 归因 | 要跑的条件 | 通过的条件 |
|---|---|---|
| `visual` | `T+V`，画面换成 `CF_V_far` | 读法 ≠ `r*` |
| `audio` | `T+A`，音频换成 `CF_A_flat`；不变且 `verify.cf_audio_tts_enabled` 时再试 `CF_A_tts` | 任一次读法 ≠ `r*` |
| `either` | 上面两项都跑 | 两项都通过；只通过一项时把归因改成那一项，并记 `attribution_revised = true` |
| `both_needed` | `T+V+A` 换画面；`T+V+A` 换音频 | 两次读法都 ≠ `r*` |

- 音频线索全是非语调类（例如笑声、嗓音情绪），而 TTS 反事实没有开启时，记 `counterfactual_sensitive = not_tested`，降级代码 `N_CF_UNTESTED`。
- `counterfactual_sensitive` 取值：`yes` / `no` / `not_tested`。

### 4.3 线索范围 `cue_scope`（只对视觉归因）

通过 `CF_V_far` 之后，再用 `CF_V_near`（同一视频的另一个时段）跑一次：

| `CF_V_near` 的结果 | `cue_scope` | 含义 |
|---|---|---|
| 读法 ≠ `r*` | `momentary` | 信息在这一刻的动作、表情或事件里 |
| 读法 = `r*` | `scene_static` | 信息在场景或人物本身（是谁、在哪里、什么关系） |
| 没有合适的片段 | `not_tested` | — |

这个标记不影响分桶，用来回答“起作用的是瞬时动作还是静态场景”。

---

## 5. 线索核验

teacher 是被问“有哪些线索支持这种读法”才写出线索的，这种问法容易诱导出并不存在的线索。所以核验不能再用“是不是在摆手”这种诱导式问题。

### 5.1 两步核验（视觉线索，以及无法测量的音频线索）

1. **开放描述**：一个开发方不同于 teacher 的多模态模型，看线索区间前后各加 0.5 秒的片段（按 `video_input.verify_fps` 采样），用 `cue_describe_visual` 或 `cue_describe_audio` 描述看到或听到了什么。它不知道线索内容，也不知道读法。
2. **蕴含判断**：一个文本评审用 `cue_entail` 判断这段描述是否明确包含线索的 `observation`。结果是 `yes` / `no` / `unclear`。

只有 `yes` 算核验通过。

### 5.2 可测量的音频线索

| `cue_type` | 核验方法 |
|---|---|
| `prosody_final_rise` | S7 测得的 `form.final_f0.class`：`rise` → `yes`，`fall` → `no`，其余 `unclear` |
| `pause` | 词级时间戳里，台词内部有不短于 300 ms 的静音 → `yes` |
| `speech_rate` | 每秒拍数与同一视频的中位数相差超过 1.5 个标准差 → `yes` |
| 其余音频线索 | 用 §5.1 的两步核验；没有能听音频的核验模型时记 `unclear` |

### 5.3 其他规则

- 线索的时间必须落在片段窗口内；音频线索必须落在门控区间内。否则作废。
- `scene_text` 类线索用 S6 的 OCR 结果核验：线索区间内确实识别到对应文字才算 `yes`。
- 一条样本如果唯一通过核验的线索是屏幕文字，记 `ocr_dependent = true`，进 general，单独统计。

---

## 6. 评审团

- **组成**：至少 `verify.jury_min_models` 个多模态模型，开发方都不同于 teacher，其中至少 `verify.jury_min_non_qwen_lineage` 个的语言模型骨干不是 Qwen 系。评审必须能处理被归因的模态：音频归因的样本只让能听音频的评审参加。够格的评审不足时记 `N_JURY_UNAVAILABLE`。
- **输入**：`clips_v` 和 `audio_gated`（只能看视频的评审只给 `clips_v`）、`text_neutral`、前文、两个译文。
- **两个译文**：一个是训练目标，另一个是 teacher 自己在 `T` 条件下的译文。两者出自同一个模型，文风接近，评审不容易因为“哪个更通顺”而偏向一方。
- **盲评**：评审看不到模型名和条件名。每个评审按 `verify.jury_orders` 的两种顺序各判一次。
- **全票**：所有评审、所有顺序都选训练目标，才算通过。选 `both`、`neither`、`cannot_tell` 都不算支持。

---

## 7. 通道 O：客观通道（只用于 `question_intonation`）

**为什么需要**：已有研究发现，端到端模型听得到语调，却经常不据此改变译文。如果 teacher 就是这样，通道 M 会一条语调样本都找不到。而“是不是疑问”恰好可以用不依赖大模型的信号来判断。

全部满足才通过（`objective.enabled = true` 时）：

| 规则 | 条件 | 不通过时 |
|---|---|---|
| **O1** | 满足 C1 和 C2；句末 `objective.f0.final_window_ms` 内的有声帧不少于 `objective.f0.min_voiced_frames` | `N_TRANSCRIPT_TIER` / `N_OBJ_UNVOICED` |
| **O2 形式上有歧义** | `form.decl_no_q = true`：句末没有疑问助词，句中没有疑问词（词表见 `configs/mining_lexicon.yaml`） | `N_TEXT_UNAMBIG` |
| **O3 信号一致** | 疑问：所有原生标点的 ASR（不少于 `objective.asr_qmark_systems_min` 套）句末都是问号，**并且**句末音高判为 `rise`。陈述：这些 ASR 句末都不是问号，**并且**音高判为 `fall`。其余情况不通过。来源有人工字幕时，人工字幕的问号算作一套系统 | `N_OBJ_CONFLICT` |
| **O4 目标与信号一致** | 训练目标的读法（`q` 或 `s`）与 O3 的结论相同；中文问句规则（句末是“？”，或句末语气词是“吗/么”）给出同样的结果 | `N_OBJ_TARGET_MISMATCH` |
| **O5 文字不够用** | O3 结论是疑问时：`text_prior_support ≤ bucket.text_prior_support_max`，即三个纯文本系统里最多一个译成了疑问句 | `N_TEXT_PRIOR` |
| **O6** | 满足 C8 | 同 C8 |

结果：

- O3 结论是**疑问**且全部通过 → `bucket = core`，`core_channel = objective`，`modality_attribution = audio`。
- O3 结论是**陈述**且 O1–O4、O6 通过 → `bucket = control`，`contrast_role = statement_counterpart`。评测时用它们统计“把陈述误译成疑问”的比例，防止模型一律译成疑问句来刷分。
- 另外记录 `teacher_perceived`：teacher 在 `T+A` 下是否也得到了疑问读法。这个比例本身就是一个研究结果。
- 评审团如果有能听音频的成员，照常投票并记录，但不作为通过条件。

音高阈值 `objective.f0.rise_delta_st`、`fall_delta_st` 用资格探针 A1 和带人工字幕的自然语音校准（SPEC 05 §1）。

---

## 8. control、general、rejected

| bucket | 条件 |
|---|---|
| `control` | 第一种：满足 C1、C2、C8；预筛为 `same`；`text_ambiguous = no`；四种消融都跑过；`T`、`T+V`、`T+A`、`T+V+A` 和训练目标都映射到同一个读法。第二种：通道 O 的陈述对照（§7） |
| `general` | 满足 C8；`transcript_tier` 是 `manual_caption` 或 `auto_agreed`；不属于 core 和 control。有 `target_subtitle` 的样本必须打码后才能进训练集，不能进 test |
| `rejected` | 命中任一 `E_` 代码 |

没有跑过四种消融的样本不能进 control。

---

## 9. 属性标记

这些标记不改变分桶，但必须记录并在报告里分层。

| 字段 | 取值 | 含义 |
|---|---|---|
| `asr_punct_sufficient` | `yes` / `no` / `not_tested` | `r_Tasr = r*`：识别出的标点已经足够，级联做法能解决 |
| `future_context_sufficient` | `yes` / `no` / `not_tested` | `r_Tfull = r*`：看了后文就能解决 |
| `text_prior_support` | 0、1/3、2/3 | §3 |
| `difficulty` | `hard` / `medium` | `text_prior_support = 0` 为 `hard` |
| `cue_scope` | `momentary` / `scene_static` / `not_tested` | §4.3 |
| `ocr_dependent` | 布尔 | §5.3 |
| `teacher_perceived` | 布尔 | 通道 O 样本上 teacher 是否听出来 |
| `attribution_revised` | 布尔 | §4.2 |

报告里常用的两个子集：

- `core_strict`：core 中 `future_context_sufficient = no` 的样本。
- `core_beyond_asr`：core 中 `asr_punct_sufficient = no` 的样本。

---

## 10. 标签等级 `label_tier`

| 取值 | 含义 |
|---|---|
| `auto_raw` | 模型直接输出，尚未验证 |
| `auto_verified` | 通过了适用的全部规则 |
| `human_audited_pass` | 抽检中人工同意 |
| `human_audited_fail` | 抽检中人工不同意；不能进 test |

---

## 11. 诊断指标与噪声底

每次分桶都要算，写进 `reports/bucket_report.md`。门槛是建议值，在 `diagnostics.*` 里；达不到时先修提示词或换模型，不要带着问题扩量。

| 指标 | 怎么算 | 说明什么 | 门槛键 |
|---|---|---|---|
| 评审位置一致性 | 同一评审在 AB 和 BA 两种顺序下选择相同的比例 | 评审有没有位置偏好 | `jury_position_consistency_min` |
| 评审间 κ | 两个评审的 Cohen's κ | 评审是否一致 | 只报告 |
| 错配视频下的全票率 | 在 `verify.diag_subsample` 条候选上，把片段换成 `CF_V_far` 再让评审团投票，统计仍然全票选训练目标的比例 | 评审的偏好是不是和画面无关（例如只是觉得那个译文更通顺） | `jury_wrongvideo_unanimous_max` |
| 反事实噪声底 | 在 control 样本上做同样的换媒体测试，统计读法发生改变的比例；画面和音频分开算 | 换媒体本身会不会让译文乱变 | `cf_noise_max` |
| 核验器误肯定率 | 把线索的 `observation` 拿到 `CF_V_far` 片段上核验，统计结果为 `yes` 的比例 | 核验器是不是什么都说有 | `verifier_false_yes_max` |
| 读法映射一致率 | 映射评审之间一致的比例 | 读法定义是否清楚 | 只报告 |
| 预筛漏判率、读法归纳漏判率 | 随机试点样本上，被预筛判 `same` 或被 S9a 判 `no`、但通过了通道 M 其余规则的比例 | 候选生成漏掉了多少 | 只报告 |
| teacher JSON 合规率 | 合规输出 / 全部调用 | 工程质量 | `teacher_json_ok_min` |

所有比例都给 95% Wilson 置信区间，并按歧义类型、归因、来源、挖掘标签分层。随机样本和规则挖掘样本分开统计：只有随机样本上的比例才是对总体的无偏估计。

---

## 12. 抽检协议（H5，可选，推荐）

**目的**：测出自动标签有多准。不是逐条审核，也不是用来修数据。

- **抽样**：`audit.n_core` 条 core（按歧义类型、归因、通道分层）、`audit.n_general` 条 general、`audit.n_control` 条 control。只从 dev 和试点数据里抽，不从 test 里抽。固定种子。
- **页面**：`cjvmt audit-page` 生成一个静态 HTML，答案保存为本地 JSON。每条的流程：
  1. 播放 `preview` 片段，显示日语台词和前文。
  2. 随机顺序显示两个译文：训练目标，和 teacher 在 `T` 条件下的译文。
  3. 研究者选：左更好 / 右更好 / 都可以 / 都不对 / 看不出。两个译文文字完全相同时只显示一个，改问：这个译文可以接受吗？可以 / 不可以 / 看不出。
  4. 选完后，如果这条样本有线索，显示 teacher 写的线索，再问一题：这条线索在视频里确实存在吗？是 / 否 / 看不清。
- **指标**（`cjvmt audit-score`）：
  - core 精确率：选“训练目标更好”的比例。选“都可以”算 core 不成立。
  - 目标可接受率：没有选“都不对”、也没有选另一个更好的比例。
  - 线索真实率。
  - control 正确率：选“都可以”的比例。
  - 都给 Wilson 95% 置信区间，并按通道 M 和通道 O 分开报告。
- **建议门槛**：core 精确率不低于 `audit.core_precision_min`，置信区间下界不低于 `audit.core_precision_ci_low_min`。
- **达不到时**：在 dev 上改规则，然后**重新抽样**再测。同一批抽检样本不能反复用来调规则。
- **回写**：抽检过的样本 `label_tier` 改为 `human_audited_pass` 或 `human_audited_fail`。
- **不做抽检时**：数据卡写 `unaudited`，论文里不能写“经人工验证”。

---

## 13. 零人工校准

不需要研究者动手的外部校准，P3–P4 必做：

| 校准对象 | 数据 | 做法 |
|---|---|---|
| QE 阈值 `target.qe_min` | WMT25 日→中的人工评分 | 算 QE 分数和人工分数的 Kendall τ、成对准确率；选一个能把明显错误的译文挡在外面的阈值 |
| 评审和映射模型的选择 | 同上 | 比较候选评审模型和人工评分的一致程度，选一致程度高的 |
| teacher 的翻译质量 | WMT25 日→中 speech 的人工参考 | teacher 在文本条件和 `T+V+A` 下的 chrF 和 COMET，与强文本系统比较 |
| 音高阈值、teacher 是否听得出语调 | 资格探针 A1；带人工字幕的自然语音 | SPEC 05 §1 |
| teacher 是否看得懂画面 | 资格探针 V1 | SPEC 05 §1 |

---

## 14. `bucketize()` 真值表测试清单

`bucketize(sample, config)` 是纯函数，不调用模型。P0 用合成样本写下面这些测试，每行至少一个用例：

| 编号 | 情形 | 期望 |
|---|---|---|
| T01 | C1–C8 全部满足，归因 `visual` | core / model |
| T02–T09 | 在 T01 上分别破坏 C1…C8 中的一条 | 不是 core，且原因代码正确 |
| T10 | 存在中性译法（纯文本候选被判 `neutral`） | general，`N_NEUTRAL_EXISTS` |
| T11 | 两个强文本系统都给出 `r*` | general，`N_TEXT_PRIOR` |
| T12 | `r_VA ≠ r*`，但通道 O 全部满足（疑问） | core / objective |
| T13 | 通道 O 的陈述对照 | control，`statement_counterpart` |
| T14 | O3 信号冲突（ASR 有问号，音高下降） | general，`N_OBJ_CONFLICT` |
| T15 | `either` 归因，只有画面的反事实通过 | core，归因改为 `visual`，`attribution_revised` |
| T16 | `both_needed`，只有一次反事实读法改变 | general，`N_CF_INSENSITIVE` |
| T17 | 唯一通过核验的线索是屏幕文字 | general，`N_OCR_ONLY`，`ocr_dependent` |
| T18 | 音频归因，没有够格的评审 | general，`N_JURY_UNAVAILABLE` |
| T19 | 文本无歧义，四种消融读法一致 | control |
| T20 | 文本无歧义，但没跑消融 | general（不能进 control） |
| T21 | 训练目标含假名 | rejected，`E_TGT_LANG` |
| T22 | 同时满足通道 M 和通道 O | core / both |
| T23 | 有 `target_subtitle` | 不能是 core；训练集需打码 |
| T24 | `transcript_tier = auto_single` | rejected，`E_ASR_DISAGREE` |
