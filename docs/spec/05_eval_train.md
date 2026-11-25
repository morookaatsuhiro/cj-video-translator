# SPEC 05 — 资格探针、评测、统计、导出与训练

> v0.3 | 2026-11-16 | 数值以 `configs/pipeline.yaml` 为准。  
> 什么时候读：P3 做 teacher 资格测试、P5 导出、P6 评测和训练时。

---

## 1. 资格探针与外部数据

探针回答一个前提问题：**teacher 到底会不会用声音和画面来改译文？** 如果它不会，通道 M 的结果就没有意义。探针的答案是构造出来的或由外部人工数据给出的，不需要研究者标注。探针数据只用于测试，不进数据集。

| 探针 | 数据 | 怎么测 | 指标 |
|---|---|---|---|
| **A1 语调最小对（合成）** | `configs/probes/intonation_frames.txt` 里的句子（可扩充到 100 句以上），用 TTS 各合成“句末上扬”和“不上扬”两版；文本一律不带问号 | teacher 在 `T+A` 下翻译；中文问句规则判断译成了疑问还是陈述 | **成对准确率**：两版都译对才算对。只看文字的模型两版输出相同，成对准确率必然是 0 |
| **A2 语调（自然语音）** | 带人工日语字幕的获批来源里，满足 `form.decl_no_q` 的台词；人工字幕句末是否为问号当作答案 | 同上，用 `text_neutral` | 疑问召回率、陈述误判率 |
| **V1 图像消歧** | CoMMuTE 的英→中部分：一句有歧义的英文配两张图，对应两个译文（CC BY-NC-SA 4.0，仅研究；使用前核对仓库里的许可文件） | teacher 看图翻译，`reading_map` 判断译文对应哪个参考 | 成对准确率 |
| **X1 整体质量** | WMT25 日→中 speech：视频、ASR 文本、人工参考 `refA` | teacher 在文本条件和 `T+V+A` 下翻译；同时跑一个强文本系统 | chrF、COMET；与强文本系统的差 |

A1 顺带完成三件事：

1. **校准音高阈值**：`objective.f0.rise_delta_st`、`fall_delta_st` 在合成最小对上调到分类准确率最高，再用 A2 的自然语音复核。
2. **核对 ASR 的标点是不是听出来的**：每套 ASR 分别转写两版音频。如果它给上扬版加问号、给另一版加句号，说明标点带声学信息，`native_punctuation` 填 `true`。
3. **选媒体输入方式和帧率**：比较“分开传入”与“内嵌音轨”、2 fps 与 4 fps（SPEC 01 §4.4–4.5）。

**资格判定**（建议门槛在 `qualification.*`）：

| 结果 | 处理 |
|---|---|
| V1 成对准确率达标 | `teacher_qualified.visual = true` |
| V1 不达标 | **停下来**。视觉是这个项目的核心，换 teacher 或请研究者决定 |
| A1 和 A2 达标 | `teacher_qualified.audio = true` |
| A1 或 A2 不达标 | `teacher_qualified.audio = false`。继续做，但语调类样本主要靠通道 O；数据卡和论文里写明 teacher 听不出语调 |

TTS 说明：探针默认用 VOICEVOX 引擎，合成接口的 `enable_interrogative_upspeak` 参数控制句末是否上扬。合成时可以用带问号的文本来触发上扬，但交给被测模型和 ASR 的文本一律不带问号。各角色声音有各自的使用条款，通常要求署名；探针音频不再分发。

**外部测试集**（永远不进训练，不用来调参，不再分发其中的视频和截图）：

| 数据 | 语向 | 有什么 | 用途 |
|---|---|---|---|
| WMT25 General MT，speech 域 | 日→中 | 视频、ASR 文本（`src_text`）、人工参考（`tgt_text.refA`）、人工评分 | 主方向的外部测试；校准 QE 和评审 |
| WMT26 General MT，speech 域 | 中→日 | 视频、ASR 文本、人工转写（`gold_transcript`）、人工参考（`refs`） | P7 中→日方向的外部测试 |

这两个数据集的参考译文是人工的，但样本没有按“是否需要视频”筛选过，所以它们测的是整体翻译质量，不是歧义消解。

---

## 2. 被评测系统的输入条件

