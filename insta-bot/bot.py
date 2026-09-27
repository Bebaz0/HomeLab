"""Keep one Instagram group thread open in real Chrome and answer game invites.

One run = one evening. insta-bot.timer ticks every ~5 min and `bot.py --should-run`
(ExecCondition) decides whether to start: enabled in the panel, inside the start/end
window from data/config.json, and not STOPPED. The bot exits when the window ends
(+0-10 min) or when the panel disables it. Anything that looks like login/verification stops the bot for good (data/STOPPED)
until a human fixes it through phase0.sh.
"""

import asyncio
import fcntl
import json
import logging
import os
import random
import re
import signal
import sys
import time
import urllib.request
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config
import db
from config import DATA, HERE
from trigger import State, pick_reply, should_reply

PROFILE = DATA / "profile"
STATE = DATA / "state.json"
PAUSE = DATA / "PAUSE"
STOPPED = DATA / "STOPPED"
LOCK = DATA / "profile.lock"

THREAD_URL = "https://www." + re.sub(r"^(https?://)?(www\.)?", "", os.environ.get("THREAD_URL", "").strip())
MY_USERNAME = os.environ.get("MY_USERNAME", "")
CONTAINER_SELECTOR = os.environ.get(
    "CONTAINER_SELECTOR",
    '[role="main"]')
ROW_SELECTOR = os.environ.get("ROW_SELECTOR", 'div[role="group"]:not([aria-label])')

HEALTH_EVERY = 300
WATCH_EVERY = 10             # how often the panel's on/off and hours are re-checked
MAX_RECOVERY_RELOADS = 3
RELOAD_GAP = 600
QUIET_RELOAD = 3600          # silent WebSocket guard: reload if no DOM change for this long

FATAL_PATHS = ("/accounts/login", "/challenge", "/suspended", "/checkpoint")
FATAL_TEXT = re.compile(r"suspicious activity|atividade suspeita|confirm it'?s you|"
                        r"confirma que és tu|we detected|detetámos", re.I)
OFFLINE_TEXT = re.compile(r"a tentar ligar|reconnecting|sem ligação à internet|"
                          r"no internet connection", re.I)
DISMISS = re.compile(r"^(agora não|not now|recusar cookies opcionais|"
                     r"decline optional cookies)$", re.I)

DATA.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              RotatingFileHandler(DATA / "bot.log", maxBytes=1_000_000, backupCount=5)],
)
log = logging.getLogger("insta-bot")


class Fatal(Exception):
    pass


def notify(title: str, message: str, priority: str = "default", tags: str = "") -> None:
    url = os.environ.get("NTFY_URL")
    if not url:
        return
    try:
        req = urllib.request.Request(
            url, data=message.encode(),
            headers={"Title": title, "Priority": priority, "Tags": tags})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        log.warning("ntfy notification failed", exc_info=True)


def why_not_run(cfg: dict) -> str | None:
    """None if the bot should be running now, else the reason it shouldn't."""
    if STOPPED.exists():
        return "data/STOPPED exists; fix the session with phase0.sh first"
    if not cfg["enabled"]:
        return "disabled in the panel"
    if not config.in_window(datetime.now(), cfg["start"], cfg["end"]):
        return f"outside {cfg['start']}-{cfg['end']}"
    return None


def mark_clean_exit() -> None:
    """Stop Chrome's "Restore pages?" bubble after a SIGKILL or crash."""
    prefs = PROFILE / "Default" / "Preferences"
    try:
        d = json.loads(prefs.read_text())
    except (FileNotFoundError, ValueError):
        return
    d.setdefault("profile", {}).update(exit_type="Normal", exited_cleanly=True)
    prefs.write_text(json.dumps(d))


