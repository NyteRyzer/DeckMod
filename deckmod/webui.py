"""Local web interface (http://127.0.0.1:8765) - big buttons, works with touch and the Deck's trackpads."""

from __future__ import annotations

import json
import secrets
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import __version__, archive, config, games
from .manager import GameManager

TOKEN = secrets.token_urlsafe(24)
_games_cache: list[games.Game] | None = None
MAX_UPLOAD = 8 * 1024 ** 3


def _games(refresh: bool = False) -> list[games.Game]:
    global _games_cache
    if refresh or _games_cache is None:
        _games_cache = games.scan_all()
    return _games_cache


def _mgr(game_id: str) -> GameManager:
    return GameManager(games.find_game(game_id, _games()))


def _download_candidates() -> list[dict]:
    dirs = [config.HOME / "Downloads", config.downloads_dir()]
    out = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.is_file() and p.name.lower().endswith(archive.ARCHIVE_SUFFIXES + (".pak", ".dll", ".esp", ".esm")):
                st = p.stat()
                out.append({"path": str(p), "name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    return sorted(out, key=lambda x: -x["mtime"])[:60]


class Handler(BaseHTTPRequestHandler):
    server_version = f"DeckMod/{__version__}"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # ---------------------------------------------------------- helpers

    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self) -> bool:
        # Blocks DNS-rebinding: only answer requests addressed to localhost.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # ---------------------------------------------------------- routes

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        try:
            if url.path == "/":
                html = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")
                return self._send(200, html.replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")
            if url.path == "/api/games":
                gs = _games(q.get("refresh") == "1")
                return self._send(200, [g.to_dict() for g in gs])
            if url.path == "/api/game":
                return self._send(200, _mgr(q["id"]).info())
            if url.path == "/api/downloads":
                return self._send(200, _download_candidates())
            return self._send(404, {"error": "not found"})
        except Exception as exc:
            return self._send(400, {"error": str(exc)})

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-DeckMod-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        try:
            if url.path == "/api/upload":
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > MAX_UPLOAD:
                    raise ValueError("Empty or too large upload.")
                filename = config.safe_name(Path(q.get("filename", "upload.zip")).name)
                dest = config.downloads_dir() / filename
                with open(dest, "wb") as fh:
                    remaining = n
                    while remaining:
                        chunk = self.rfile.read(min(remaining, 1 << 20))
                        if not chunk:
                            break
                        fh.write(chunk)
                        remaining -= len(chunk)
                m = _mgr(q["game"])
                mod = m.install(str(dest), display_name=filename)
                return self._send(200, {"mod": mod, "deploy": m.last_deploy})

            body = self._json_body()
            m = _mgr(body["game"])
            action = url.path.rsplit("/", 1)[-1]
            result = None
            if action == "install":
                src = body.get("source", "").strip()
                if not src:
                    raise ValueError("Pick a file or paste a path/URL.")
                result = m.install(src, name=body.get("name") or None,
                                   target=body.get("target") if body.get("target") is not None else None)
            elif action == "enable":
                result = m.set_enabled(body["mod"], bool(body.get("value", True)))
            elif action == "move":
                result = m.move(body["mod"], int(body["value"]))
            elif action == "target":
                result = m.set_target(body["mod"], body.get("value") or "")
            elif action == "uninstall":
                result = m.uninstall(body["mod"])
            elif action == "deploy":
                result = m.deploy()
            elif action == "purge":
                result = m.undeploy()
            elif action == "set-path":
                m.set_path_override(body.get("value") or None)
            else:
                return self._send(404, {"error": "unknown action"})
            return self._send(200, {"result": result, "deploy": m.last_deploy})
        except Exception as exc:
            traceback.print_exc()
            return self._send(400, {"error": str(exc)})


def serve(port: int = 8765, open_browser: bool = True) -> None:
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"DeckMod is running at {url}  (Ctrl+C to quit)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")
    finally:
        httpd.server_close()
