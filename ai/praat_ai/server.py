from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from typing import Callable
from pathlib import Path

from .config import AppConfig
from .vram import RuntimeProfile
from .paths import installation_root


class QwenServerError(RuntimeError):
    pass


def endpoint_available(base_url: str, timeout: float = 2.0, *, opener=None) -> bool:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/models")
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def _port_is_open(host: str, port: int, timeout: float = 0.25) -> bool:
    """端口开没开（快速探测）：模型加载期间它的 HTTP 还没起来，用它把循环跑细一点。"""

    import socket

    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _endpoint_host_port(base_url: str) -> tuple[str, int] | None:
    """从 base_url 里取出 (host, port)；取不出来时返回 None。"""

    from urllib.parse import urlsplit

    try:
        parts = urlsplit(base_url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not host:
        return None
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return host, int(port)


def _endpoint_port_is_open(base_url: str, timeout: float = 0.25) -> bool:
    """base_url 的端口开没开（快速探测）。

    探测的就是下面要做 HTTP 请求的那个地址：取不出地址时返回 True，
    也就是「拿不准就当它开着」，别把原来的 HTTP 探测跳过去。
    """

    address = _endpoint_host_port(base_url)
    if address is None:
        return True
    return _port_is_open(address[0], address[1], timeout=timeout)


def _normalize_model_reference(value: str | Path) -> str:
    """Normalize a model path or name for comparison (Windows is case-insensitive)."""

    text = str(value or "").strip().strip('"')
    if not text:
        return ""
    return Path(text).as_posix().casefold()


def model_name_matches(running: str | Path, configured: str | Path) -> bool:
    """True if `running` refers to the same model file as `configured`."""

    running_norm = _normalize_model_reference(running)
    configured_norm = _normalize_model_reference(configured)
    if not running_norm or not configured_norm:
        return False
    if running_norm == configured_norm:
        return True
    # llama-server 也可能只报告文件名；这种情况按文件名判断。
    return Path(running_norm).name == Path(configured_norm).name


def _model_ids_from_payload(payload: object) -> list[str]:
    ids: list[str] = []

    def add(value: object) -> None:
        text = str(value or "").strip()
        if text and text not in ids:
            ids.append(text)

    if isinstance(payload, dict):
        for entry in payload.get("data") or []:
            if isinstance(entry, dict):
                add(entry.get("id"))
                add(entry.get("model"))
        for entry in payload.get("models") or []:
            if isinstance(entry, dict):
                add(entry.get("name"))
                add(entry.get("model"))
    return ids


def _fetch_models_payload(
    base_url: str,
    timeout: float = 2.0,
    *, opener=None,
) -> object | None:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/models")
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError, UnicodeDecodeError):
        return None


def running_models(base_url: str, timeout: float = 2.0) -> list[str]:
    """Model ids reported by the running OpenAI-compatible server (empty if unreachable)."""

    payload = _fetch_models_payload(base_url, timeout)
    if payload is None:
        return []
    return _model_ids_from_payload(payload)


def running_model_info(base_url: str, timeout: float = 2.0) -> dict[str, object]:
    """What the running server reports about its model; {} when unreadable."""

    payload = _fetch_models_payload(base_url, timeout)
    if payload is None:
        return {}
    info: dict[str, object] = {}
    ids = _model_ids_from_payload(payload)
    if ids:
        info["id"] = ids[0]
    capabilities: list[str] = []
    if isinstance(payload, dict):
        entries = list(payload.get("data") or []) + list(payload.get("models") or [])
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for capability in entry.get("capabilities") or []:
                text = str(capability or "").strip()
                if text and text not in capabilities:
                    capabilities.append(text)
    if capabilities:
        info["capabilities"] = capabilities
    return info


def running_model(base_url: str, timeout: float = 2.0) -> str:
    """The model the running server actually loaded, or "" when unknown."""

    return str(running_model_info(base_url, timeout=timeout).get("id", ""))


