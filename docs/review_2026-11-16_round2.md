# 第二轮审查：v0.2 → v0.3

> 日期：2026-11-16  
> 对象：上一轮写出的 `AGENTS.md` v0.2 和 `PHASED_GOALS.md` v0.2（存档在 `docs/archive/`）。  
> 第一轮审查（v0.1 → v0.2）见 `docs/review_2026-11-16.md`。

---

## 0. 结论

1. **v0.2 有一个会直接导致规则失效的缺陷：`AGENTS.md` 太长。** 它有 47,424 字节，而 Codex 默认只读取 AGENTS.md 的前 32 KiB，超出的部分被静默丢弃。v0.2 排在后面的 schema、质量检查、评测、合规、工程规范，Codex 根本读不到。v0.3 把它拆成一份 19.6 KB 的规则文件，加上按需阅读的详细规范。
2. **v0.2 的自动判定规则有 8 处方法漏洞**，会让“视频有用”的结论站不住。最重要的三处：没有排除“不表态也算对”的中性译法；音频窗口里能听到别人说的话；线索核验用的是诱导式的是非题。v0.3 逐条补上。
3. **v0.2 没有考虑“teacher 自己就不会用语调”的情况。** 你的课程汇报里引用过这类结果：端到端模型听得到语调，却常常不改译文。v0.3 增加了两样东西：正式开工前的资格探针，和不依赖 teacher 的语调客观通道。
4. **v0.2 很多内容只有描述，没有落到文件。** 提示词、阈值、原因代码、每个阶段的输入输出，Agent 都得自己编。v0.3 把它们写成了配置文件、提示词文件和逐阶段的规范。

---

## 1. v0.2 的问题清单

### 1.1 结构问题

| 编号 | 问题 | v0.3 的处理 |
|---|---|---|
| S1 | `AGENTS.md` 47 KB，超过 Codex 默认的 32 KiB 读取上限 | 拆分：`AGENTS.md`（19.6 KB，规则和索引）+ `docs/spec/` 六份规范 + `configs/`；`make check` 检查字节数 |
| S2 | 没有告诉 Agent 每次会话怎么开始、怎么结束、受阻时怎么办 | `AGENTS.md` §1 的会话流程、`AGENTS.md` §13 的完成定义 |
| S3 | Claude Code 读的是 `CLAUDE.md` | 增加 `CLAUDE.md`，用一行导入 `AGENTS.md` |
| S4 | 阈值散落在正文里，改一处要改多处 | `configs/pipeline.yaml` 是唯一来源，文档只写键名 |
| S5 | 提示词只有描述，没有文本 | `configs/prompts/prompts_v1.yaml`，10 个模板，带版本号 |
| S6 | 拒收原因和降级原因没有统一的代码 | `docs/spec/04_schema.md` §5：18 个拒收代码、20 个降级代码 |
| S7 | Gate 只写了“通过条件”，没写没通过怎么办 | `PHASED_GOALS.md` 每个阶段加了“没通过怎么办” |
| S8 | 阶段太粗，一个阶段就是一大段任务 | 47 个里程碑，每个有“完成证据” |

### 1.2 方法问题

