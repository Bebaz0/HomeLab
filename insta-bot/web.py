"""Control panel for insta-bot: static page in web/ plus a small JSON API.

Listens on the docker0 address only (like home-assistant), so it is reachable through
Caddy (insta-bot.homelab, basic auth) and not directly from the LAN.
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config
import db
from config import DATA, HERE
from trigger import State

BIND = os.environ.get("WEB_BIND", "172.17.0.1:8091")
STATE = DATA / "state.json"
STOPPED = DATA / "STOPPED"
UNIT = "insta-bot.service"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("insta-bot-web")


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                          timeout=40)


def running() -> bool:
    return systemctl("is-active", "--quiet", UNIT).returncode == 0


def next_start(cfg: dict, now: datetime) -> str | None:
    if config.in_window(now, cfg["start"], cfg["end"]):
        return None
    h, m = map(int, cfg["start"].split(":"))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return at.isoformat(timespec="minutes")


def status() -> dict:
    cfg, now = config.load(), datetime.now()
    state = State.load(STATE)
    left = state.last_reply + cfg["cooldown_minutes"] * 60 - time.time()
    return {
        "running": running(),
        "enabled": cfg["enabled"],
        "dry_run": cfg["dry_run"],
        "in_window": config.in_window(now, cfg["start"], cfg["end"]),
        "start": cfg["start"],
        "end": cfg["end"],
        "next_start": next_start(cfg, now),
        "cooldown_left": max(0, round(left)),
        "last_reply": state.last_reply or None,
        "stopped": STOPPED.read_text().strip() if STOPPED.exists() else None,
        "now": now.isoformat(timespec="seconds"),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE / "web"), **kw)

    def log_message(self, fmt, *args):   # quieter than the default stderr spam
        if not self.path.startswith("/api/status"):
            log.info("%s %s", self.command, self.path)

    def send_json(self, body, code=HTTPStatus.OK):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def body(self) -> dict | None:
        # Only JSON bodies: a cross-site <form> can't send application/json without a
        # CORS preflight, so the browser's cached basic-auth can't be abused (CSRF).
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return None
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    # ---------- routes ----------

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)

        def limit(default: int) -> int:
            try:
                return max(1, min(int(q.get("limit", [default])[0]), 500))
            except ValueError:
                return default
        if url.path == "/api/status":
            return self.send_json(status())
        if url.path == "/api/config":
            return self.send_json(config.load())
        if url.path == "/api/history":
            return self.send_json(db.recent(limit(100)))
        if url.path == "/api/replies":
            return self.send_json(db.recent_replies(limit(20)))
        if url.path.startswith("/api/"):
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        return super().do_GET()

    def do_PUT(self):
        if urlparse(self.path).path != "/api/config":
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        data = self.body()
        if data is None:
            return self.send_json({"error": "JSON inválido"}, HTTPStatus.BAD_REQUEST)
        cfg, errors = config.validate(data)
        if errors:
            return self.send_json({"errors": errors}, HTTPStatus.BAD_REQUEST)
        config.save(cfg)
        return self.send_json(cfg)

    def do_POST(self):
        path = urlparse(self.path).path
        if self.body() is None:
            return self.send_json({"error": "JSON inválido"}, HTTPStatus.BAD_REQUEST)
        if path == "/api/bot/on":
            cfg = config.load() | {"enabled": True}
            config.save(cfg)
            # Start now instead of waiting for the next timer tick; ExecCondition still
            # refuses outside the window or while STOPPED.
            systemctl("start", "--no-block", UNIT)
        elif path == "/api/bot/off":
            config.save(config.load() | {"enabled": False})
            systemctl("stop", UNIT)
        elif path == "/api/cooldown/reset":
            STATE.unlink(missing_ok=True)
        elif path == "/api/stopped/clear":
            STOPPED.unlink(missing_ok=True)
        else:
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        return self.send_json(status())


def main() -> None:
    host, port = BIND.rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port)), Handler)
    log.info("panel on http://%s", BIND)
    server.serve_forever()


if __name__ == "__main__":
    main()
