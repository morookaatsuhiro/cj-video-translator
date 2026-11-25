# SPEC 04 — Canonical Schema、枚举、原因代码

> v0.3 | 2026-11-16 | schema_version `0.3.0`  
> 什么时候读：写 pydantic 模型和 JSON Schema、写 validator、读写样本记录、给样本记原因代码时。

---

## 1. 约定

- 一行一条台词样本，UTF-8 JSONL。
- **时间**：一律是父视频的绝对毫秒，字段名以 `_abs_ms` 结尾，`t_end > t_start`。片段内的相对秒数只在调用模型或导出时临时计算，不落盘（证据里 teacher 返回的 `t_start_s` 要立刻换算成绝对毫秒再保存）。
- **三值和多值枚举**：`yes / no / uncertain / unknown / not_tested`。不允许用空字符串或 `null` 代替“不知道”。
- **只追加**：每个阶段只添加自己负责的字段，不改别的阶段写的字段。要更正时写新版本的记录，保留旧记录。
- **合成数据**：`is_synthetic = true` 的记录只能放在 `tests/fixtures/`，文件名含 `synthetic`。validator 在 `data/` 下发现合成记录直接报错。
- **隐私**：不存真实姓名；`speaker_id` 形如 `<parent_video_id>_spk<N>`。
- 核心 schema 里不写任何训练框架专用的字段，训练格式由导出器生成。

---

## 2. 完整示例

**虚构的 schema 演示，不是真实数据。**