def log_dir() -> Path:
    """Folder that holds the llama-server log."""

    path = installation_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def server_model_state(
    base_url: str,
    model_path: str | Path,
    timeout: float = 2.0,
    *, opener=None,
) -> bool | None:
    """True/False when the server reports its models; None when that cannot be read."""

    ids = (_model_ids_from_payload(_fetch_models_payload(base_url, timeout, opener=opener))
           if opener is not None else running_models(base_url, timeout=timeout))
    if not ids:
        return None
    return any(model_name_matches(item, model_path) for item in ids)


class QwenServerManager:
    def __init__(
        self,
        config: AppConfig,
        profile: RuntimeProfile,
        *,
        progress: Callable[[float, str], None] | None = None,
    ):
        self.config = config
        self.profile = profile
        #: 进度回调：(0–1, 中文说明)。加载模型要几十秒，界面靠它画进度条
        #: （Praat 菜单那一路由控制脚本打到标准输出，对话窗口那一路直接回调）。
        self.progress = progress
        self.process: subprocess.Popen[bytes] | None = None
        self.log_handle = None
        self.vision_active = False
        self.last_warning = ""
        self._probe_options = {}
        if config.qwen.token_mode is not None:
            from .qwen import _open_no_redirect
            self._probe_options['opener'] = _open_no_redirect

    def ensure_started(self) -> bool:
        # 先花 0.25 秒看一眼 base_url 的端口：端口没开就不可能在跑「同一个模型」，
        # 可以直接跳过下面两次 HTTP 探测——本机的端口是**静默丢包**（不是立刻拒绝），
        # 一次探测要等满 2 秒超时，两次就是 4 秒，用户看到的正是「进度条不动」。
        self._report(0.10, "检查本机是否已在运行模型服务…")
        if _endpoint_port_is_open(self.config.qwen.base_url):
            state = server_model_state(
                self.config.qwen.base_url,
                self.config.server.model_path,
                **self._probe_options,
            )
            if state is True:
                self._report(0.95, "模型服务已经在跑，直接用。")
                return False   # 已经在跑同一个模型
            if state is None and endpoint_available(self.config.qwen.base_url, **self._probe_options):
                return False   # 服务在跑但读不到模型列表：保持原行为，不擅自重启
            if state is False:
                raise QwenServerError(
                    "The running Qwen server already serves a different model. "
                    "Stop it first, or use the frontend menu so it can be restarted: "
                    f"{self.config.server.model_path}"
                )
        if not self.config.server.auto_start:
            return False

        if not self.config.server.llama_server.strip():
            raise QwenServerError(
                "未配置 llama-server 路径。请检查 ai_config.json 的 server.llama_server，"
                "或设置 PRAAT_AI_LLAMA_SERVER 环境变量。"
            )
        if not self.config.server.model_path.strip():
            raise QwenServerError(
                "未配置本地模型路径。请检查 ai_config.json 的 server.model_path，"
                "或设置 PRAAT_AI_QWEN_MODEL_PATH 环境变量。"
            )
        server = Path(self.config.server.llama_server)
        model = Path(self.config.server.model_path)
        if not server.is_file():
            raise QwenServerError(f"llama-server not found: {server}")
        if not model.is_file():
            raise QwenServerError(f"Qwen model not found: {model}")

        mmproj = self._vision_mmproj()

        attempts: list[tuple[bool, list[str]]] = []
        if mmproj is not None:
            attempts.append((True, self._build_command(server, model, mmproj)))
        attempts.append((False, self._build_command(server, model, None)))

        last_error = ""
        for use_vision, command in attempts:
            try:
                self._report(
                    0.15,
                    "正在启动 llama-server（读取模型文件）…"
                    if use_vision
                    else "正在启动 llama-server（纯文本模式）…",
                )
                self._spawn(command)
                self._wait_for_endpoint()
            except QwenServerError as error:
                last_error = str(error)
                self.stop()
                continue
            self.vision_active = use_vision
            if mmproj is not None and not use_vision:
                # 视觉投影文件与所选模型不匹配时，纯文本启动仍然可用。
                self.last_warning = (
                    "视觉投影文件（mmproj）与所选模型不匹配，已按纯文本模式启动："
                    f"{self.config.server.mmproj_path}"
                )
                self._log_warning()
            return True
        raise QwenServerError(last_error or "Qwen server exited while starting.")

    def _vision_mmproj(self) -> Path | None:
        """Pick the projector that belongs to the configured model.

        `server.mmproj_by_model` 按模型路径（或文件名）给出各自的投影文件；
        没有条目时退回 `server.mmproj_path`。
        """

        if not self.profile.vision_enabled:
            return None
        configured = self.config.server.mmproj_path
        for key, value in (self.config.server.mmproj_by_model or {}).items():
            if value and model_name_matches(key, self.config.server.model_path):
                configured = str(value)
                break
        if not configured:
            raise QwenServerError(
                "Vision was requested, but the mmproj model file is missing."
            )
        mmproj = Path(configured)
        if not mmproj.is_file():
            raise QwenServerError(
                "Vision was requested, but the mmproj model file is missing: "
                f"{mmproj}"
            )
        return mmproj

    def _build_command(
        self,
        server: Path,
        model: Path,
        mmproj: Path | None,
    ) -> list[str]:
        command = [
            str(server),
            "-m",
            str(model),
            "--host",
            self.config.server.host,
            "--port",
            str(self.config.server.port),
            "--ctx-size",
            str(self.profile.context_tokens),
            "--n-gpu-layers",
            str(self.profile.n_gpu_layers),
            "--threads",
            str(self.config.server.threads),
            "--parallel",
            str(self.config.server.parallel),
            "--no-webui",
            # 使用模型 chat 模板序列化多轮消息和 role=tool 的工具返回。
            "--jinja",
        ]
        if mmproj is not None:
            command.extend(["--mmproj", str(mmproj)])
        return command

    def _spawn(self, command: list[str]) -> None:
        self.log_handle = (log_dir() / "qwen-server.log").open("ab")
        try:
            self.process = subprocess.Popen(
                command,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
            )
        except OSError as error:
            # 启动失败（llama-server 路径不对、那个文件不是可执行文件……）时别把
            # 日志句柄留着：Windows 上它会一直占着 qwen-server.log，连删都删不掉，
            # 而且异常类型换成 QwenServerError，用户看到的中文提示才对得上。
            self.log_handle.close()
            self.log_handle = None
            raise QwenServerError(f"无法启动 llama-server：{error}") from error

    def _wait_for_endpoint(self) -> None:
        started = time.time()
        deadline = started + 60
        #: 以 15 秒估算加载进度，未就绪时上限为 0.95。
        expected = 15.0
        while time.time() < deadline:
            if self.process is None:
                raise QwenServerError("Qwen server is not running.")
            if self.process.poll() is not None:
                raise QwenServerError("Qwen server exited while starting.")
            # 先报进度、再探测：探测有可能卡满超时（端口是静默丢包），
            # 先报一下用户才能看到进度条在动。
            elapsed = time.time() - started
            self._report(
                0.2 + 0.7 * min(1.0, elapsed / expected),
                f"正在加载模型…（已等待 {elapsed:.0f} 秒）",
            )
            # 先看 base_url 的端口开没开（0.25 秒的快速探测），开上了再做一次
            # 完整的 HTTP 检查。直接做 HTTP 检查时，模型还没监听，每次连接都要
            # 等满 2 秒超时，循环就变成 2.5 秒才报一次进度（进度条一顿一顿的）。
            if _endpoint_port_is_open(self.config.qwen.base_url):
                # 端口开了：用短超时问一句，能答上就算就绪（模型已经在监听了）。
                if endpoint_available(self.config.qwen.base_url, timeout=0.8, **self._probe_options):
                    self._report(0.95, "模型服务已就绪。")
                    return
            time.sleep(0.25)
        self.stop()
        raise QwenServerError("Timed out waiting for the Qwen server.")

    def _report(self, fraction: float, message: str) -> None:
        if self.progress is not None:
            try:
                self.progress(float(fraction), message)
            except Exception:   # noqa: BLE001 - 进度只影响显示，不能打断加载
                pass

    def _log_warning(self) -> None:
        try:
            with (log_dir() / "qwen-server.log").open(
                "a",
                encoding="utf-8",
                errors="replace",
            ) as handle:
                handle.write(f"\n=== PRAAT-AI: {self.last_warning} ===\n")
        except OSError:
            pass

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