| 编号 | v0.2 的做法 | 问题 | v0.3 的处理 |
|---|---|---|---|
| M1 | 只要 teacher 的多模态译法和纯文本译法不同，再经验证就算 core | 如果有一种不表态的中文译法在两种理解下都对（例如「すみません」译成“不好意思”），纯文本模型不看视频也能得分，这条样本测不出视频的作用 | **中性译法检验**（C3）：存在中性译法的样本不进 core |
| M2 | 没说清四种条件用哪种文本、训练目标用哪种文本 | 识别出的问号留在文本里，音频的作用就被文本吃掉；去掉问号再生成训练目标，teacher 听不出语调时目标就是错的 | 分桶判定一律用 `text_neutral`；训练目标用 `text_asr` 加画面加声音；另打 `asr_punct_sufficient` 标记 |
| M3 | 片段的音频和画面一样长 | 窗口里能听到别人的话，尤其是台词之后的回答。“音频有用”可能只是多听到了几句台词 | **音频门控**：只保留目标台词那一段 |
| M4 | 线索核验是让另一个模型回答“是否在摆手” | 这种是非题容易得到“是” | **两步核验**：先开放描述，再做文本蕴含判断；另测核验器在错配片段上的误肯定率 |
| M5 | 评审团在多模态译文和“最强的文本译文”之间二选一 | 两个译文出自不同模型，评审可能只是选了更通顺的那个 | 两个译文都出自 teacher；增加“错配视频下的全票率”诊断 |
| M6 | “异家族”只看开发方 | 很多开源多模态模型的语言骨干来自 Qwen，例如 MiniCPM-o 4.5 用的是 Qwen3-8B | 评审团至少一个模型的语言骨干不是 Qwen 系；`models.yaml` 登记骨干来源 |
| M7 | 候选样本靠文本模型判断“有没有歧义”加规则词表 | 文本模型凭空想读法容易漏；而且每条样本都要调用一次 | **预筛**：每条样本本来就有训练目标和纯文本译文，直接比较两者是否不同；漏判率在随机样本上实测 |
| M8 | 反事实测试把“同一视频的其他时段”和“其他来源的片段”混着用；`either` 归因用同时换画面和声音来测 | 两种替换回答的是不同问题；同时替换测不出单一模态的作用 | `CF_V_far` 用于判定，`CF_V_near` 用于区分“瞬时动作”和“静态场景”；按归因分别测试；在 control 上测噪声底 |
| M9 | 上下文只有前文 | 审稿人会问：看了后一句不就知道了吗 | `future_context_sufficient` 标记和 `core_strict` 子集 |
| M10 | 默认 teacher 能听出语调 | 已有研究和你汇报里的结论都指向相反的情况 | **资格探针**（合成语调最小对、人工字幕的自然语音、图像消歧）；**通道 O**（ASR 问号加句末音高） |
| M11 | 把 ASR 的问号当作声学证据 | 有些 ASR 的标点是纯文本后处理加的，不是听出来的 | `native_punctuation` 标记；探针 A1 实测 |
| M12 | 主指标没有定义“不表态的输出”怎么算 | 会把骑墙的译文算对 | DisAcc 分四类：目标、干扰、不表态、其他；只有第一类算对 |
| M13 | 没有训练数据怎么配的说明 | core 在自然数据里占比很小，按自然比例训练，模型会学会忽略媒体 | 训练配方：core 上采样、句末标点丢弃、模态丢弃；三个学生共用同一组设置 |
| M14 | 依赖 `use_audio_in_video` 和内嵌音轨 | ms-swift 有内嵌音频解码挂死的已知问题 | 画面和音频分开传入；解码放在带超时的子进程里；训练前 50 步冒烟检查 |
| M15 | 没有分析日→中方向哪些歧义真的需要视频 | 敬语和こそあ指示词在日→中方向多半不需要视频，按中→日的经验规划会高估产出 | `docs/spec/01_task.md` §6：逐类说明，并标明哪些是待检验的假设 |
| M16 | 单独调用模型判断“语义差异类型” | 多余，读法映射已经给出同样的信息 | 删除；差异由读法编号是否相同得出 |

---

## 2. 这一轮新核实的事实

| 事实 | 核实结果 | 用在哪里 |
|---|---|---|
| Codex 读取 AGENTS.md 的上限 | 官方说明：合并后的大小达到 `project_doc_max_bytes` 就不再读取，默认 32 KiB；多篇第三方文章报告超出的部分会被直接截断，没有任何提示；可以在 `~/.codex/config.toml` 里调大 | S1 |
| Codex 的 `/goal` | 0.128.0 加入；`/goal pause`、`resume`、`clear`；命令不出现时在 `[features]` 下设 `goals = true`；官方建议目标要有可验证的停止条件 | `PHASED_GOALS.md` 的任务提示 |
| Claude Code 与 AGENTS.md | 官方做法是在 `CLAUDE.md` 里写 `@AGENTS.md` 导入；较新的版本在没有 `CLAUDE.md` 时会回退读取 `AGENTS.md` | S3 |
| MiniCPM-o 4.5 | 2026 年 2 月开源，9B，输入含图像、视频、文本、音频；语言骨干是 Qwen3-8B | M6 |
| CoMMuTE 的中文部分 | 已扩展到英→中（Findings of NAACL 2025）；README 写的许可是 CC BY-NC-SA 4.0，仓库许可文件的写法略有出入 | 资格探针 V1 |
| VOICEVOX 的句末上扬 | 合成接口有 `enable_interrogative_upspeak` 参数；各角色声音要求署名 | 资格探针 A1 |
| ms-swift 的 Omni 训练示例 | `examples/train/multimodal/omni/sft.sh`：Qwen2.5-Omni-7B，LoRA rank 8、alpha 32，冻结视觉编码器和对齐层，`VIDEO_MAX_PIXELS=50176`、`FPS_MAX_FRAMES=12`，标注 4 张卡、每张约 35 GB | 训练配方的起点 |
| ms-swift 的已知问题 | issue #9507：开启内嵌音频微调 Qwen3-Omni 时个别片段让解码器挂死；issue #6069：音视频成对输入时 loss 为 0、输出为空，维护者认为多半是帧数或分辨率太高 | M14 |
| Qwen2.5-Omni 关闭语音输出 | 官方 README：`disable_talker()` 约省 2 GB 显存 | teacher 和学生的部署 |

这个环境仍然访问不了 huggingface.co、arxiv.org 和 statmt.org。上表里涉及这三个站点的条目（MiniCPM-o、CoMMuTE 论文）是通过搜索结果和 GitHub 页面核对的。

---

## 3. v0.3 的文件结构