```json
{
  "schema_version": "0.3.0",
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
           "audio_gate_start_abs_ms": 754100, "audio_gate_end_abs_ms": 755800,
           "path_v": "data/processed/clips_v/ja-3f9a1c2b7d10.mp4",
           "path_audio_gated": "data/processed/audio_gated/ja-3f9a1c2b7d10.wav",
           "path_preview": "data/processed/preview/ja-3f9a1c2b7d10.mp4",
           "sha256_v": null, "sha256_audio_gated": null,
           "scene_cut_in_window": false, "masked": false},
  "source_text": {
    "asr": [{"system": "asr-primary@<rev>", "text": "いいよ。", "native_punctuation": true,
             "avg_logprob": -0.21, "no_speech_prob": 0.02, "compression_ratio": 1.1, "lang_prob": 0.99},
            {"system": "asr-secondary@<rev>", "text": "いいよ", "native_punctuation": true}],
    "asr_agreement_cer": 0.0,
    "asr_hallucination": false,
    "transcript_tier": "auto_agreed",
    "text_asr": "いいよ。",
    "text_neutral": "いいよ。"
  },
  "context": {"policy": "prev3_within30s_v1",
              "prev": [{"speaker_tag": "S1", "text": "これ、運ぶの手伝おうか？",
                        "t_start_abs_ms": 751800, "t_end_abs_ms": 753200}],
              "target_speaker_tag": "S2",
              "next": [{"speaker_tag": "S1", "text": "ドア、開けとくね。",
                        "t_start_abs_ms": 756900, "t_end_abs_ms": 758000}]},
  "onscreen_text": {"target_subtitle": false, "source_caption": false, "scene_text": false, "items": []},
  "mining": {"tags": ["ii_kekkou", "short_utt"], "priority": 0.9},
  "form": {"decl_no_q": true,
           "final_f0": {"class": "fall", "delta_st": -3.1, "voiced_frames": 22},
           "asr_qmark": [false, false]},
  "prefilter": {"relation": "different", "which": "speech_act", "judge": "<text-judge-1>@<rev>"},
  "text_analysis": {"judge": "<text-judge-1>@<rev>", "prompt": "text_readings@1",
                    "text_ambiguous": "yes", "ambiguity_types": ["speech_act"],
                    "neutral_exists": "no", "neutral_zh": null,
                    "readings": [{"reading_id": "r1", "gloss": "接受帮助", "zh": "好啊，谢谢。",
                                  "cue_hint": "点头、微笑、把东西递过去", "source": "apriori"},
                                 {"reading_id": "r2", "gloss": "谢绝帮助", "zh": "不用了。",
                                  "cue_hint": "摆手、摇头、自己继续搬", "source": "apriori"}]},
  "candidates": [
    {"condition": "TARGET",   "model": "<teacher>@<rev>",   "prompt": "translate_ja_zh@1", "text": "不用了。",     "reading": "r2"},
    {"condition": "T",        "model": "<teacher>@<rev>",   "prompt": "translate_ja_zh@1", "text": "好啊。",       "reading": "r1"},
    {"condition": "T_strong", "model": "<text-mt-1>@<rev>", "prompt": "translate_ja_zh@1", "text": "好的。",       "reading": "r1"},
    {"condition": "T_strong", "model": "<text-mt-2>@<rev>", "prompt": "translate_ja_zh@1", "text": "没事，不用。", "reading": "r2"},
    {"condition": "T+V",      "model": "<teacher>@<rev>",   "prompt": "translate_ja_zh@1", "text": "不用了。",     "reading": "r2"},
    {"condition": "T+A",      "model": "<teacher>@<rev>",   "prompt": "translate_ja_zh@1", "text": "好啊。",       "reading": "r1"},
    {"condition": "T+V+A",    "model": "<teacher>@<rev>",   "prompt": "translate_ja_zh@1", "text": "不用了。",     "reading": "r2"},
    {"condition": "T_asr",    "model": "<teacher>@<rev>",   "prompt": "translate_ja_zh@1", "text": "好啊。",       "reading": "r1"},
    {"condition": "T+C_full", "model": "<teacher>@<rev>",   "prompt": "translate_ja_zh@1", "text": "好啊。",       "reading": "r1"},
    {"condition": "CF_V_far", "base_condition": "T+V", "model": "<teacher>@<rev>", "prompt": "translate_ja_zh@1",
     "text": "好啊。", "reading": "r1", "cf_source": "ja-77aa01bc9e3f"},
    {"condition": "CF_V_near", "base_condition": "T+V", "model": "<teacher>@<rev>", "prompt": "translate_ja_zh@1",
     "text": "好啊。", "reading": "r1", "cf_source": "SRC-000123@812000-817300"}
  ],
  "evidence": [{"evidence_id": "ev1", "modality": "visual", "cue_type": "gesture",
                "t_start_abs_ms": 754500, "t_end_abs_ms": 755400,
                "observation": "说话者一边摆手一边后退半步",
                "interpretation": "可能表示谢绝",
                "source": "model", "model": "<teacher>@<rev>", "prompt": "evidence_extract@1", "confidence": 0.8,
                "verification": {"method": "describe_entail",
                                 "describer": "<other-dev-vlm>@<rev>",
                                 "description": "右侧的人抬起右手左右摆了两下，同时向后退了一小步，面向左侧的人。",
                                 "entail_judge": "<text-judge-2>@<rev>",
                                 "answer": "yes"}}],
  "verification": {
    "map_agreement": true,
    "counterfactual": {"sensitive": "yes", "tests": [{"cf": "CF_V_far", "left_target_reading": true}]},
    "jury": [{"judge": "<jury-1>@<rev>", "order": "AB", "choice": "target"},
             {"judge": "<jury-1>@<rev>", "order": "BA", "choice": "target"},
             {"judge": "<jury-2>@<rev>", "order": "AB", "choice": "target"},
             {"judge": "<jury-2>@<rev>", "order": "BA", "choice": "target"}],
    "jury_unanimous": true,
    "objective": {"applicable": true, "signal": "statement", "target_consistent": true}
  },
  "translation": {"target": "不用了。", "target_condition": "TARGET", "target_model": "<teacher>@<rev>",
                  "normalized": "不用了。",
                  "qe": {"metric": "<qe-model>@<rev>", "score": 0.81},
                  "checks": {"lang_zh_hans": true, "no_kana": true, "format_ok": true, "length_ratio_ok": true}},
  "labels": {"bucket": "core", "core_channel": "model", "label_tier": "auto_verified",
             "modality_attribution": "visual", "visual_dependency": "yes", "audio_dependency": "no",
             "text_prior_support": 0.3333, "difficulty": "medium",
             "asr_punct_sufficient": "no", "future_context_sufficient": "no",
             "cue_scope": "momentary", "ocr_dependent": false, "attribution_revised": false,
             "teacher_perceived": null, "contrast_role": null,
             "contrast": {"target_reading": "r2", "distractor_readings": ["r1"], "pair_ids": []},
             "downgrade_reasons": []},
  "split": null,
  "provenance": {"pipeline_version": "0.3.0", "git_commit": null,
                 "stage_manifests": ["data/interim/s8_teacher/stage_manifest.json",
                                     "data/interim/s9_verify/stage_manifest.json"],
                 "created_at": "2026-11-16T00:00:00Z"}
}
```