| 名称 | 文本 | 媒体 | 回答什么 |
|---|---|---|---|
| `T_neutral` | 前文 + `text_neutral` | — | 文本下限 |
| `T_asr` | 前文 + `text_asr` | — | 识别出的标点能帮多少 |
| `T+A` | `text_neutral` | `audio_gated` | 声音的作用 |
| `T+V` | `text_neutral` | `clips_v` | 画面的作用 |
| `T+V+A` | `text_neutral` | 两者 | 两者是否互补 |
| `T_asr+V+A` | `text_asr` | 两者 | 实际使用时的最好情况 |
| `T+V_shuf`、`T+A_shuf` | `text_neutral` | 其他 group 的媒体 | 会不会被无关媒体带偏 |
| `A_only`、`V+A_only` | 不给台词文本（仍给前文） | 音频；音频加画面 | 端到端：模型自己听 |
| `T_asr+desc_auto` | `text_asr` + “补充信息：……” | — | 级联：先把画面和声音写成文字再翻译 |

- “补充信息”由 `desc_auto` 提示词生成，生成它的模型不能和 teacher 同一开发方。
- 同一个实验里，各条件必须用同一个模型和版本、同一个提示词模板（只差 `modality_note`）、同一组解码参数（不采样）、同一组帧率和分辨率。

**与旧材料 A–F 的对照**：旧 PPT 第 21 页和旧 `SPEC.md` 对 A–F 的定义并不一致，所以这里不再使用字母。

| 本项目 | 旧 PPT 第 21 页 | 旧 `SPEC.md` |
|---|---|---|
| `T_neutral` | A（旧材料用人工转写，本项目用 `manual_caption` 或 `auto_agreed` 的转写近似） | A |
| `T_asr` | B | B |
| `T_asr+desc_auto` | C | — |
| `A_only` | D | — |
| `V+A_only` | E | — |
| `T+A` | — | C |
| `T+V` | — | D |
| `T+V+A` | — | E |
| 文本 + 人写的描述 | F | F（本项目没有人写的描述，只在外部数据自带描述时才能跑） |

---

## 3. 指标

### 3.1 主指标：歧义消解准确率（DisAcc）

对 core-test 的每条样本，把系统输出用 `reading_map` 判成四类之一：

| 类别 | 含义 | 计分 |
|---|---|---|
| `target` | 表达了目标读法 | 对 |
| `distractor` | 表达了另一种读法 | 错 |
| `neutral` | 没有表态 | 错，单独统计比例 |
| `other` / `invalid` | 列表外的意思，或不是译文 | 错，单独统计比例 |

- `DisAcc = target 的条数 / 全部条数`。
- core 样本按定义不存在可接受的中性译法（SPEC 01 §7），所以不表态算错。
- 评审：`verify.map_judges` 个文本评审，一致才采用，不一致算 `other`。评审模型不能是被评测的模型，也不能是 teacher。
- `question_intonation` 类型优先用规则判定（中文问句规则），并和评审结果对照，报告两者的一致率。
- 通道 O 的陈述对照样本另算**陈述误判率**：被译成疑问句的比例。

### 3.2 其他指标

| 指标 | 定义 | 在哪个子集上 |
|---|---|---|
| 成对准确率 | 自然最小对的两条都判 `target` 才算对 | core-test 里的最小对 |
| 跟随率 | `T+V_shuf` 下的输出，相对同一系统 `T_neutral` 的输出，读法发生改变的比例 | control-test（越低越好） |
| 模态增益 | `DisAcc(T+V+A) − DisAcc(T_neutral)` 等差值 | core-test，并按归因分层 |
| chrF | sacreBLEU 的 chrF，字符级（`word_order = 0`） | 全部子集；参考是 silver 时注明 |
| BLEU | sacreBLEU，`tokenize = zh`，只作参考 | 同上 |
| COMET | `metrics.comet` | silver 参考和 WMT25 人工参考分开报告 |
| QE | `qe.primary` | 全部子集 |
| 成本 | 每条样本的延迟、峰值显存、帧数、输入 token 数 | — |

- 中文目标不报不分词的 chrF++：它比 chrF 多出来的词 n-gram 依赖空格分词，对中文不成立。
- 用 silver 参考（teacher 的译文）算出来的 chrF 和 COMET，衡量的是“和 teacher 有多像”。它们只作辅助，结论以 DisAcc 和外部人工参考为准。

---

## 4. 统计

| 场景 | 方法 |
|---|---|
| 一个比例的置信区间 | Wilson 95% |
| 同一批样本上两个条件的对错比较 | McNemar 精确检验，报告 b、c 和 p 值 |
| 一组比较的多重校正 | Holm |
| 连续分数（chrF、COMET）的差 | 配对 bootstrap，10,000 次，种子 2026，95% 置信区间 |
| 成对准确率的置信区间 | 以“对”为单位做 bootstrap |
| 多个种子的训练结果 | 报告均值和每个种子的值；检验用合并后的预测逐条比较 |

