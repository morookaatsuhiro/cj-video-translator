# WMT25 日语 → 简体中文评测

运行 `20261124-1752_wmt25-eval_ae915d16`，准备记录 `20261124-1730_wmt25-prepare_c9bb479c`。
本次评测 59 段文档。<!-- run=20261124-1752_wmt25-eval_ae915d16 file=manifest.json key=document_count value=59 -->

模型 `cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit`，revision `b8f7bfd32ec3e7a59fa78cad0be54463eb89e9a9`。
同模型、同解码，依次比较文本、加声音、加静音视频与音画交错。源转写与参考按整段提供，逐句问句命中率未测。
COMET 使用 `Unbabel/wmt22-comet-da`，版本 `2760a223ac957f30acfb18c8aa649b01cf1d75f2`。

| 条件 | 文档级 chrF | 回退行 | 空输出 | COMET |
|---|---:|---:|---:|---:|
| `T_neutral` | 24.286 | 0 | 0 | 0.78729 | <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T_neutral:chrf value=24.286 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T_neutral:fallback_lines value=0 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T_neutral:blank_outputs value=0 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T_neutral:comet value=0.78729 -->
| `T+A` | 25.926 | 0 | 0 | 0.79909 | <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+A:chrf value=25.926 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+A:fallback_lines value=0 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+A:blank_outputs value=0 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+A:comet value=0.79909 -->
| `T+V` | 22.185 | 0 | 0 | 0.75798 | <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+V:chrf value=22.185 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+V:fallback_lines value=0 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+V:blank_outputs value=0 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+V:comet value=0.75798 -->
| `T+V+A` | 24.135 | 0 | 0 | 0.78225 | <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+V+A:chrf value=24.135 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+V+A:fallback_lines value=0 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+V+A:blank_outputs value=0 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=summary.csv key=condition:T+V+A:comet value=0.78225 -->

COMET 差值以同一文档配对重采样，基线为仅文字。

| 条件 | COMET 差值 | 95% CI 下界 | 95% CI 上界 |
|---|---:|---:|---:|
| `T+A` | 0.0118 | -0.00109 | 0.02822 | <!-- run=20261124-1752_wmt25-eval_ae915d16 file=comparisons.csv key=condition:T+A:comet_delta value=0.0118 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=comparisons.csv key=condition:T+A:ci_low value=-0.00109 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=comparisons.csv key=condition:T+A:ci_high value=0.02822 -->
| `T+V` | -0.02931 | -0.04808 | -0.01292 | <!-- run=20261124-1752_wmt25-eval_ae915d16 file=comparisons.csv key=condition:T+V:comet_delta value=-0.02931 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=comparisons.csv key=condition:T+V:ci_low value=-0.04808 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=comparisons.csv key=condition:T+V:ci_high value=-0.01292 -->
| `T+V+A` | -0.00504 | -0.02528 | 0.01427 | <!-- run=20261124-1752_wmt25-eval_ae915d16 file=comparisons.csv key=condition:T+V+A:comet_delta value=-0.00504 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=comparisons.csv key=condition:T+V+A:ci_low value=-0.02528 --> <!-- run=20261124-1752_wmt25-eval_ae915d16 file=comparisons.csv key=condition:T+V+A:ci_high value=0.01427 -->

该结果用于课程研究。评测媒体保留在本机。