---

## 3. 字段字典

| 块 | 字段 | 写入阶段 | 说明 |
|---|---|---|---|
| 顶层 | `schema_version`、`sample_id`、`is_synthetic`、`src_lang`、`tgt_lang` | S4 | `sample_id` 的算法见 SPEC 02 §0.3；语言用 BCP-47 |
| 顶层 | `parent_video_id`、`group_id`、`source_registry_id` | S1 | `group_id` 是切分单位（频道、系列或作品） |
| `utterance` | 起止时间、`speaker_id`、`speaker_confidence`、`overlap` | S4 | 说话人不确定时 `speaker_id = "unknown"` |
| `clip` | 窗口起止、门控起止、三个路径、sha256、`scene_cut_in_window`、`masked` | S5、S6 | 路径相对仓库根目录 |
| `source_text` | `asr[]`、`asr_agreement_cer`、`asr_hallucination`、`transcript_tier`、`text_asr`、`text_neutral` | S3、S4 | `asr[].native_punctuation` 决定它的问号能不能当声学证据 |
| `context` | `policy`、`prev[]`、`target_speaker_tag`、`next[]` | S5 | `next` 只给 `T+C_full` 用 |
| `onscreen_text` | 三个布尔标记、`items[]` | S6 | `items[]`：`text`、`bbox`、`t_abs_ms`、`lang`、`class`、`confidence` |
| `mining` | `tags[]`、`priority` | S7 | 只用于排序和分层 |
| `form` | `decl_no_q`、`final_f0`、`asr_qmark[]` | S7 | 通道 O 的输入 |
| `prefilter` | `relation`、`which`、`judge` | S8c | — |
| `text_analysis` | `text_ambiguous`、`ambiguity_types[]`、`neutral_exists`、`neutral_zh`、`readings[]` | S9a | `readings[].source`：`apriori` 或 `induced` |
| `candidates[]` | `condition`、`base_condition`、`model`、`prompt`、`text`、`reading`、`cf_source` | S8、S9 | `reading` 可以是读法编号、`neutral`、`other`、`invalid`、`uncertain` |
| `evidence[]` | 见 §4.3 | S9e、S9f | `observation` 和 `interpretation` 必须分开 |
| `verification` | `map_agreement`、`counterfactual`、`jury[]`、`jury_unanimous`、`objective` | S9 | `jury[].choice`：`target` / `other` / `both` / `neither` / `cannot_tell` |
| `translation` | `target`、`target_condition`、`target_model`、`normalized`、`qe`、`checks` | S8a、S11 | `target_condition` 必须是 `TARGET` |
| `labels` | 分桶、通道、等级、归因、属性标记、`contrast`、`downgrade_reasons[]` | S10、S12 | `visual_dependency`、`audio_dependency` 由归因导出 |
| `split` | `train` / `dev` / `test` / `null` | S12 | 冻结后不能改 |
| `provenance` | 版本、commit、stage_manifest 列表、时间 | 各阶段 | — |

---

## 4. 枚举

### 4.1 通用

