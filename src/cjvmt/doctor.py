from concurrent.futures import ThreadPoolExecutor
from os import getenv
from pathlib import Path
from shutil import disk_usage
from sys import version_info
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cjvmt.backends.local_omni import LocalOmni
from cjvmt.config import Settings
from cjvmt.media import executable
from cjvmt.store import inspect_command, versions, write_text


def access(host: str) -> str:
    try:
        with urlopen(Request(f"https://{host}", method="HEAD"), timeout=6) as response:
            return f"可达（HTTP {response.status}）"
    except HTTPError as error:
        return f"有响应（HTTP {error.code}）"
    except (OSError, URLError) as error:
        return f"不可达（{error}）"


def deployment(settings: Settings) -> list[str]:
    asr = settings.values["asr"]
    device = getenv("CJVMT_ASR_DEVICE", asr["device"])
    local_asr = getenv("CJVMT_ASR_PATH")
    whisper = f"本地权重 `{Path(local_asr).expanduser()}`" if local_asr else "从本地 Hugging Face 缓存解析"
    if local_asr and not (Path(local_asr).expanduser() / "model.bin").is_file():
        whisper += "（缺少 model.bin）"
    revision = getenv("CJVMT_ASR_REVISION") or asr.get("revision") or "由本地缓存解析或未记录"
    lines = [f"- faster-whisper `{asr['model']}`，设备配置 `{device}`，revision `{revision}`，{whisper}"]
    try:
        backend = LocalOmni(settings)
    except (OSError, ValueError, KeyError, TypeError) as error:
        lines.append(f"- `local_omni` 静态检查未通过（{error}）")
    else:
        lines.extend(
            (
                f"- `local_omni` 模型 `{backend.identity['model']}`，revision `{backend.identity['revision']}`",
                f"- 本地权重 `{backend.model}`",
                f"- vLLM-Omni Python `{backend.helper}`，部署配置 `{backend.deploy.relative_to(settings.root)}`",
            )
        )
    return lines


def doctor(settings: Settings) -> Path:
    gpu = inspect_command(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader",
        ]
    )
    version = ".".join(map(str, version_info[:3]))
    ffmpeg = inspect_command([executable("ffmpeg"), "-version"]) or "不可用"
    hosts = ("github.com", "huggingface.co", "modelscope.cn")
    with ThreadPoolExecutor() as pool:
        network = dict(zip(hosts, pool.map(access, hosts), strict=True))
    capability = [line.split(",")[-1].strip() for line in (gpu or "").splitlines()]
    bf16 = [
        f"{item} {'支持' if float(item) >= 8 else '不支持'}" for item in capability if item.replace(".", "").isdigit()
    ]
    omni = settings.values["models"]["omni"]
    cpu = inspect_command(["nproc"]) or "未知"
    memory_file = Path("/sys/fs/cgroup/memory.max")
    memory_limit = memory_file.read_text().strip() if memory_file.is_file() else ""
    path = settings.path("reports") / "env.md"
    existing = path.read_text().partition("\n## 四条件实测\n")[2] if path.exists() else ""
    fields = [
        "# 运行环境",
        "",
        f"- Python {version}",
        f"- {ffmpeg.splitlines()[0]}",
        f"- GPU（型号、显存、驱动、架构）{gpu or '不可用'}",
        f"- 硬件 BF16 {', '.join(bf16) or '未知'}",
        f"- 当前可用的逻辑 CPU（nproc）{cpu}",
        f"- cgroup 内存上限（GiB）{int(memory_limit) / 2**30:g}"
        if memory_limit.isdigit()
        else f"- cgroup 内存上限{'无限制' if memory_limit == 'max' else '未知'}",
        f"- 项目盘剩余 {disk_usage(settings.root).free / 2**30:.1f} GiB",
        *[f"- {key} {value}" for key, value in network.items()],
        "",
        "## 本地模型配置",
        "",
        *deployment(settings),
        f"- 可选 `qwen_omni` 配置 `{omni['id']}`，revision `{getenv('CJVMT_OMNI_REVISION') or omni['revision'] or '尚未记录'}`",
        "",
        "## 已安装版本",
        "",
        *[f"- {k} {v}" for k, v in versions().items()],
        "",
        "## 四条件实测",
        "",
        existing.strip() or "未运行。",
        "",
    ]
    write_text(path, "\n".join(fields))
    return path
