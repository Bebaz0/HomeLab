"""Runtime settings shared by bot.py and web.py: data/config.json, edited from the panel.

The bot re-reads this on every message and every few seconds, so panel changes apply
without a restart. Missing keys fall back to DEFAULTS, so old files keep working.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DATA_DIR", HERE / "data"))
CONFIG = DATA / "config.json"

DEFAULTS = {
    "enabled": True,
    # Only a starting value: once config.json exists, the panel owns this.
    "dry_run": os.environ.get("DRY_RUN", "1") != "0",
    "start": "16:45",
    "end": "00:05",
    "cooldown_minutes": 60,
    "max_words": 12,          # for "triggers"
    "max_words_short": 5,     # for "short_triggers"
    "replies": ["bora", "eu vou", "já entro", "bora 🎮", "conta comigo", "siga", "tou dentro",
                "vou já", "bora lá", "partiu", "dá-me 5 min e entro", "já tou a abrir o lol"],
    # "*" = any words in between, "/" = alternatives. Case and accents are ignored.
    "triggers": [
        "flex", "league", "ranked/rankeds", "soloq", "solo q", "duoq", "duo q",
        "aram/arams", "clash",
        "siga/bora/vamos/vamo/bamos/partiu * jogar/joga/jogamos/game/games/partida/partidas/lol",
        "quem/alguém * jogar/joga/jogamos/game/games/partida/partidas/lol",
    ],
    # Ambiguous words (laughter vs. the game): only in short messages that aren't just laughter.
    "short_triggers": ["lol"],
}

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
LISTS = ("replies", "triggers", "short_triggers")

_cache: tuple[float, dict] | None = None


def load() -> dict:
    global _cache
    try:
        mtime = CONFIG.stat().st_mtime
    except FileNotFoundError:
        return dict(DEFAULTS)
    if _cache and _cache[0] == mtime:
        return dict(_cache[1])
    try:
        cfg = {**DEFAULTS, **json.loads(CONFIG.read_text())}
    except ValueError:
        return dict(DEFAULTS)   # half-written by hand; never crash the bot over it
    _cache = (mtime, cfg)
    return dict(cfg)


def save(cfg: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    os.replace(tmp, CONFIG)


def validate(new: dict) -> tuple[dict, list[str]]:
    """Merge a (partial) update onto the current config. Returns (config, errors)."""
    cfg, errors = load(), []
    for key, value in new.items():
        if key not in DEFAULTS:
            errors.append(f"campo desconhecido: {key}")
        elif key in ("enabled", "dry_run"):
            if not isinstance(value, bool):
                errors.append(f"{key} tem de ser true/false")
        elif key in ("start", "end"):
            if not isinstance(value, str) or not HHMM.match(value):
                errors.append(f"{key} tem de ser HH:MM")
        elif key in ("cooldown_minutes", "max_words", "max_words_short"):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1440:
                errors.append(f"{key} tem de ser um número entre 0 e 1440")
        elif key in LISTS:
            if not isinstance(value, list) or not value:
                errors.append(f"{key} não pode ficar vazio")
                continue
            items = [v.strip() if isinstance(v, str) else "" for v in value]
            if len(items) > 50:
                errors.append(f"{key}: máximo 50")
            if any(not v or len(v) > 100 for v in items):
                errors.append(f"{key}: cada item tem de ter 1 a 100 caracteres")
            if key != "replies" and any(not re.search(r"\w", v.replace("*", "")) for v in items):
                errors.append(f"{key}: cada gatilho precisa de pelo menos uma palavra")
            value = items
        cfg[key] = value
    return cfg, errors


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_window(now: datetime, start: str, end: str, end_extra: int = 0) -> bool:
    """True if now is inside [start, end + end_extra min). Handles windows past midnight."""
    t = now.hour * 60 + now.minute
    s, e = minutes(start), (minutes(end) + end_extra) % 1440
    if s == e:
        return True            # 24h
    return s <= t < e if s < e else (t >= s or t < e)
