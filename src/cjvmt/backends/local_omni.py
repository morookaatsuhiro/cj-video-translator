from __future__ import annotations

import atexit
import json
import os
import selectors
import signal
import subprocess
from dataclasses import asdict
from pathlib import Path

from cjvmt.backends import Request
from cjvmt.config import Settings, file_hash


class LocalOmni:
    def __init__(self, settings: Settings):
        helper = os.getenv("CJVMT_LOCAL_OMNI_PYTHON")
        model = os.getenv("CJVMT_LOCAL_OMNI_PATH")
        if not helper or not model:
            raise ValueError("请设置 CJVMT_LOCAL_OMNI_PYTHON 和 CJVMT_LOCAL_OMNI_PATH。")
        self.helper = Path(helper).expanduser().absolute()
        self.model = Path(model).expanduser().resolve()
        if not self.helper.is_file() or not os.access(self.helper, os.X_OK):
            raise ValueError(f"找不到可执行的 vLLM-Omni Python：{self.helper}")
        try:
            receipt = json.loads((self.model / "receipt.json").read_text())
        except (OSError, ValueError) as error:
            raise ValueError(f"无法读取本地 Qwen3-Omni 下载记录：{self.model / 'receipt.json'}") from error
        if (
            not isinstance(receipt, dict)
            or not receipt.get("model")
            or receipt.get("revision") != self.model.name
            or not (self.model / "config.json").is_file()
        ):
            raise ValueError(f"本地 Qwen3-Omni 权重和下载记录不匹配：{self.model}")
        self.deploy = settings.root / "configs/local_omni_deploy.yaml"
        self.cache = settings.path("cache") / "local_omni"
        self.seed = settings.values["seed"]
        self.identity = {
            "backend": "local_omni",
            "model": receipt["model"],
            "revision": receipt["revision"],
            "deploy_sha256": file_hash(self.deploy),
            "adapter_sha256": file_hash(Path(__file__).with_name("local_omni_worker.py")),
        }
        self.process: subprocess.Popen | None = None
        atexit.register(self.close)

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if process.stdout:
            process.stdout.close()

    def _read(self, timeout: int) -> dict:
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout):
                self.close()
                raise RuntimeError("本地 Qwen3-Omni 响应超时。")
            line = self.process.stdout.readline()
        if not line:
            self.close()
            raise RuntimeError(f"本地 Qwen3-Omni 进程提前退出。日志：{self.cache / 'worker.log'}")
        return json.loads(line)

    def warm(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        self.cache.mkdir(parents=True, exist_ok=True)
        worker = Path(__file__).with_name("local_omni_worker.py")
        environment = {**os.environ, "PATH": f"{self.helper.parent}:{os.environ['PATH']}"}
        if "CJVMT_LOCAL_OMNI_LD_LIBRARY_PATH" in environment:
            environment["LD_LIBRARY_PATH"] = environment["CJVMT_LOCAL_OMNI_LD_LIBRARY_PATH"]
        with (self.cache / "worker.log").open("a") as log:
            self.process = subprocess.Popen(
                [str(self.helper), str(worker), str(self.cache), str(self.model), str(self.deploy), str(self.seed)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                encoding="utf-8",
                env=environment,
                start_new_session=True,
            )
        if not self._read(650).get("ready"):
            raise RuntimeError("本地 Qwen3-Omni 初始化失败。")

    def generate(self, request: Request) -> dict:
        self.warm()
        self.process.stdin.write(json.dumps(asdict(request), ensure_ascii=False, default=str) + "\n")
        self.process.stdin.flush()
        result = self._read(180)
        if "error" in result:
            if "EngineDeadError" in result["error"]:
                self.close()
            raise RuntimeError(result["error"])
        return result

    def generate_batch(self, requests: list[Request], batch_size: int = 4) -> list[dict]:
        if not requests:
            return []
        if len(requests) == 1:
            return [self.generate(requests[0])]
        self.warm()
        outputs: list[dict] = []
        for offset in range(0, len(requests), max(1, batch_size)):
            chunk = requests[offset : offset + max(1, batch_size)]
            if len(chunk) == 1:
                outputs.append(self.generate(chunk[0]))
                continue
            payload = {"batch": [asdict(req) for req in chunk]}
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            self.process.stdin.flush()
            result = self._read(max(180, 90 * len(chunk)))
            if "error" in result:
                if "EngineDeadError" in result["error"]:
                    self.close()
                raise RuntimeError(result["error"])
            outputs.extend(result["batch"])
        return outputs