| 字段 | 取值 |
|---|---|
| `transcript_tier` | `manual_caption`、`auto_agreed`、`auto_single` |
| `prefilter.relation` | `same`、`different`、`unsure` |
| `text_ambiguous`、`neutral_exists` | `yes`、`no`、`uncertain` |
| `ambiguity_types[]` | `ellipsis`、`reference`、`polysemy`、`speech_act`、`question_intonation`、`emotion_prosody`、`register_politeness`、`speaker_identity`、`scene_event` |
| `labels.bucket` | `core`、`general`、`control`、`rejected` |
| `labels.core_channel` | `model`、`objective`、`both`、`null` |
| `labels.label_tier` | `auto_raw`、`auto_verified`、`human_audited_pass`、`human_audited_fail` |
| `labels.modality_attribution` | `visual`、`audio`、`either`、`both_needed`、`none`、`unclear` |
| `labels.cue_scope` | `momentary`、`scene_static`、`not_tested` |
| `labels.contrast_role` | `statement_counterpart`、`null` |
| `verification.counterfactual.sensitive` | `yes`、`no`、`not_tested` |
| `verification.objective.signal` | `question`、`statement`、`uncertain` |
| `form.final_f0.class` | `rise`、`fall`、`unclear` |

### 4.2 `candidates[].condition`

`TARGET`、`T`、`T_strong`、`T+V`、`T+A`、`T+V+A`、`T_asr`、`T+C_full`、`CF_V_far`、`CF_V_near`、`CF_A_flat`、`CF_A_tts`。反事实条目必须带 `base_condition`（它替换的是哪个条件）和 `cf_source`。

### 4.3 evidence

统一结构：`evidence_id`、`modality`、`cue_type`、`t_start_abs_ms`、`t_end_abs_ms`、`observation`、`interpretation`、`source`、`model`、`prompt`、`confidence`、`verification`。

| 字段 | 取值 |
|---|---|
| `modality` | `visual`、`audio` |
| `cue_type` | `gesture`、`head_motion`、`facial_expression`、`gaze`、`body_posture`、`object_interaction`、`scene_event`、`scene_text`、`onscreen_source_text`、`speaker_identity_visual`、`prosody_final_rise`、`prosody_emphasis`、`pause`、`speech_rate`、`voice_emotion`、`nonspeech_audio_event` |
| `source` | `model`、`rule`、`human` |
| `verification.method` | `describe_entail`、`measurement`、`ocr` |
| `verification.answer` | `yes`、`no`、`unclear` |

---

## 5. 原因代码

### 5.1 拒收（`E_`）：样本进 `rejected`

| 代码 | 含义 | 产生阶段 |
|---|---|---|
| `E_LICENSE` | 来源不在 `approved_sources.yaml` 里 | S1 |
| `E_DECODE` | 媒体无法解码或解码超时 | S1、S5 |
| `E_NO_AUDIO` | 没有音轨 | S1 |
| `E_DUP_SOURCE` | 与已有源文件重复 | S1 |
| `E_ASR_EMPTY` | 转写为空 | S3 |
| `E_ASR_HALLUC` | 命中幻觉过滤 | S3 |
| `E_LANG_SRC` | 源语言不是日语 | S3 |
| `E_ASR_DISAGREE` | 双路读音 CER 超过 `asr.tau_train`（`auto_single`） | S4 |
| `E_LEN` | 时长或字符数超限 | S4 |
| `E_PRIVACY` | 来源或片段被标记为隐私敏感 | S1、S5 |
| `E_TARGET_SUB` | 有目标语烧录字幕（对 test 和 core 是拒收；训练集须打码） | S6 |
| `E_TEACHER_JSON` | 模型输出不合规，重试后仍失败 | S8、S9 |
| `E_TIMEOUT` | 模型调用超时，重试后仍失败 | S8、S9 |
| `E_TGT_LANG` | 训练目标不是简体中文或含假名 | S11 |
| `E_TGT_FORMAT` | 训练目标带解释、引号、多行 | S11 |
| `E_TGT_LEN` | 长度比异常 | S11 |
| `E_QE_LOW` | QE 分数低于阈值 | S11 |
| `E_SCHEMA` | 记录不合 schema | 任意 |

### 5.2 降级（`N_`）：不是 core 的原因，写进 `labels.downgrade_reasons[]`

