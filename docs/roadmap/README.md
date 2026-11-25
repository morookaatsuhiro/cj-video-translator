# docs/roadmap：数据集完整建设计划（v0.3，结课后继续用）

这里存的是 2026-11-16 写好的完整计划：怎样在尽量少人工的前提下，自动构建一个能证明“视频或音频确实改变了译法”的日→中多模态翻译数据集，并用它微调模型。

为了赶结课，根目录现在用的是结课冲刺版（`AGENTS.md`、`GOALS.md`）。完整计划暂时停在这里。

| 文件 | 内容 |
|---|---|
| `AGENTS_full_v0.3.md` | 完整版的 Agent 规则（小于 32 KiB） |
| `PHASED_GOALS_full_v0.3.md` | P0–P7，共 47 个里程碑 |
| `HUMAN_TASKS_full_v0.3.md` | 完整版的人工事项 H1–H6 |
| `../spec/01–06_*.md` | 完整版的六份详细规范（仍放在 `docs/spec/`） |
| `../../configs/pipeline.yaml` 等 | 完整版的阈值、模型角色、提示词、挖掘词表 |

## 结课后怎么恢复

1. 把根目录的 `AGENTS.md`、`GOALS.md` 移到 `docs/archive/`，改名为 `AGENTS_mvp.md`、`GOALS_mvp.md`。
2. 把本目录的 `AGENTS_full_v0.3.md` 复制到根目录，改名为 `AGENTS.md`；`PHASED_GOALS_full_v0.3.md` 复制为根目录的 `PHASED_GOALS.md`；`HUMAN_TASKS_full_v0.3.md` 复制为 `docs/HUMAN_TASKS.md`。
3. 删掉这三个文件开头的“存档说明”。
4. 在 `docs/decision_log.md` 记一条：恢复完整计划。并写明结课版已经完成了哪些部分。
5. 从完整计划的 P1（来源与授权）继续。结课版已经覆盖了：
   - P0 的骨架；
   - P2 的媒体处理、语音识别、切句、片段（不含双路 ASR）；
   - P3 的多条件翻译（不含资格探针）；
   - P6 的统计工具。

   这些代码直接复用，按完整规范补齐缺的部分。
