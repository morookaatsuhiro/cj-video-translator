# GitHub 上传清单

## 上传内容

- 根目录配置和入口文件包括 `README.md`、`AGENTS.md`、`GOALS.md`、`CLAUDE.md`、`Makefile`、`pyproject.toml`、`.env.example` 和 `.gitignore`。
- `src/` 是可安装的 `cjvmt` Python 包，包含媒体处理、Whisper 转写、分句、片段生成、翻译后端、CosyVoice 配音、评测、报告和网页服务。
- `configs/` 保存结课版参数和提示词模板。
- `scripts/` 保存启动 AutoDL、报告数字检查、COMET 补算和环境辅助脚本。
- `tests/` 保存 CPU 可运行的单元测试、实时队列测试、翻译条件隔离测试和 WMT 合成夹具。
- `docs/` 保存方法说明、环境说明、数据卡、决策记录、演示脚本、验收清单和后续路线。`docs/github_upload.md` 是本清单。
- `reports/` 上传 Markdown 报告、进度记录、环境记录和小型图表。正式 WMT26 报告在 `reports/eval_wmt26.md`，课程总报告在 `reports/final_report.md`。

## 不上传内容

- `data/` 不上传，里面有 WMT26 媒体、自录素材、处理后音视频和本地数据缓存。
- `results/` 不上传，里面有模型输出、运行日志、WMT26 逐文档结果和可能较大的媒体文件。需要留证时只在报告中保留运行编号和指标来源。
- `reports/html/` 不上传，里面有离线演示包、演示视频、浏览器录像和复制的运行媒体。
- `.env`、`.venv/`、`.cache/`、Python 缓存、模型权重和下载缓存不上传。
- `*.mp4`、`*.wav`、`*.safetensors`、`*.gguf`、`*.onnx`、`*.pt` 和 `*.bin` 已由 `.gitignore` 排除。

## 规模

- 2026 年 11 月 25 日实测，AutoDL 项目目录占用约 27 GiB，其中 `.cache/` 约 24 GiB（含 CosyVoice 权重、独立环境和依赖缓存），`.venv/` 约 1.2 GiB，`data/` 约 1.1 GiB，`results/` 约 191 MiB，`reports/` 约 64 MiB。
- 按当前忽略规则，可上传的源码、配置、测试、文档和报告共约 105 个文件，逻辑大小约 1.5 MB，包含小型报告图表。

## 上传前检查

```bash
git status --short --ignored
git check-ignore data/x results/x .env reports/html/x
make check
```

推送前确认 `.env`、自录视频、WMT26 视频和 `results/` 没有出现在暂存区。需要复现实验时，在目标机器重新下载 WMT26 媒体，并按 `docs/data_cards/wmt26.md` 校验 SHA-256。
