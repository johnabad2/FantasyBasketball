"""Fantasy Hoops as a web app in its own window (the new look). Still being built tab by tab; the
desktop window (main.py) keeps working meanwhile.

    .venv\\Scripts\\python app\\webapp.py            its own window
    .venv\\Scripts\\python app\\webapp.py --browser  in your web browser instead
    .venv\\Scripts\\python app\\webapp.py --demo     made-up league, no ESPN needed

A small local server (only reachable from this PC) hands the page its data as JSON; the data is
loaded with the same code as the desktop app (loader.py) and refreshed every 15 minutes."""
import argparse
import json
import mimetypes
import socket
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fantasy
import lineup
import loader
import views
import views_more

WEB = Path(__file__).resolve().parent / "web"
REFRESH_MIN = 15


class State:
    """The latest data from ESPN, reloaded in the background so the page never waits on ESPN."""

    def __init__(self, demo):
        self.demo, self.data, self.updated, self.error = demo, None, None, None
        self.loading, self.roles, self.pages = False, {}, {}
        self._lock = threading.Lock()

    def refresh(self):
        with self._lock:
            if self.loading:
                return
            self.loading = True
        try:
            data = loader.load(self.demo)
            # pages that don't change until the next refresh are built once here, not on every poll
            self.pages = {"header": views.header(data), "standings": views.standings(data),
                          "week": views.week_ahead(data), "schedule": views.schedule_view(data),
                          "fa": views_more.free_agents_trades(data), "teams": views_more.league_teams(data),
                          "news": views_more.news_view(data), "activity": views_more.activity_view(data),
                          "rosters": views_more.trade_rosters(data)}
            self.data, self.updated, self.error = data, datetime.now(), None
        except SystemExit as e:  # fantasy/stats explain user-facing problems this way
            self.error = str(e)
        except Exception as e:
            self.error = f"Couldn't load from ESPN: {type(e).__name__}: {e}"
        finally:
            self.loading = False
        if self.data and not self.demo:
            self._load_roles()

    def _load_roles(self):
        """NBA roles (recent starts) need box scores: slow the first time, cached after."""
        d = self.data
        missing = [p.playerId for p in d["me"].roster if p.playerId not in self.roles]
        if missing:
            try:
                self.roles.update(lineup.nba_roles(missing, d["league"].year))
            except Exception:
                pass

    def refresh_soon(self):
        threading.Thread(target=self.refresh, daemon=True).start()

    def snapshot(self):
        out = {"loading": self.loading, "error": self.error,
               "updated": self.updated.strftime("%H:%M") if self.updated else None,
               "refresh_min": REFRESH_MIN}
        d = self.data
        if d:
            out.update(self.pages, my_team=views.my_team(d, self.roles))
        return out


def _json(obj):
    def plain(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        if isinstance(o, (set, frozenset, tuple)):
            return sorted(o)
        return str(o)
    return json.dumps(obj, default=plain).encode()


def handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def _send(self, body, ctype="application/json", status=200, cache=False):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse
            url = urlparse(self.path)
            path, q = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
            if path == "/api/state":
                return self._send(_json(state.snapshot()))
            ask = {  # pages that answer what you pick or type
                "/api/league": lambda d: views_more.league_roster(d, int(q.get("team") or 0), q.get("split")),
                "/api/player": lambda d: views_more.player(d, q.get("q", "")),
                "/api/compare": lambda d: views_more.compare(d, q.get("a", ""), q.get("b", ""), q.get("split"),
                                                             q.get("mode", "fantasy")),
                "/api/news": lambda d: views_more.player_news(int(q.get("id") or 0)) if not state.demo else [],
            }.get(path)
            if ask:
                if not state.data:
                    return self._send(_json({"error": "Still loading from ESPN."}))
                try:
                    return self._send(_json(ask(state.data)))
                except Exception as e:
                    return self._send(_json({"error": f"{type(e).__name__}: {e}"}))
            if path.startswith("/photo/"):
                pid = path.rsplit("/", 1)[-1]
                photo = loader.headshot(pid) if pid.isdigit() and not state.demo else None
                if photo:
                    return self._send(photo.read_bytes(), "image/png", cache=True)
                return self._send(b"", "image/png", 404)
            file = (WEB / (path.lstrip("/") or "index.html")).resolve()
            if WEB in file.parents and file.is_file():
                ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                return self._send(file.read_bytes(), ctype)
            self._send(b"not found", "text/plain", 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/refresh":
                state.refresh_soon()
                return self._send(_json({"ok": True}))
            if self.path == "/api/trade" and state.data:
                return self._send(_json(views_more.trade_lab(state.data, body.get("a"), body.get("b"),
                                                             body.get("send") or [], body.get("get") or [])))
            if self.path == "/api/classic":  # open the original window for tabs not moved over yet
                import subprocess
                import sys
                exe = Path(sys.executable)
                pythonw = exe.with_name("pythonw.exe")
                subprocess.Popen([str(pythonw if pythonw.exists() else exe), str(Path(__file__).with_name("main.py"))]
                                 + (["--demo"] if state.demo else []))
                return self._send(_json({"ok": True}))
            if self.path == "/api/keep" and state.data:
                player = next((p for p in state.data["me"].roster if p.playerId == body.get("id")), None)
                if player:
                    fantasy.set_keep(player, bool(body.get("keep")))
                    state.refresh_soon()  # suggestions everywhere are rebuilt with (or without) him
                    return self._send(_json({"ok": True}))
            self._send(_json({"ok": False}), status=400)
    return Handler


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser(description="Fantasy Hoops web app")
    ap.add_argument("--demo", action="store_true", help="made-up league, no ESPN needed")
    ap.add_argument("--browser", action="store_true", help="open in your web browser instead of its own window")
    ap.add_argument("--serve", action="store_true", help="only run the server (open the address yourself)")
    ap.add_argument("--port", type=int, default=0, help="port for the local server (default: any free one)")
    args = ap.parse_args()

    state = State(args.demo)
    state.refresh_soon()

    def auto_refresh():
        while True:
            time.sleep(REFRESH_MIN * 60)
            state.refresh()
    threading.Thread(target=auto_refresh, daemon=True).start()

    port = args.port or free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(state))
    url = f"http://127.0.0.1:{port}/"
    if args.browser or args.serve:
        print(f"Fantasy Hoops is running at {url}  (Ctrl+C to stop)", flush=True)
        if args.browser:
            import webbrowser
            webbrowser.open(url)
        server.serve_forever()
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    import webview
    webview.create_window("Fantasy Hoops" + (" (demo)" if args.demo else ""), url, width=1560, height=980,
                          min_size=(900, 600), background_color="#0b0f16")
    webview.start()


if __name__ == "__main__":
    main()
