"""Offline React assets in pywebview / Windows WebView2, no Electron/Pi core."""
from __future__ import annotations

import argparse
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import default_config_path
from .modern_app import HostAPI, ModernApplication
from .service_lock import service_transition_lock


class AssetServer:
    def __init__(self, directory):
        root = Path(directory).resolve()
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root), **kwargs)
            def log_message(self, *args):
                pass
            def list_directory(self, path):
                self.send_error(403)
                return None
            def send_head(self):
                if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                    self.send_error(403)
                    return None
                path = Path(self.translate_path(self.path)).resolve()
                if not path.is_relative_to(root):
                    self.send_error(403)
                    return None
                return super().send_head()
            def end_headers(self):
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'")
                super().end_headers()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}/index.html'
        self.thread = threading.Thread(target=self.server.serve_forever, name='AI-offline-assets', daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def create_window(application, *, assets=None, hidden=False):
    import webview
    assets = Path(assets or Path(__file__).resolve().parents[1] / 'frontend' / 'dist')
    if not (assets / 'index.html').is_file():
        raise RuntimeError('现代前端离线资产未构建；请在 ai/frontend 运行 npm ci && npm run build，或使用 --legacy')
    server = AssetServer(assets)
    api = HostAPI(application)
    window = webview.create_window('AIPraat · AI 工作台', server.url, js_api=api, width=900, height=640,
                                   min_size=(800, 600), hidden=hidden, text_select=True)
    application.window = window
    api._origin = server.url.rsplit('/', 1)[0]
    window.events.closed += application.close
    # Adapt the pywebview examples/events.py closed-event subscription (BSD-3-Clause).
    from .desktop_launch import clear_own_records
    window.events.closed += clear_own_records
    from .desktop_window import focus_existing_window
    window.events.shown += lambda: focus_existing_window(os.getpid())
    return window, server


def watch_native_parent(window):
    """Only watch an explicitly bound menu parent, never guess the current Praat."""
    from .parent_watch import parent_pid
    from .process import identities_match, process_identity
    pid = parent_pid()
    original = process_identity(pid) if pid else None
    if not original:
        return
    stopped = threading.Event()
    window.events.closed += stopped.set
    while not stopped.wait(1):
        if not identities_match(original, process_identity(pid)):
            window.destroy()
            return


def main(argv=None):
    parser = argparse.ArgumentParser(description='AIPraat modern assistant-ui / WebView2 frontend')
    parser.add_argument('--legacy', action='store_true', help='显式使用旧 Tk 窗口')
    parser.add_argument('--allow-cloud', action='store_true', help='单独授权此进程真实远程模型请求（可能产生费用）')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'runtime' / 'modern')
    parser.add_argument('--config', type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    # Bridge files are shared even when callers use different modern data dirs.
    shared_runtime = Path(__file__).resolve().parents[1] / 'runtime'
    try:
        with service_transition_lock(shared_runtime / 'frontend.lock', timeout=1):
            if args.legacy:
                from .chat import ChatWindow
                return ChatWindow().run()
            import webview
            args.data_dir.mkdir(parents=True, exist_ok=True)
            application = ModernApplication(args.data_dir, args.config, shared_runtime / 'conversations.sqlite3',
                                            allow_cloud=args.allow_cloud, configured_cloud=True)
            window, server = create_window(application)
            from .desktop_launch import connected_record
            window._js_api._on_connected = lambda: connected_record(type(application.executor).__name__)
            try:
                webview.start(lambda: watch_native_parent(window), gui='edgechromium' if os.name == 'nt' else None, private_mode=True,
                              storage_path=str(args.data_dir / 'webview-profile'))
            finally:
                application.close()
                server.close()
        return 0
    except Exception as error:
        from .desktop_launch import report_failure
        return report_failure(error, runtime=shared_runtime, config_path=args.config)