| 代码 | 含义 | 对应规则 |
|---|---|---|
| `N_NOT_CANDIDATE` | 预筛为 `same`，没有做消融 | SPEC 03 §2 |
| `N_TRANSCRIPT_TIER` | 转写可靠度不够 core 的要求 | C1 |
| `N_OVERLAP` | 多人重叠说话 | C1 |
| `N_TEXT_UNAMBIG` | 文字没有歧义 | C3(a)、O2 |
| `N_TARGET_NEUTRAL` | 训练目标没有表态，或不是有效译文 | C3(b) |
| `N_NEUTRAL_EXISTS` | 存在中性译法 | C3(c) |
| `N_MAP_DISAGREE` | 读法映射评审不一致 | SPEC 03 §1.2 |
| `N_NOT_PERCEIVED` | 中性文本下 teacher 没有得到目标读法 | C4(a) |
| `N_TEACHER_T_SAME` | teacher 只看文字就得到了目标读法 | C4(b) |
| `N_TEXT_PRIOR` | 强文本系统都给出了目标读法 | C4(c)、O5 |
| `N_NO_CUE` | teacher 没有给出线索 | C5 |
| `N_NO_VERIFIED_CUE` | 没有线索通过核验 | C5 |
| `N_OCR_ONLY` | 唯一通过核验的线索是屏幕文字 | C5 |
| `N_CF_INSENSITIVE` | 换掉媒体后读法不变 | C6 |
| `N_CF_UNTESTED` | 没有可用的反事实测试 | C6 |
| `N_JURY_SPLIT` | 评审团不是全票 | C7 |
| `N_JURY_UNAVAILABLE` | 能处理该模态的评审不足 | C7 |
| `N_OBJ_UNVOICED` | 句末有声帧不足，无法测音高 | O1 |
| `N_OBJ_CONFLICT` | ASR 问号和音高结论不一致 | O3 |
| `N_OBJ_TARGET_MISMATCH` | 训练目标与客观信号的结论不一致 | O4 |

---

## 6. 其他文件

| 文件 | 一行是什么 | 关键字段 |
|---|---|---|
| `manifests/source_registry.csv` | 一个来源 | 见 SPEC 06 §1 |
| `manifests/videos.jsonl` | 一个源视频 | `parent_video_id`、`source_registry_id`、`group_id`、`path`、`sha256`、时长、fps、分辨率、音频参数 |
| `manifests/clip_manifest.jsonl` | 一个片段 | `sample_id`、`parent_video_id`、窗口和门控时间、三个媒体路径和 sha256 |
| `manifests/split_registry.jsonl` | 一个 group | `group_id`、`split`、样本数、各 bucket 数、`frozen_at`、数据集版本 |
| `data/interim/<stage>/stage_manifest.json` | 一次阶段运行 | 见 SPEC 02 §0.2 |
| `experiments/<run_id>/manifest.json` | 一次实验 | 见 SPEC 05 §10 |
| `reports/cost_ledger.csv` | 一次阶段运行或实验 | 日期、阶段、模型、调用数、token 数、GPU 小时、API 费用 |
| `reports/audit/answers_<date>.json` | 一次抽检 | `sample_id`、选择、线索判断、耗时 |

---

## 7. validator 必须检查的规则

`cjvmt validate <jsonl>` 至少检查：

1. 必填字段、类型、枚举值。
2. 时间：`t_end > t_start`；台词在片段窗口内；门控区间包含台词、且在窗口内；证据在窗口内；`prev` 各句都在台词之前、`next` 各句都在台词之后。
3. `translation.target_condition == "TARGET"`，且 `translation.target` 等于 `candidates` 里 `TARGET` 那条的 `text`。
4. `candidates` 里每个 `condition` 的 `prompt` 和 `model` 都非空；反事实条目有 `base_condition` 和 `cf_source`。
5. `labels.bucket == "core"` 时：`core_channel` 非空；`downgrade_reasons` 为空；`modality_attribution` 不是 `none` 或 `unclear`。
6. `labels.bucket == "control"` 时：四种消融条件都在 `candidates` 里，或 `contrast_role == "statement_counterpart"`。
7. `is_synthetic == true` 的记录不在 `data/` 目录下。
8. 路径是相对路径；被引用的媒体文件存在（`--check-files` 时）。
9. `evidence[].observation` 和 `interpretation` 都非空，且两者不相同。
10. `sample_id` 在文件内唯一；`split` 非空时，同一 `group_id` 的所有样本 `split` 相同。