- 样本少于 20 的格子在表里标“样本太少”，不下结论。
- **功效**：core-test 的规模按 McNemar 检验估算。设两个条件判断不一致的样本占 ψ，其中差值为 δ，所需样本数约为 `n = (1.96·√ψ + 0.84·√(ψ − δ²))² / δ²`。ψ = 0.30、δ = 0.10 时约 233 条，所以目标定为 `split.core_test_target`。P5 用试点测得的 ψ 重算，并在报告里写出“当前规模能检出的最小差异”。

---

## 5. 报告里必须有的表

`reports/baseline_results.md` 和 `reports/ablation_results.md` 至少包含：

1. **同骨干消融表**：行是 §2 的条件，列是 DisAcc（core 全体、`core_strict`、`core_beyond_asr`）、陈述误判率、跟随率、chrF、COMET；每个数字带置信区间。
2. **按归因分层**：`visual`、`audio`、`either`、`both_needed` 各自的 DisAcc。预期：`T+V` 主要提升 `visual`，`T+A` 主要提升 `audio`。如果不是这样，要解释。
3. **按歧义类型分层**。
4. **比较表**：`T_neutral` 对 `T+V`、对 `T+A`、对 `T+V+A`；`T+V+A` 对 `T+V_shuf`；`T_asr+desc_auto` 对 `T_asr+V+A`。每行有 b、c、原始 p 值、Holm 校正后的 p 值。
5. **三个学生的对照表**（§8）。
6. **外部测试表**：WMT25 日→中上的 chrF 和 COMET。
7. **成本表**。

每张表下面写明：数据版本、split 版本、`run_id`。没跑的格子写“未运行”和原因。

---

## 6. 导出格式

ms-swift 的 JSONL：`messages` 加 `videos`、`audios` 两个路径列表；用户消息里用 `<video>`、`<audio>` 标记位置。视频和音频作为两个独立文件传入。

`T+V+A` 的训练样本（示意，内容虚构）：

```json
{"messages": [
  {"role": "system", "content": "你是专业的日语→简体中文口语翻译。只输出译文本身，写成一行，不加引号、解释、注释或备选译法。"},
  {"role": "user", "content": "<video><audio>前文：\n[S1] これ、運ぶの手伝おうか？\n当前台词（S2）：いいよ。\n请结合随附的视频画面和音频。请把“当前台词”翻译成自然的简体中文口语。只输出译文。"},
  {"role": "assistant", "content": "不用了。"}],
 "videos": ["data/processed/clips_v/ja-3f9a1c2b7d10.mp4"],
 "audios": ["data/processed/audio_gated/ja-3f9a1c2b7d10.wav"]}
```

| 导出文件 | `<video>` | `<audio>` | 文本 |
|---|---|---|---|
| `train_T.jsonl` | 无 | 无 | 见 §7.2 |
| `train_TVA.jsonl` | 有 | 有 | 同上 |
| `train_TVA_shuf.jsonl` | 有（错配） | 有（错配） | 同上 |
| `eval_<条件>.jsonl` | 按 §2 | 按 §2 | 按 §2；`assistant` 留空 |

导出器的硬规则：

- 用户消息里不能出现训练目标、读法、分桶标签、线索、屏幕上的中文文字。导出后自动扫描。
- 评测文件不带 `assistant` 内容；参考译文单独存放。
- 文本提示与构建数据时用的 `translate_ja_zh` 模板一致。

---

## 7. 训练配方（起点，P6 在 dev 上调整）

### 7.1 环境与参数

参数名以实际安装的 ms-swift 版本为准。下面的起点来自 ms-swift 官方的 Omni 训练示例（`examples/train/multimodal/omni/sft.sh`）：

| 项 | 起点 | 说明 |
|---|---|---|
| 模型 | `students.main` | — |
| 微调方式 | LoRA，rank 8，alpha 32，目标模块 `all-linear`；冻结视觉编码器和对齐层 | 之后可以试 rank 16–64 |
| 精度 | bfloat16 | — |
| 学习率 | 1e-4，warmup 5% | — |
| 批量 | 每卡 1，梯度累积到等效 16 | — |
| 轮数 | 1–3，看 dev | — |
| `ENABLE_AUDIO_OUTPUT` | 0 | 不需要语音输出，省显存 |
| `FPS_MAX_FRAMES` | 12 起步 | 与评测时保持一致 |
| `VIDEO_MAX_PIXELS` | 50176（224×224）起步，另试 200704（448×448） | 分辨率太低可能看不清表情和小动作，在 dev 上比较 |
| 最大长度 | 2048 | 帧数和分辨率提高后要检查是否截断 |

