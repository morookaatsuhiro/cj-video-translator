# SPEC 06 — 来源、授权、隐私、第三方许可、发布

> v0.3 | 2026-11-16  
> 什么时候读：P1 找来源、任何下载之前、要调用外部 API 之前、P7 准备发布时。  
> 说明：本文件是项目内的操作规则，不是法律意见。拿不准的地方由研究者向所在机构确认。

---

## 1. 来源台账 `manifests/source_registry.csv`

一行一个来源（一个频道、一部作品、一个语料库或一批自录素材）。

| 列 | 内容 |
|---|---|
| `source_registry_id` | `RIGHTS-000001` 起编号 |
| `source_name`、`source_uri` | 名称和地址 |
| `source_type` | `own`（自有或自录）、`licensed`（书面授权）、`open_license`（开放许可）、`research_corpus`（研究许可语料）、`external_test`（外部测试集） |
| `rights_holder` | 权利人；视频、音轨、字幕的权利人不同时分别写 |
| `license_name`、`license_evidence` | 许可证名称；证据（文件路径、网页存档或邮件） |
| `allow_view`、`allow_download`、`allow_cache`、`allow_clip`、`allow_transcribe`、`allow_translate`、`allow_train`、`allow_derive`、`allow_redistribute` | 九项操作各自的状态：`yes` / `no` / `unclear` |
| `platform_terms_note` | 平台服务条款的要点和评估结论 |
| `intended_use` | `internal`、`restricted_share`、`public_release` |
| `contains_minors_or_private` | `yes` / `no` / `unknown`；`yes` 时默认不用 |
| `has_manual_ja_caption`、`has_burned_in_subtitle` | 是否有人工日语字幕；是否有烧录字幕 |
| `est_hours`、`est_dialog_density` | 估计时长和对话密度 |
| `withdrawal_contact`、`withdrawal_process` | 撤回联系方式和处理流程 |
| `approved_by`、`approved_at`、`approval_note` | **只能由研究者填写** |

规则：

- `allow_download`、`allow_clip`、`allow_transcribe`、`allow_translate` 有任何一项不是 `yes` 的来源，不能进 `configs/approved_sources.yaml`。
- `allow_train` 不是 `yes` 的来源只能用于评测。
- `allow_redistribute` 不是 `yes` 的来源，它的媒体和完整字幕不能进入任何公开版本。

## 2. 审批流程

1. Agent 收集元数据和许可信息，填好除审批列以外的各列，把证据存到 `docs/license_evidence/`。
2. Agent 在 `docs/HUMAN_TASKS.md` 的 H2 里列出待审批的来源、建议和风险。
3. 研究者逐个来源决定，填写审批列，并把批准的来源写进 `configs/approved_sources.yaml`。
4. `license_check` 在每个阶段开始时运行：`data/raw/` 里的每个文件都必须属于已批准的来源。

Agent 不能自己判定“这个可以用”，也不能在获批前下载媒体。能看到不等于可以下载、训练或再分发。

## 3. 各类来源的注意事项

| 类型 | 例子 | 注意 |
|---|---|---|
| 自有或自录 | 研究者拍摄的对话、短剧 | 参与者书面同意，写明用途（研究、模型训练、是否公开）；用匿名编号 |
| 书面授权 | 权利人邮件或合同授权 | 保存授权原文；按授权范围填九项操作 |
| 开放许可 | CC BY 等许可的视频；YODAS 这类只收录开放许可视频的语料 | 许可证之外还要看平台条款；署名要求写进数据卡；视频里的背景音乐等第三方内容可能不在许可范围内 |
| 研究许可语料 | EVA 的视频（发邮件申请，仅限研究，禁止再分发）；CEJC（需申请，含映像，禁止整体复制） | 严格按条款使用；媒体不再分发；衍生标注能否公开要看条款 |
| 外部测试集 | WMT25、WMT26 General MT | 仓库没有写许可证，按“仅研究、不再分发”处理；只用于评测 |
| 影视剧、动漫、平台搬运内容 | — | 默认不用。只有研究者确认依法有权使用时，才能作为仅内部使用的来源，且永远不进公开版本 |

## 4. 第三方模型与工具的许可

使用前逐项核对现行条款，把结论记在 `NOTICE.md`。下表是 2026-11 的记录，标“核对”的没有确认过原文。

