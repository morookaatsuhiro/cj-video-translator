# 数据集试点规格（结课版写好、这次不做）

> 从结课版 AGENTS.md §7.11 移出（2026-11-24）。结课后做数据集时作为最早的一步试点使用。

## 规则
输入：WMT26 的逐行结果（加上 WMT25 和自录视频，如果有）。只对能用规则判对的现象（问句；日语目标的文体）打标签。“对”和“错”都以人工参考为准，用 §7.7 的同一套规则判断。

| 标签 | 条件 |
|---|---|
| `helped_by_audio` | A 错，C 对，C 错配后又错 |
| `helped_by_video` | A 错，D 对，D 错配后又错 |
| `helped_by_both` | A 错，E 对，C 和 D 单独都错 |
| `misled` | A 对，C、D、E 中有一个错 |
| `unchanged` | 各条件结果一样 |
| `cf_untested` | 需要的错配条件没有跑 |

- 导出 `data/pilot/candidates.jsonl`（§8 格式），只导出 `helped_*` 和 `misled`。
- `reports/pilot.md`：每个标签的数量和比例（附 Wilson 置信区间），以及几个文字例子。
- `reports/html/pilot.html`：可以筛选，能播放本地片段。
- 每条记录标明：`label_tier: auto_raw`、`source_use: eval_only`，备注“来自公开测试集，只用于演示挖掘方法，不能用于训练”。

