"""Both API settings entrances must apply the same local-service policy."""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from praat_ai import api_settings, chat, control
from praat_ai.chat import ChatWindow, local_preset_allowed
from praat_ai.config import AppConfig, ServerPreset, load_config
from praat_ai.server import QwenServerError


def write_config(directory: Path, *, enabled: bool, stop_local: bool = True) -> Path:
    config = AppConfig()
    config.server.host = "127.0.0.1"
    config.server.port = 18008
    config.api.enabled = enabled
    config.api.base_url = "https://example.test/v1"
    config.api.model = "remote-model"
    config.api.api_key = "test-key"
    config.api.stop_local_service = stop_local
    path = directory / "ai_config.json"
    path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
    return path


class ApiTransitionTests(unittest.TestCase):
    def test_local_menu_policy_covers_all_lock_and_stop_combinations(self) -> None:
        for locked in (False, True):
            for stopped in (False, True):
                with self.subTest(locked=locked, stopped=stopped):
                    config = AppConfig()
                    config.api.enabled = True
                    config.api.locked = locked
                    config.api.stop_local_service = stopped
                    config.api.base_url = "https://example.test/v1"
                    config.api.model = "remote-model"
                    self.assertEqual(local_preset_allowed(config), not (locked or stopped))
                    config.api.enabled = False
                    self.assertEqual(local_preset_allowed(config), not locked)

    def test_legacy_enabled_api_is_locked_until_user_unlocks_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_config(Path(raw), enabled=True, stop_local=False)
            payload = json.loads(path.read_text(encoding="utf-8"))
            del payload["api"]["locked"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(load_config(path).api.locked)
            api_settings.save_settings({"locked": False}, path)
            config = load_config(path)
            self.assertTrue(config.api.enabled)
            self.assertFalse(config.api.locked)

    def test_top_bar_can_activate_saved_api_from_local_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_config(Path(raw), enabled=False, stop_local=False)
            with patch.object(control, "reconcile_api_transition", return_value={"success": True}):
                control.activate_api(path)
            config = load_config(path)
            self.assertTrue(config.api.enabled)
            self.assertEqual(config.api.model, "remote-model")

    def test_top_bar_uses_first_provider_if_no_api_was_configured(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ai_config.json"
            path.write_text(json.dumps(AppConfig().to_dict()), encoding="utf-8")
            with patch.object(control, "reconcile_api_transition", return_value={"success": True}):
                control.activate_api(path)
            config = load_config(path)
            self.assertEqual(config.api.label, api_settings.PROVIDERS[0]["label"])
            self.assertEqual(config.api.base_url, api_settings.PROVIDERS[0]["base_url"])
            self.assertEqual(config.api.model, api_settings.PROVIDERS[0]["model"])

    def test_lock_and_new_stop_choice_switch_current_local_selection_to_api(self) -> None:
        for change in ({"locked": True}, {"stop_local_service": True}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as raw:
                path = write_config(Path(raw), enabled=False, stop_local=False)
                api_settings.save_settings(change, path)
                config = load_config(path)
                self.assertTrue(config.api.enabled)
                self.assertEqual(config.api.model, "remote-model")
                self.assertFalse(local_preset_allowed(config))

    def test_unlocking_keeps_api_selected_and_allows_local_choice(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_config(Path(raw), enabled=True, stop_local=False)
            api_settings.save_settings({"locked": True}, path)
            api_settings.save_settings({"locked": False}, path)
            config = load_config(path)
            self.assertTrue(config.api.enabled)
            self.assertTrue(local_preset_allowed(config))

    def test_partial_custom_api_is_preserved_and_requires_completion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_config(Path(raw), enabled=False, stop_local=False)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["api"]["base_url"] = "https://gateway.example.test/v1"
            payload["api"]["model"] = ""
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                api_settings.save_settings({"locked": True}, path)
            with self.assertRaises(ValueError):
                control.activate_api(path)
            config = load_config(path)
            self.assertEqual(config.api.base_url, "https://gateway.example.test/v1")
            self.assertEqual(config.api.model, "")
            self.assertFalse(config.api.enabled)

    def test_top_menu_grays_local_items_and_keeps_cloud_entry_visible(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            path = write_config(directory, enabled=True, stop_local=False)
            with (
                patch.dict(os.environ, {"PRAAT_AI_CONFIG_PATH": str(path)}),
                patch.object(chat, "runtime_dir", return_value=directory),
                patch.object(chat, "selected_object_label", return_value=""),
                patch("praat_ai.parent_watch.should_watch", return_value=False),
            ):
                try:
                    window = ChatWindow()
                except tk.TclError as error:
                    self.skipTest(f"Tk unavailable: {error}")
                try:
                    window.presets = [{"id": "local", "label": "本地测试", "active": True,
                                       "available": True, "vision": False, "model": "local.gguf",
                                       "mmproj": "", "context_tokens": 0}]
                    window.root.update()
                    self.assertIs(window.preset_menu.master, window.preset_box)
                    self.assertIn("Menubutton.indicator", repr(ttk.Style(window.root).layout(window.preset_box.cget("style"))))
                    # Windows 原生菜单的 tk_popup 会等待鼠标选择；这里只截获弹出入口，
                    # 仍从真正的鼠标绑定触发，避免 Menu.invoke 绕过展开流程。
                    window.root.tk.call("rename", "tk_popup", "_test_original_tk_popup")
                    window.root.tk.eval("proc tk_popup {menu args} {set ::test_posted_menu $menu}")
                    try:
                        window.preset_box.event_generate("<Enter>")
                        window.preset_box.event_generate("<ButtonPress-1>", x=10, y=10)
                        window.root.update()
                        self.assertEqual(str(window.root.tk.getvar("test_posted_menu")), str(window.preset_menu))
                    finally:
                        window.root.tk.call("rename", "tk_popup", "")
                        window.root.tk.call("rename", "_test_original_tk_popup", "tk_popup")
                        window.preset_box.state(["!pressed", "!active"])
                    for locked, stopped, expected in (
                        (False, False, "normal"),
                        (True, False, "disabled"),
                        (False, True, "disabled"),
                        (True, True, "disabled"),
                    ):
                        with self.subTest(locked=locked, stopped=stopped):
                            window.config.api.locked = locked
                            window.config.api.stop_local_service = stopped
                            window.refresh_preset_widgets()
                            self.assertEqual(window.preset_menu.entrycget(0, "state"), "normal")
                            self.assertEqual(window.preset_menu.entrycget(1, "state"), expected)
                            self.assertEqual(window.preset_choice.get(), window.api_choice)
                    window.config.api.locked = False
                    window.config.api.stop_local_service = False
                    window.refresh_preset_widgets()
                    window.busy = True
                    window.preset_menu.invoke(1)
                    self.assertEqual(window.preset_choice.get(), window.api_choice)
                    window.busy = False
                    window.config.api.enabled = False
                    window.config.api.locked = False
                    window.refresh_preset_widgets()
                    self.assertEqual(window.preset_menu.entrycget(0, "state"), "normal")
                    self.assertEqual(window.preset_menu.entrycget(1, "state"), "normal")
                    self.assertNotEqual(window.preset_choice.get(), window.api_choice)
                    with patch.object(window, "apply_selected_preset") as switch:
                        window.preset_menu.invoke(0)
                        switch.assert_called_once_with()
                    self.assertEqual(
                        ttk.Style(window.root).lookup(
                            window.preset_box.cget("style"), "background"
                        ),
                        window.theme.color("surface"),
                    )
                finally:
                    window.close()

    def test_top_menu_selection_really_switches_cloud_local_cloud(self) -> None:
        import tkinter as tk

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            model_path = directory / "local.gguf"
            model_path.write_bytes(b"stub")
            config = AppConfig()
            config.api.enabled = True
            config.api.locked = False
            config.api.stop_local_service = False
            config.api.base_url = "https://example.test/v1"
            config.api.model = "remote-model"
            config.server.presets = [ServerPreset(id="local", label="本地测试", model_path=str(model_path))]
            config.server.active_preset = "local"
            path = directory / "ai_config.json"
            path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            with (
                patch.dict(os.environ, {"PRAAT_AI_CONFIG_PATH": str(path)}),
                patch.object(chat, "runtime_dir", return_value=directory),
                patch.object(chat, "selected_object_label", return_value=""),
                patch("praat_ai.parent_watch.should_watch", return_value=False),
                patch.object(control, "collect_status", return_value={"success": True}),
                patch.object(control, "ensure_local_service", return_value={"frontend_preset_label": "本地测试", "frontend_model": "local.gguf"}),
                patch.object(control, "reconcile_api_transition", return_value={"success": True}),
            ):
                try:
                    window = ChatWindow()
                except tk.TclError as error:
                    self.skipTest(f"Tk unavailable: {error}")
                try:
                    for index, expected in ((1, False), (0, True)):
                        window.preset_menu.invoke(index)
                        deadline = time.monotonic() + 3
                        while window.busy and time.monotonic() < deadline:
                            window.flush_messages()
                            time.sleep(0.01)
                        self.assertFalse(window.busy)
                        self.assertEqual(load_config(path).api.enabled, expected)
                finally:
                    window.close()

    def test_settings_callback_failure_is_visible_after_save(self) -> None:
        dialog = object.__new__(api_settings.ApiSettingsDialog)
        dialog.collect = lambda: {"enabled": False}
        dialog.config_path = Path("unused.json")
        dialog.verified = False
        dialog.window = object()
        dialog._messagebox = SimpleNamespace(showerror=Mock(), showwarning=Mock())
        dialog.on_saved = lambda _values: (_ for _ in ()).throw(RuntimeError("queue failed"))
        dialog.close = Mock()
        with patch.object(api_settings, "save_settings"):
            dialog.save()
        dialog._messagebox.showerror.assert_called_once()
        self.assertIn("queue failed", dialog._messagebox.showerror.call_args.args[1])
        dialog.close.assert_not_called()

    def test_concurrent_saves_converge_on_the_latest_api_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            path = write_config(directory, enabled=False)
            starting = threading.Event()
            release_start = threading.Event()
            stopped = threading.Event()
            state = {"running": False}
            errors: list[BaseException] = []

            def slow_start(*_args, **_kwargs):
                starting.set()
                if not release_start.wait(3):
                    raise TimeoutError("test did not release local startup")
                state["running"] = True
                return {"api_enabled": False}

            def stop(*_args, **_kwargs):
                state["running"] = False
                stopped.set()
                return {"api_enabled": True}

            def apply() -> None:
                try:
                    control.reconcile_api_transition(path)
                except BaseException as error:  # keep worker failures visible to this test
                    errors.append(error)

            with (
                patch.object(control, "runtime_dir", return_value=directory),
                patch.object(control, "ensure_local_service", side_effect=slow_start),
                patch.object(control, "stop_frontend", side_effect=stop),
            ):
                first = threading.Thread(target=apply)
                first.start()
                self.assertTrue(starting.wait(2))
                write_config(directory, enabled=True)
                second = threading.Thread(target=apply)
                second.start()
                stopped.wait(0.2)
                release_start.set()
                first.join(3)
                second.join(3)

        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse(state["running"], "old local startup must not win the race")

    def test_api_switch_reports_when_an_unowned_service_still_occupies_port(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_config(Path(raw), enabled=True)
            with (
                patch.object(control, "_stop_running_service", return_value=False),
                patch.object(control, "endpoint_available", return_value=True),
                patch.object(control, "_wait_for_endpoint_gone"),
                patch.object(control, "collect_status", return_value={"success": True}),
            ):
                with self.assertRaises(QwenServerError):
                    control.reconcile_api_transition(path)

    def test_waiting_for_stopped_endpoint_raises_on_timeout(self) -> None:
        with patch.object(control, "endpoint_available", return_value=True):
            with self.assertRaises(QwenServerError):
                control._wait_for_endpoint_gone("http://127.0.0.1:18008/v1", timeout=0)

    def test_enabling_api_stops_only_the_configured_local_service(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_config(Path(raw), enabled=True)
            with (
                patch.object(control, "_stop_running_service", return_value=True) as stop,
                patch.object(control, "_wait_for_endpoint_gone") as wait,
                patch.object(control, "collect_status", return_value={"success": True}),
            ):
                control.reconcile_api_transition(path)
        self.assertEqual(stop.call_count, 1)
        wait.assert_called_once_with("http://127.0.0.1:18008/v1")

    def test_enabling_api_with_keep_local_option_does_not_stop_service(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_config(Path(raw), enabled=True, stop_local=False)
            with (
                patch.object(control, "_stop_running_service") as stop,
                patch.object(control, "collect_status", return_value={"success": True}),
            ):
                control.reconcile_api_transition(path)
        stop.assert_not_called()

    def test_disabling_api_starts_local_service_when_port_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_config(Path(raw), enabled=False)
            with (
                patch.object(control, "endpoint_available", return_value=False),
                patch.object(control, "_launch_server", return_value={"success": True}) as launch,
            ):
                control.reconcile_api_transition(path)
        self.assertEqual(launch.call_count, 1)

    def test_standalone_entry_redirects_without_changing_service_or_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_config(Path(raw), enabled=True)
            before = path.read_bytes()
            with patch("start_api_settings.main", return_value=0) as navigate, \
                 patch.object(control, "reconcile_api_transition") as reconcile:
                self.assertEqual(api_settings.run_standalone(path), 0)
            navigate.assert_called_once_with(path)
            reconcile.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_chat_dialog_queues_same_transition_for_api_and_local_modes(self) -> None:
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                config = AppConfig()
                config.api.enabled = enabled
                config.api.base_url = "https://example.test/v1"
                config.api.model = "remote-model"
                config.api.api_key = "test-key"
                messages: queue.Queue[tuple[str, str]] = queue.Queue()
                window = SimpleNamespace(
                    config=config,
                    messages=messages,
                    reload_config=lambda: None,
                    append_hint=lambda _message: None,
                )
                ChatWindow.on_api_settings_saved(window, {})
                self.assertEqual(messages.get_nowait(), ("reconcile-service", ""))


def tearDownModule():
    # Tk 解释器必须在主线程回收：否则后端线程的下一次 GC 会析构它，触发 Tcl_AsyncDelete。
    import gc

    gc.collect()


if __name__ == "__main__":
    unittest.main()