| 对象 | 许可 | 对本项目的影响 |
|---|---|---|
| Qwen2.5-Omni-7B、Qwen3-Omni-30B-A3B、Qwen3-VL-8B | Apache-2.0 | 输出可以作训练目标；可以微调 |
| PLaMo 3 Translate | PLaMo Community License（核对现行版本） | 旧版条款规定：用其输出训练的模型算派生模型，名称须含“PLaMo”，并有商业收入上限。**默认不把它的输出放进训练目标**，只作评测时的文本参照 |
| Whisper | 宽松许可（GitHub 仓库写 MIT，Hugging Face 模型卡写 Apache-2.0；核对） | — |
| pyannote 说话人分离 | MIT，需在模型页接受条款 | H3 |
| CometKiwi | CC BY-NC-SA 4.0，需接受条款 | 仅研究；报告里注明 |
| COMET（wmt22-comet-da）、MetricX-24 | Apache-2.0 | — |
| Parselmouth（Praat） | GPL-3.0 | 作为可选依赖或独立脚本调用；如果代码要以宽松许可证发布，不要把它变成硬依赖 |
| VOICEVOX | 引擎和各角色声音分别有条款，通常要求署名 | 只用于探针和可选的反事实音频；音频不再分发 |
| CoMMuTE | CC BY-NC-SA 4.0（README）；仓库许可文件的写法略有出入，核对 | 仅研究；只作探针 |
| 闭源 API（任何厂商） | 各自的服务条款 | 见 §5 |
| 其余模型（副路 ASR、评审、核验、强文本系统） | 核对 | 选定时登记在 `configs/models.yaml` |

## 5. 调用外部 API 的规则

调用任何第三方云端模型之前，逐项确认并记入 `decision_log.md`：

1. **输出能否用于训练**：有些服务禁止用输出训练模型。不允许的，它的输出不能成为训练目标，只能作评审或对照。
2. **数据去向**：是否跨境传输，服务方是否保留或用于训练。
3. **媒体权限**：该来源的许可是否允许把片段交给第三方处理。研究许可语料和未明确允许的来源一律不上传。
4. **费用**：`--dry-run` 给出预计费用；超过 `budget.max_api_cost_without_approval_cny` 先请研究者确认。
5. **密钥**：只放在 `.env`，代码通过环境变量读取，日志里不打印。

默认方案是全部用本地开源模型，不需要外部 API。

## 6. 隐私

- 不收集、不存储真实姓名。说话人一律用匿名编号。
- 不做人脸识别或声纹识别来确认身份。
- 人物外观只在翻译需要时（例如“他/她”）记录为“外观呈现”，写在线索的 `observation` 里，不推断身份、年龄、职业等属性。
- 来源标记为含未成年人或私人场景的，默认不用，记 `E_PRIVACY`；要用必须由研究者单独批准。
- 抽检页面和 QA 预览只在本地打开，不上传。

## 7. 发布分级

| 级别 | 内容 | 条件 |
|---|---|---|
| R0 仅内部 | 全部数据和媒体 | 默认 |
| R1 公开代码和统计 | 代码、schema、提示词、配置、统计报告、Data Card | 不含任何样本内容 |
| R2 公开索引和标注 | 再加：来源标识、时间戳、标签、线索的类型和时间（不含台词全文和译文，除非许可允许） | 逐个来源核对 `allow_derive` 和 `allow_redistribute`；确认无法从公开内容还原出受限媒体或完整字幕 |
| R3 公开获许可的子集 | 再加：`allow_redistribute = yes` 的来源的媒体、台词和译文 | 研究者批准（H6）；附许可文件和署名 |
| R4 受限研究共享 | 向签署协议的研究者提供 R0 的一部分 | 研究者批准；协议范围不超过原始许可 |

每个公开版本都要有：版本号、sha256 清单、Data Card、许可文件、受限内容排除清单、撤回渠道。`data/releases/` 在 H6 之前保持为空。

**版本号**：`主.次.修`。test 有任何变化升主版本；train 或 dev 增加数据升次版本；不涉及 test 的标签修正升修订号。旧版本保留，方便结果对比。

## 8. 撤回

收到权利人或参与者的撤回请求时：

1. 在 `source_registry.csv` 里把该来源标为撤回，从 `approved_sources.yaml` 移除。
2. 删除对应的原始媒体和全部衍生文件（片段、音频、帧、样本记录）。
3. 受影响的数据集版本发布新版本；如果涉及 test，升主版本。
4. 在 `decision_log.md` 记录日期、范围和处理结果。
