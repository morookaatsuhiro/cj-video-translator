# WMT25 General MT 日→中 speech

来源为 `wmt-conference/wmt25-general-mt` 发布的 `wmt25-genmt.jsonl` 以及本机已有的媒体包。筛选 `src_lang=ja`、`tgt_lang=zh_CN`、`domain=speech`。

- 文档与集合通过 `doc_id`、`dataset_id` 和 `collection_id` 标识，另有 `domain`、`prompt_instruction`、`src_lang`、`tgt_lang` 和 `screenshot`。
- `src_text` 为发布的日语转写，`refs.refA.ref` 为中文参考，`video` 为媒体相对路径。
- 准备运行 `20261124-1730_wmt25-prepare_c9bb479c` 的 `manifest.json` 记录转写与媒体压缩包的 SHA-256，以及文档数量和媒体匹配情况。发布数据按整段提供转写和参考，缺少逐句对应标注。
- 视频、截图和逐文档结果保留在本机，课堂分发包只有公开演示素材和汇总报告。