```text
AGENTS.md                    规则和索引（19.6 KB，上限 32 KiB）
CLAUDE.md                    导入 AGENTS.md
PHASED_GOALS.md              P0–P7，47 个里程碑
README.md                    从哪里开始
configs/
  pipeline.yaml              全部阈值
  models.yaml                模型角色
  mining_lexicon.yaml        规则挖掘词表
  prompts/prompts_v1.yaml    10 个提示词模板
  probes/intonation_frames.txt  资格探针的起始句表
docs/
  spec/01_task.md            任务定义
  spec/02_pipeline.md        S0–S13
  spec/03_rules.md           判定规则
  spec/04_schema.md          数据格式
  spec/05_eval_train.md      探针、评测、训练
  spec/06_compliance.md      合规
  HUMAN_TASKS.md             H1–H6
  decision_log.md            D-001 – D-014
  review_2026-11-16.md       第一轮审查
  review_2026-11-16_round2.md  本文件
  archive/                   v0.1、v0.2 原文
reports/progress.md          进度和看板
```

---

## 4. 需要你决定的事

都列在 `docs/HUMAN_TASKS.md` 的 H1 里，这里只列会明显改变工作量的三项：

| 决定 | 选项 | 影响 |
|---|---|---|
| 是否启用通道 O | 启用（默认）/ 不启用 | 不启用时，如果 teacher 听不出语调，数据集里就几乎没有语调类的 core 样本 |
| 是否做 150 条抽检 | 做（默认）/ 不做 | 不做时数据集标 `unaudited`，自动标签的准确率只能靠诊断指标和外部校准间接说明 |
| 是否允许外部 API | 不用（默认）/ 允许 | 本地找不到满足“非 Qwen 骨干”的评审模型时，要么用 API，要么接受评审团只有一个家族 |

---

## 5. 一致性检查

写完后用脚本检查了下面这些，全部通过：

| 检查 | 结果 |
|---|---|
| `AGENTS.md` 字节数 | 19,626，小于 32,768 |
| 4 个 YAML 文件能解析 | 通过 |
| 文档里用反引号写的配置键、字段路径 | 都能在 `pipeline.yaml`、`models.yaml` 或样本示例里找到 |
| 文档引用的提示词 | 10 个模板都存在，都带 `data_guard`；翻译模板的模态说明正好覆盖四种条件 |
| 原因代码 | 文档里用到的代码都在 `docs/spec/04_schema.md` §5 里有定义 |
| `cue_type` 和歧义类型 | 提示词、schema、挖掘词表三处一致（16 种、9 种） |
| 跨文件的章节引用 | 都存在 |
| 样本示例 | 能解析；时间区间、门控区间、留白与配置一致；按 C4 重算的文本先验支持度、归因、反事实、各属性标记都与示例里写的标签一致 |
| 里程碑编号 | 47 个，无重复 |

这只是文档层面的检查。项目代码还没有写，判定规则还没有在任何真实数据上跑过。

---

## 6. 仍然存在的局限

这些是方案本身的局限，换文档写法解决不了，数据卡和论文里要写明：

1. **产出率是下界。** 通道 M 只能找到 teacher 看得出、听得出的线索。
2. **蒸馏上限。** 训练目标来自 teacher，学生模型最多学到 teacher 的水平。实验能回答“同样的目标，给不给媒体有没有差别”，不能回答“学生是否超过 teacher”。
3. **自动标签不等于人工标签。** 抽检只给出准确率的估计，不会把每条样本都变成人工确认的。
4. **默认阈值都没有数据支持。** `configs/pipeline.yaml` 里标“待校准”的值，要到 P2–P4 才能定。
5. **通道 O 只覆盖语调疑问句。** 其他音频线索（语气、情绪）仍然只能靠通道 M。
6. **合成探针和自然语音有差距。** 探针 A1 用 TTS，通过它不保证在真实对话里也听得出语调，所以另设了用人工字幕的探针 A2；没有带人工字幕的来源时 A2 做不了。

---

## 7. 来源

- Codex 的 AGENTS.md 说明：https://developers.openai.com/codex/guides/agents-md
- Codex 的 goal 模式：https://developers.openai.com/codex/use-cases/follow-goals
- Claude Code 的 CLAUDE.md 说明：https://code.claude.com/docs/en/memory
- MiniCPM-o：https://github.com/OpenBMB/MiniCPM-o
- CoMMuTE：https://github.com/MatthieuFP/CoMMuTE ；扩展到中文的论文 https://arxiv.org/abs/2407.13579
- VOICEVOX 引擎：https://hub.docker.com/r/voicevox/voicevox_engine
- ms-swift 的 Omni 训练示例：https://github.com/modelscope/ms-swift/tree/main/examples/train/multimodal/omni
- ms-swift issue #9507：https://github.com/modelscope/ms-swift/issues/9507
- ms-swift issue #6069：https://github.com/modelscope/ms-swift/issues/6069
- Qwen2.5-Omni README：https://github.com/QwenLM/Qwen2.5-Omni
