"""模型切换必须真正重启服务，状态必须报告服务实际加载的模型。"""

import json
import socketserver
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from praat_ai import control
from praat_ai import server as server_module
from praat_ai.server import (
    QwenServerError,
    QwenServerManager,
    model_name_matches,
    running_model,
)
from praat_ai.vram import select_runtime_profile


SMALL_MODEL = r"D:\models\Qwen3.5-0.8B-Q4_K_M.gguf"
BIG_MODEL = r"D:\llama.cpp\Qwen3.5-2B-UD-Q5_K_XL.gguf"


class _ModelsHandler(BaseHTTPRequestHandler):
    model_id = SMALL_MODEL
    capabilities: list[str] = []

    def do_GET(self) -> None:   # noqa: N802 - http.server API
        if not self.path.startswith("/v1/models"):
            self.send_error(404)
            return
        payload = json.dumps(
            {
                "object": "list",
                "models": [
                    {
                        "name": self.model_id,
                        "model": self.model_id,
                        "type": "model",
                        "capabilities": list(self.capabilities),
                    }
                ],
                "data": [
                    {
                        "id": self.model_id,
                        "object": "model",
                        "aliases": [self.model_id],
                        "meta": {"n_params": 752393024},
                    }
                ],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        return


class _FastHTTPServer(HTTPServer):
    """HTTPServer without the slow reverse-DNS lookup in server_bind()."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class FakeService:
    """A real HTTP endpoint that reports the model a server would load."""

    def __init__(self, model_id: str, capabilities: list[str] | None = None) -> None:
        self.model_id = model_id
        self.capabilities = list(capabilities or [])
        self.server = _FastHTTPServer(("127.0.0.1", 0), _ModelsHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "FakeService":
        _ModelsHandler.model_id = self.model_id
        _ModelsHandler.capabilities = self.capabilities
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def write_config(path: Path, *, base_url: str, model_path: str) -> None:
    path.write_text(
        json.dumps(
            {
                "qwen": {"base_url": base_url, "model": Path(model_path).name},
                "server": {
                    "llama_server": str(path.parent / "llama-server.exe"),
                    "model_path": model_path,
                    "mmproj_path": str(path.parent / "mmproj.gguf"),
                    "auto_start": False,
                },
            }
        ),
        encoding="utf-8",
    )


class RunningModelTests(unittest.TestCase):
    def test_reports_model_from_endpoint(self) -> None:
        with FakeService(SMALL_MODEL) as service:
            self.assertEqual(running_model(service.base_url), SMALL_MODEL)

    def test_returns_empty_when_service_is_down(self) -> None:
        self.assertEqual(running_model("http://127.0.0.1:9/v1"), "")

    def test_matching_accepts_path_and_bare_file_name(self) -> None:
        self.assertTrue(model_name_matches(SMALL_MODEL, SMALL_MODEL))
        self.assertTrue(model_name_matches("Qwen3.5-0.8B-Q4_K_M.gguf", SMALL_MODEL))
        self.assertFalse(model_name_matches(SMALL_MODEL, BIG_MODEL))
        self.assertFalse(model_name_matches("", BIG_MODEL))


class CollectStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        self.runtime = directory / "runtime"
        self.runtime.mkdir()
        self.config_path = directory / "ai_config.json"
        self.pid_file = self.runtime / "qwen.pid"
        self.patches = [
            patch.object(control, "runtime_dir", return_value=self.runtime),
            patch.object(control, "status_path", return_value=self.runtime / "status.json"),
            patch.object(control, "pid_path", return_value=self.runtime / "qwen.pid"),
            patch.object(control, "detect_gpu", return_value=None),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self._temp.cleanup()

    def test_status_reports_model_loaded_by_service(self) -> None:
        with FakeService(SMALL_MODEL) as service:
            write_config(self.config_path, base_url=service.base_url, model_path=BIG_MODEL)
            status = control.collect_status(self.config_path)
        self.assertEqual(status["frontend_model"], "Qwen3.5-0.8B-Q4_K_M.gguf")
        self.assertEqual(
            status["frontend_model_configured"], "Qwen3.5-2B-UD-Q5_K_XL.gguf"
        )
        self.assertTrue(status["frontend_model_mismatch"])
        self.assertEqual(status["frontend_model_source"], "server")
        self.assertTrue(status["frontend_running"])

    def test_status_reports_vision_from_service_capabilities(self) -> None:
        with FakeService(SMALL_MODEL, ["completion", "multimodal"]) as service:
            write_config(self.config_path, base_url=service.base_url, model_path=SMALL_MODEL)
            vision_status = control.collect_status(self.config_path)
        with FakeService(SMALL_MODEL, ["completion"]) as service:
            write_config(self.config_path, base_url=service.base_url, model_path=SMALL_MODEL)
            text_status = control.collect_status(self.config_path)
        self.assertTrue(vision_status["frontend_vision"])
        self.assertFalse(text_status["frontend_vision"])

    def test_status_falls_back_to_config_when_stopped(self) -> None:
        write_config(self.config_path, base_url="http://127.0.0.1:9/v1", model_path=BIG_MODEL)
        status = control.collect_status(self.config_path)
        self.assertEqual(status["frontend_model"], "Qwen3.5-2B-UD-Q5_K_XL.gguf")
        self.assertFalse(status["frontend_model_mismatch"])
        self.assertEqual(status["frontend_model_source"], "config")
        self.assertFalse(status["frontend_running"])
        self.assertFalse(status["frontend_vision"])

    def test_stale_pid_record_does_not_mark_an_unrelated_process_running(self) -> None:
        write_config(self.config_path, base_url="http://127.0.0.1:9/v1", model_path=BIG_MODEL)
        self.pid_file.write_text(
            json.dumps({
                "pid": 4242,
                "executable": str(self.config_path.parent / "llama-server.exe"),
                "started": "win:old-process",
            }),
            encoding="utf-8",
        )
        with patch.object(control, "process_identity", return_value={
            "executable": str(self.config_path.parent / "other.exe"),
            "started": "win:other-process",
        }):
            status = control.collect_status(self.config_path)
        self.assertFalse(status["frontend_running"])


class FakeManager:
    instances: list["FakeManager"] = []

    def __init__(
        self, config: object, profile: object, *, progress: object = None
    ) -> None:
        self.config = config
        self.profile = profile
        #: 进度回调（加载/停止模型时界面画进度条用）；这里只记下来。
        self.progress = progress
        self.ensure_called = False
        self.process = None
        FakeManager.instances.append(self)

    def ensure_started(self) -> bool:
        self.ensure_called = True
        return True


class StartFrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeManager.instances = []
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        self.runtime = directory / "runtime"
        self.runtime.mkdir()
        self.config_path = directory / "ai_config.json"
        self.server_exe = directory / "llama-server.exe"
        self.server_exe.write_bytes(b"stub")
        self.small = directory / "Qwen3.5-0.8B-Q4_K_M.gguf"
        self.big = directory / "Qwen3.5-2B-UD-Q5_K_XL.gguf"
        self.small.write_bytes(b"stub")
        self.big.write_bytes(b"stub")
        self.patches = [
            patch.object(control, "runtime_dir", return_value=self.runtime),
            patch.object(control, "status_path", return_value=self.runtime / "status.json"),
            patch.object(control, "pid_path", return_value=self.runtime / "qwen.pid"),
            patch.object(control, "detect_gpu", return_value=None),
            patch.object(control, "QwenServerManager", FakeManager),
            patch.object(control, "select_runtime_profile", select_runtime_profile),
            patch.object(control, "_wait_for_endpoint_gone", return_value=None),
            patch.object(control, "_stop_running_service", return_value=True),
        ]
        for item in self.patches:
            item.start()
        self.stop_mock = control._stop_running_service

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self._temp.cleanup()

    def test_start_restarts_service_when_loaded_model_differs(self) -> None:
        with FakeService(SMALL_MODEL) as service:
            write_config(
                self.config_path,
                base_url=service.base_url,
                model_path=str(self.big),
            )
            control.start_frontend(self.config_path)
        self.stop_mock.assert_called_once()
        self.assertTrue(FakeManager.instances)
        self.assertTrue(FakeManager.instances[-1].ensure_called)

    def test_start_keeps_service_when_loaded_model_matches(self) -> None:
        with FakeService(SMALL_MODEL) as service:
            write_config(
                self.config_path,
                base_url=service.base_url,
                model_path=str(self.small),
            )
            control.start_frontend(self.config_path)
        self.stop_mock.assert_not_called()
        self.assertFalse(FakeManager.instances)

    def test_set_model_restarts_service_when_loaded_model_differs(self) -> None:
        with FakeService(SMALL_MODEL) as service:
            write_config(
                self.config_path,
                base_url=service.base_url,
                model_path=str(self.small),
            )
            status = control.set_frontend_model(str(self.big), config_path=self.config_path)
        self.stop_mock.assert_called_once()
        self.assertTrue(FakeManager.instances)
        self.assertEqual(
            json.loads(self.config_path.read_text(encoding="utf-8"))["server"]["model_path"],
            str(self.big),
        )
        self.assertIn("frontend_model_configured", status)

    def test_set_model_starts_the_service_when_nothing_is_listening(self) -> None:
        """端口空着时选模型必须把服务起起来（以前什么都不做 → 下一条消息 10061）。"""

        write_config(
            self.config_path,
            base_url="http://127.0.0.1:9/v1",
            model_path=str(self.small),
        )
        control.set_frontend_model(str(self.big), config_path=self.config_path)
        self.stop_mock.assert_not_called()
        self.assertTrue(FakeManager.instances)
        self.assertTrue(FakeManager.instances[-1].ensure_called)


class EnsureStartedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp.name)
        self.server_exe = self.directory / "llama-server.exe"
        self.server_exe.write_bytes(b"stub")
        self.model = self.directory / "Qwen3.5-2B-UD-Q5_K_XL.gguf"
        self.model.write_bytes(b"stub")
        self.mmproj = self.directory / "mmproj-F16.gguf"
        self.mmproj.write_bytes(b"stub")
        self.log_dir = self.directory / "logs"
        self.log_dir.mkdir()
        self.patches = [
            patch.object(control, "runtime_dir", return_value=self.directory / "runtime"),
            patch.object(control, "status_path", return_value=self.directory / "status.json"),
            patch.object(control, "pid_path", return_value=self.directory / "qwen.pid"),
            patch.object(server_module, "log_dir", return_value=self.log_dir),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self._temp.cleanup()

    def build_manager(
        self,
        profile_vision: bool = True,
        progress: object = None,
    ) -> QwenServerManager:
        config = control.load_config(self.config_path())
        config.server.llama_server = str(self.server_exe)
        config.server.model_path = str(self.model)
        config.server.mmproj_path = str(self.mmproj)
        config.server.auto_start = True
        return QwenServerManager(
            config,
            select_runtime_profile(None, profile_vision),
            progress=progress,
        )

    def config_path(self) -> Path:
        path = self.directory / "ai_config.json"
        if not path.is_file():
            write_config(
                path,
                base_url="http://127.0.0.1:9/v1",
                model_path=str(self.model),
            )
        return path

    def test_ensure_started_refuses_to_keep_a_different_model(self) -> None:
        with FakeService(SMALL_MODEL) as service:
            write_config(
                self.config_path(),
                base_url=service.base_url,
                model_path=str(self.model),
            )
            manager = self.build_manager(profile_vision=False)
            with self.assertRaises(QwenServerError):
                manager.ensure_started()

    def test_start_command_includes_mmproj_only_for_vision(self) -> None:
        manager = self.build_manager(profile_vision=True)
        with_vision = manager._build_command(self.server_exe, self.model, self.mmproj)
        without_vision = manager._build_command(self.server_exe, self.model, None)
        self.assertIn("--mmproj", with_vision)
        self.assertNotIn("--mmproj", without_vision)

    def test_start_command_enables_the_model_chat_template(self) -> None:
        """``--jinja`` 不能丢：对话前端要靠模型的工具调用模板回灌 role=tool 结果。

        """

        manager = self.build_manager(profile_vision=False)
        command = manager._build_command(self.server_exe, self.model, None)
        self.assertIn("--jinja", command)

    def test_port_check_skips_the_slow_http_probes_and_reports_progress(self) -> None:
        """端口上没东西在听时，跳过两轮 2 秒超时的 HTTP 探测，而且一开始就报进度。

        用户看到的「窗口打开时没有进度条」就是这两轮超时（4 秒）害的：以前
        `ensure_started` 上来先问两遍 `http://127.0.0.1:8000/v1/models`，
        本机端口是静默丢包，每遍都等满 2 秒，进度条 4 秒不动。
        """

        notes: list[str] = []
        manager = self.build_manager(
            profile_vision=False,
            progress=lambda fraction, message: notes.append(message),
        )
        with (
            patch.object(server_module, "endpoint_available") as endpoint,
            patch.object(server_module, "server_model_state") as state,
            patch.object(QwenServerManager, "_spawn"),
            patch.object(QwenServerManager, "_wait_for_endpoint"),
        ):
            self.assertTrue(manager.ensure_started())
        endpoint.assert_not_called()
        state.assert_not_called()
        self.assertIn("检查本机是否已在运行模型服务…", notes)

    def test_bad_server_executable_reports_an_error_without_leaking_the_log(self) -> None:
        """llama-server 路径不对时要报中文错误，而且不能把日志文件句柄留在手里。

        以前这里漏出去的是原始 OSError，日志句柄也没关：Windows 上
        `qwen-server.log` 会一直被占着（连删都删不掉）。
        """

        manager = self.build_manager(profile_vision=False)
        with self.assertRaises(QwenServerError) as caught:
            manager.ensure_started()
        self.assertIn("llama-server", str(caught.exception))
        self.assertIsNone(manager.log_handle)


class VisionFallbackTests(unittest.TestCase):
    """mmproj 与所选模型不匹配时必须回退纯文本，而不是启动失败。"""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp.name)
        self.server_exe = self.directory / "llama-server.exe"
        self.server_exe.write_bytes(b"stub")
        self.model = self.directory / "Qwen3.5-2B-UD-Q5_K_XL.gguf"
        self.model.write_bytes(b"stub")
        self.mmproj = self.directory / "mmproj-F16.gguf"
        self.mmproj.write_bytes(b"stub")
        self.log_dir = self.directory / "logs"
        self.log_dir.mkdir()
        self.config_path = self.directory / "ai_config.json"
        write_config(
            self.config_path,
            base_url="http://127.0.0.1:9/v1",
            model_path=str(self.model),
        )
        self.patches = [patch.object(server_module, "log_dir", return_value=self.log_dir)]
        self.patches.extend(
            [
                # 这些用例只关心启动重试逻辑，不依赖真实端口探测。
                patch.object(server_module, "endpoint_available", return_value=False),
                patch.object(server_module, "server_model_state", return_value=None),
            ]
        )
        for item in self.patches:
            item.start()
        config = control.load_config(self.config_path)
        config.server.llama_server = str(self.server_exe)
        config.server.model_path = str(self.model)
        config.server.mmproj_path = str(self.mmproj)
        config.server.auto_start = True
        self.config = config

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self._temp.cleanup()

    class _Manager(QwenServerManager):
        """Fails whenever --mmproj is passed, succeeds without it."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.attempts: list[bool] = []

        def _spawn(self, command: list[str]) -> None:
            self.attempts.append("--mmproj" in command)

        def _wait_for_endpoint(self) -> None:
            if self.attempts[-1]:
                raise QwenServerError("Qwen server exited while starting.")

    def test_falls_back_to_text_mode_and_records_warning(self) -> None:
        manager = self._Manager(self.config, select_runtime_profile(7000, True))
        self.assertTrue(manager.ensure_started())
        self.assertEqual(manager.attempts, [True, False])
        self.assertFalse(manager.vision_active)
        self.assertIn("纯文本", manager.last_warning)
        log_text = (self.log_dir / "qwen-server.log").read_text(encoding="utf-8")
        self.assertIn("纯文本", log_text)

    def test_reports_error_when_text_mode_also_fails(self) -> None:
        class AlwaysFailing(self._Manager):
            def _wait_for_endpoint(self) -> None:
                raise QwenServerError("Qwen server exited while starting.")

        manager = AlwaysFailing(self.config, select_runtime_profile(7000, True))
        with self.assertRaises(QwenServerError):
            manager.ensure_started()
        self.assertEqual(manager.attempts, [True, False])

    def test_text_only_model_starts_on_first_attempt_when_no_mmproj(self) -> None:
        config = control.load_config(self.config_path)
        config.server.llama_server = str(self.server_exe)
        config.server.model_path = str(self.model)
        config.server.mmproj_path = ""
        config.server.auto_start = True
        manager = self._Manager(config, select_runtime_profile(None, False))
        self.assertTrue(manager.ensure_started())
        self.assertEqual(manager.attempts, [False])
        self.assertFalse(manager.vision_active)


class MmprojSelectionTests(unittest.TestCase):
    """每个模型应该用自己匹配的 mmproj，切换模型不能把投影文件带错。"""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp.name)
        self.server_exe = self.directory / "llama-server.exe"
        self.server_exe.write_bytes(b"stub")
        self.small_model = self.directory / "Qwen3.5-0.8B-Q4_K_M.gguf"
        self.big_model = self.directory / "Qwen3.5-2B-UD-Q5_K_XL.gguf"
        self.small_mmproj = self.directory / "mmproj-F16.gguf"
        self.big_mmproj = self.directory / "mmproj-2B-F16.gguf"
        for path in (
            self.small_model,
            self.big_model,
            self.small_mmproj,
            self.big_mmproj,
        ):
            path.write_bytes(b"stub")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def build_manager(
        self,
        model: Path,
        mmproj: Path | str,
        mapping: dict[str, str] | None = None,
        *,
        vision: bool = True,
    ) -> QwenServerManager:
        config = control.load_config(None)
        config.server.llama_server = str(self.server_exe)
        config.server.model_path = str(model)
        config.server.mmproj_path = str(mmproj)
        config.server.mmproj_by_model = dict(mapping or {})
        config.server.auto_start = True
        return QwenServerManager(config, select_runtime_profile(7000, vision))

    def test_per_model_mapping_wins_over_default(self) -> None:
        manager = self.build_manager(
            self.big_model,
            self.small_mmproj,
            {str(self.big_model): str(self.big_mmproj)},
        )
        self.assertEqual(manager._vision_mmproj(), self.big_mmproj)

    def test_default_mmproj_is_used_without_mapping_entry(self) -> None:
        manager = self.build_manager(
            self.small_model,
            self.small_mmproj,
            {str(self.big_model): str(self.big_mmproj)},
        )
        self.assertEqual(manager._vision_mmproj(), self.small_mmproj)

    def test_mapping_accepts_bare_file_name(self) -> None:
        manager = self.build_manager(
            self.big_model,
            self.small_mmproj,
            {"Qwen3.5-2B-UD-Q5_K_XL.gguf": str(self.big_mmproj)},
        )
        self.assertEqual(manager._vision_mmproj(), self.big_mmproj)

    def test_missing_mmproj_still_raises(self) -> None:
        manager = self.build_manager(
            self.big_model,
            self.directory / "missing.gguf",
            {},
        )
        with self.assertRaises(QwenServerError):
            manager._vision_mmproj()

    def test_no_mmproj_needed_when_vision_disabled(self) -> None:
        manager = self.build_manager(
            self.big_model,
            self.directory / "missing.gguf",
            {},
            vision=False,
        )
        self.assertIsNone(manager._vision_mmproj())


class SetMmprojMappingTests(unittest.TestCase):
    """set-model/set-mmproj 必须把投影文件记到对应模型名下。"""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp.name)
        self.runtime = self.directory / "runtime"
        self.runtime.mkdir()
        self.config_path = self.directory / "ai_config.json"
        self.small_model = self.directory / "Qwen3.5-0.8B-Q4_K_M.gguf"
        self.big_model = self.directory / "Qwen3.5-2B-UD-Q5_K_XL.gguf"
        self.big_mmproj = self.directory / "mmproj-2B-F16.gguf"
        self.small_mmproj = self.directory / "mmproj-F16.gguf"
        for path in (
            self.small_model,
            self.big_model,
            self.small_mmproj,
            self.big_mmproj,
        ):
            path.write_bytes(b"stub")
        write_config(
            self.config_path,
            base_url="http://127.0.0.1:9/v1",
            model_path=str(self.small_model),
        )
        self.patches = [
            patch.object(control, "runtime_dir", return_value=self.runtime),
            patch.object(control, "status_path", return_value=self.runtime / "status.json"),
            patch.object(control, "pid_path", return_value=self.runtime / "qwen.pid"),
            patch.object(control, "detect_gpu", return_value=None),
            # 端口空着时 set-model 现在会把服务起起来；这两个用例只关心 mmproj
            # 记账，所以把真正的启动换成一次状态收集。
            patch.object(
                control,
                "_launch_server",
                side_effect=lambda *args, **kwargs: control.collect_status(
                    self.config_path
                ),
            ),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self._temp.cleanup()

    def mapping(self) -> dict[str, str]:
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        return payload["server"].get("mmproj_by_model", {})

    def test_mmproj_is_recorded_for_the_model(self) -> None:
        control.set_frontend_model(
            str(self.big_model),
            str(self.big_mmproj),
            config_path=self.config_path,
        )
        self.assertEqual(self.mapping()[str(self.big_model)], str(self.big_mmproj))

    def test_existing_mapping_entries_are_kept(self) -> None:
        control.set_frontend_model(
            str(self.small_model),
            str(self.small_mmproj),
            config_path=self.config_path,
        )
        control.set_frontend_model(
            str(self.big_model),
            str(self.big_mmproj),
            config_path=self.config_path,
        )
        mapping = self.mapping()
        self.assertEqual(mapping[str(self.small_model)], str(self.small_mmproj))
        self.assertEqual(mapping[str(self.big_model)], str(self.big_mmproj))


if __name__ == "__main__":
    unittest.main()
