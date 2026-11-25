# WMT26 General MT 中→日 speech

来源为 `wmt-conference/wmt26-general-mt` 发布的 `data/wmt26-genmt.jsonl`。保留中→日语向 `zho_Hans` → `jpn_Jpan`，以 `speech` 为评测域。发布媒体单独提供于 `humeval` 版本。

- 文档 ID、语言、域和提示字段为 `doc_id`、`src_lang`、`tgt_lang`、`domain`、`prompt_instruction`、`evaluation_instruction` 和 `multimodal_instruction`。
- ASR 转写在 `src_text`。人工转写在 `gold_transcript`。人工后编辑日语参考在 `refs.refPE`。
- 视频用 `video.id` 和 `video.path` 描述，另有 `screenshot`。本项目的页面和离线包不复制 WMT 媒体。
- 核对运行 `20261125-0214_wmt26-prepare_800787aa` 的 `manifest.json` 包含 SHA-256、完整字段、筛选数量和分层计数。人工转写与 ASR 去除空白与 Unicode 标点后做字符对齐，再检查对应末字后紧邻的问号。
- `humeval` 的 `multimodal_inputs.zip` 已下载。压缩包大小为 1,137,545,542 字节，SHA-256 与官方发布一致，为 `e509ad71ee818e70a513c28caaf87c013100d950f879fa3aef4b08b00c9d6982`。
- 媒体准备运行 `20261125-0214_wmt26-prepare_800787aa` 匹配了全部中→日 speech 文档。媒体保存在 `data/external/wmt26/`，用于站内评测。
- 正式评测运行 `20261125-0247_wmt26-eval_bb2b92a1` 覆盖 114 段、7 个条件和 800 条文档条件结果。OCR 审计保存于准备运行目录的 `ocr.json`，包含 114 段、342 个抽帧。译文、指标、COMET 和图表保存在 `results/20261125-0247_wmt26-eval_bb2b92a1/` 与 `reports/eval_wmt26.md`。