### 7.2 训练数据的组成

core 样本在自然数据里只占很小一部分。已有研究（3AM）发现，歧义样本太少时，模型会学会忽略图像。所以不能直接按自然比例训练。

| 设置 | 起点 | 目的 |
|---|---|---|
| 采样比例 | core : general : control = 1 : 2 : 1（对 core 上采样） | 让模型有理由去看、去听 |
| 句末标点丢弃 | 训练输入有 50% 的概率用 `text_neutral`，其余用 `text_asr` | 不让模型只靠问号判断疑问 |
| 模态丢弃 | general 样本有 20% 的概率不给媒体 | 保住纯文本翻译能力 |

这些比例是起点，只在 dev 上调整，并记入 `decision_log.md`。三个学生（§8）必须用同一组设置。

### 7.3 显存

官方示例的四模态演示标注为 4 张卡、每张约 35 GB。实际需求随帧数、分辨率和序列长度变化，P6 M6.4 先用 50 步短跑记录峰值显存，再决定正式配置。

---

## 8. 三个学生的对照（回答“模型是否学会了用非语言线索”）

| 学生 | 训练输入 | 训练目标 |
|---|---|---|
| `S_T` | 只有文本 | 相同（teacher 的训练目标） |
| `S_TVA` | 文本 + 画面 + 声音 | 相同 |
| `S_TVA_shuf` | 文本 + **错配的**画面和声音 | 相同 |

- 三者的骨干、步数、种子、采样设置（§7.2）全部相同。预算允许时每个跑 3 个种子。
- `S_TVA_shuf` 的作用：它和 `S_TVA` 看到的 token 数量和训练量一样，但媒体和台词对不上。如果 `S_TVA` 的优势只是来自“多了些 token”或“多训了几步”，`S_TVA_shuf` 也会一样好。
- **结论条件**：只有当 `S_TVA` 在 core-test 上的 DisAcc 显著高于 `S_T`，同时 `S_TVA_shuf` 与 `S_T` 没有显著差别，才能写“模型学会了使用非语言线索”。否定的结论同样如实写出。
- 另外要看两项副作用：`S_TVA` 在 control-test 上的跟随率有没有升高；在 general-test 和 WMT25 上的整体质量有没有下降。

**能说什么、不能说什么**：训练目标来自 teacher，所以学生最多学到 teacher 的水平。这个实验能回答“同样的目标，给不给媒体有没有差别”，不能回答“学生的翻译是否超过 teacher”。

---

## 9. 冒烟检查与已知问题

每次正式训练前先跑 50 步，检查：

1. loss 不为 0，并且在下降。
2. 用保存的 checkpoint 生成 10 条，输出不为空，是中文。
3. 抽 10 条训练样本，确认输入里确实有视频 token 和音频 token（按条件）。
4. 峰值显存和每步耗时已记录。

已知问题（2026-11 核对）：

| 问题 | 来源 | 本项目的对策 |
|---|---|---|
| 开启内嵌音频（`USE_AUDIO_IN_VIDEO=1`）微调 Qwen3-Omni 时，个别片段会让解码器挂死，训练静默卡住 | ms-swift issue #9507 | 音频和视频分开传入；所有媒体在 S5 用子进程加超时重新编码过 |
| 音视频成对输入时出现 loss 为 0、输出为空 | ms-swift issue #6069；维护者认为多半是帧数或分辨率太高挤占了长度预算 | 冒烟检查第 1、2 项；控制帧数和分辨率；检查是否截断 |

---

## 10. 实验记录

每次推理或训练一个目录 `experiments/<YYYYMMDD-HHMM>_<名称>_<短哈希>/`：

| 文件 | 内容 |
|---|---|
| `manifest.json` | git commit、数据集版本、split 版本、模型 id 和 revision、提示词 id 和 version、全部配置、种子、包版本、GPU 型号、精度、LoRA 配置、帧率和分辨率、峰值显存、耗时 |
| `predictions.jsonl` | `sample_id`、`condition`、`output`、`latency_s`、`cached` |
| `scores.jsonl` | `sample_id`、`condition`、`reading`、`disacc_class`、`chrf`、`comet`、`qe` |
| `summary.csv` | 条件 × 子集的指标和置信区间 |
| `comparisons.csv` | 比较名、指标、差值、置信区间、b、c、原始 p、Holm p |

规则：阈值、提示词、超参数只在 dev 和试点数据上调；test 只在最后跑一次，`run_id` 记进 `reports/progress.md`。报告里的每个数字都能在某个 `run_id` 的 `summary.csv` 或 `comparisons.csv` 里找到。