class Bot:
    def __init__(self, ctx, page):
        self.ctx, self.page = ctx, page
        self.queue: asyncio.Queue = asyncio.Queue()
        self.end_jitter = random.randint(0, 10)   # minutes past the configured end
        self.reloads = 0
        self.last_reload = 0.0

    # ---------- page ----------

    def textbox(self):
        # The chat box has no accessible name (only aria-placeholder="Mensagem..."), and the
        # search box is also a textbox; the chat box is the only contenteditable one.
        return self.page.locator('div[role="textbox"][contenteditable="true"]')

    async def dismiss_popups(self) -> None:
        buttons = self.page.get_by_role("button", name=DISMISS)
        for i in range(await buttons.count()):
            b = buttons.nth(i)
            if await b.is_visible():
                log.info("dismissing popup: %s", await b.inner_text())
                await b.click()
                await asyncio.sleep(random.uniform(0.8, 1.5))

    async def check_fatal(self) -> None:
        url = self.page.url
        if any(p in url for p in FATAL_PATHS):
            raise Fatal(f"redirected to {url}")
        # Only read the page text when the chat box is gone, so a friend typing
        # "atividade suspeita" in the group cannot stop the bot.
        if not await self.textbox().count():
            body = await self.page.locator("body").inner_text()
            if m := FATAL_TEXT.search(body):
                raise Fatal(f"page says: {m.group(0)!r}")

    async def is_alive(self) -> tuple[bool, str]:
        if not self.page.url.startswith(THREAD_URL.rstrip("/")):
            return False, f"left thread ({self.page.url})"
        if not await self.page.evaluate("navigator.onLine"):
            return False, "navigator offline"
        if not await self.textbox().count():
            return False, "textbox missing"
        banner = self.page.get_by_text(OFFLINE_TEXT)
        if await banner.count() and await banner.first.is_visible():
            return False, "reconnect banner"
        return True, ""

    async def open_thread(self) -> None:
        await self.page.goto(THREAD_URL, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(random.uniform(4000, 7000))
        await self.dismiss_popups()
        await self.check_fatal()
        ok = await self.page.evaluate(
            (HERE / "observer.js").read_text(),
            {"containerSelector": CONTAINER_SELECTOR, "rowSelector": ROW_SELECTOR})
        if not ok:
            raise Fatal("conversation container not found (selectors need updating?)")
        self.last_reload = time.time()
        log.info("thread open, observer installed (baseline taken)")

    async def reload(self, why: str, counts: bool) -> None:
        if counts:
            if self.reloads >= MAX_RECOVERY_RELOADS:
                raise Fatal(f"page dead after {self.reloads} reloads: {why}")
            if time.time() - self.last_reload < RELOAD_GAP:
                log.info("page unhealthy (%s), waiting for reload gap", why)
                return
            self.reloads += 1
        log.warning("reloading thread (%s)%s", why,
                    f" [{self.reloads}/{MAX_RECOVERY_RELOADS}]" if counts else "")
        await self.open_thread()

    # ---------- loops ----------

    async def on_message(self, msg: dict) -> None:
        if msg.get("resync"):
            log.warning("observer resync (%s), nothing answered", msg.get("why"))
            return
        await self.queue.put(msg)

    async def responder(self) -> None:
        while True:
            msg = await self.queue.get()
            text, sender = msg.get("text", ""), msg.get("sender", "other")
            # Re-read both every time: the panel edits config and can reset the cooldown.
            cfg, state = config.load(), State.load(STATE)
            ok, reason = should_reply(text, sender, MY_USERNAME, time.time(),
                                      state, cfg, PAUSE.exists())
            log.info("msg from=%s text=%r -> %s", sender, text[:80], reason)
            if not ok:
                db.log_event(sender, text, reason)
                continue
            reply = pick_reply(cfg["replies"], state.last_text)
            await asyncio.sleep(random.uniform(1.2, 2.0))
            if cfg["dry_run"]:
                log.info("dry_run: would send %r", reply)
            else:
                box = self.textbox()
                await box.click()
                await self.page.keyboard.type(reply, delay=random.randint(60, 110))
                await self.page.keyboard.press("Enter")
                log.info("sent %r", reply)
            db.log_event(sender, text, reason, reply, cfg["dry_run"])
            # Cooldown applies to dry runs too, so the log shows real behaviour.
            State(last_reply=time.time(), last_text=reply).save(STATE)

    async def health(self) -> None:
        while True:
            await asyncio.sleep(HEALTH_EVERY + random.uniform(-30, 30))
            await self.dismiss_popups()
            await self.check_fatal()
            alive, why = await self.is_alive()
            if not alive:
                await self.reload(why, counts=True)
                await self.check_fatal()
                continue
            quiet = time.time() - await self.page.evaluate("window.__lastMutation") / 1000
            if quiet > QUIET_RELOAD and time.time() - self.last_reload > QUIET_RELOAD:
                await self.reload(f"no DOM activity for {quiet / 60:.0f} min", counts=False)

    async def watch(self) -> str:
        """Return (ending the session) when the panel disables the bot or the window ends."""
        while True:
            await asyncio.sleep(WATCH_EVERY)
            cfg = config.load()
            if not cfg["enabled"]:
                return "disabled in the panel"
            if not config.in_window(datetime.now(), cfg["start"], cfg["end"], self.end_jitter):
                return f"window {cfg['start']}-{cfg['end']} (+{self.end_jitter} min) is over"

    async def run(self) -> None:
        await self.page.expose_function("onNewMessage", self.on_message)
        await self.open_thread()
        term = asyncio.Event()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, term.set)
        watch = asyncio.create_task(self.watch())
        tasks = [asyncio.create_task(self.responder()), asyncio.create_task(self.health()),
                 asyncio.create_task(term.wait()), watch]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if term.is_set():
                log.info("SIGTERM received")
            if watch in done:
                log.info("ending session: %s", watch.result())
            for t in done:
                t.result()   # re-raise Fatal / unexpected errors
        finally:
            for t in tasks:
                t.cancel()


async def main() -> int:
    cfg = config.load()
    if reason := why_not_run(cfg):
        log.info("not starting: %s", reason)
        return 0
    if "/direct/t/" not in THREAD_URL:
        log.error("THREAD_URL is not set in .env")
        return 1

    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("profile is in use by another Chrome (phase0.sh?)")
        return 1

    log.info("starting (dry_run=%s), window %s-%s", cfg["dry_run"], cfg["start"], cfg["end"])
    if not os.environ.get("NTFY_URL"):
        log.info("NTFY_URL not set: stops are only visible in the log and data/STOPPED")
    from playwright.async_api import async_playwright   # heavy; --should-run doesn't need it

    mark_clean_exit()
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            PROFILE, channel="chrome", headless=False, no_viewport=True,
            chromium_sandbox=True,   # else Playwright adds --no-sandbox and Chrome shows a warning bar
            args=["--disable-blink-features=AutomationControlled",
                  "--window-size=1366,768", "--window-position=0,0"],
            ignore_default_args=["--enable-automation"])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await Bot(ctx, page).run()
        except Exception as e:
            reason = str(e) if isinstance(e, Fatal) else f"unexpected: {e!r}"
            log.exception("stopping: %s", reason)
            try:
                await page.screenshot(path=DATA / f"fail-{int(time.time())}.png")
            except Exception:
                pass
            STOPPED.write_text(f"{datetime.now().isoformat()} {reason}\n")
            notify("insta-bot parado", reason, priority="high", tags="warning")
            await ctx.close()
            return 1
        log.info("closing Chrome (no logout)")
        await ctx.close()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--should-run"]:     # systemd ExecCondition: 0 = start, 1 = skip
        sys.exit(1 if why_not_run(config.load()) else 0)
    sys.exit(asyncio.run(main()))
