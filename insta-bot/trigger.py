"""Pure decision logic: no browser, no clock, no globals. Everything here is unit-tested."""

import json
import os
import random
import re
import unicodedata
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

# A message made only of laughter ("lol", "LOOOL", "kkkk lol", "hahaha") is not an invite.
# "LOL?" is not matched on purpose: the question mark makes it an invite.
LAUGH = re.compile(r"^\s*((l+o+l+|k{2,}|a?(ha){2,}|rs+)[\s.!]*)+$", re.I)


@dataclass
class State:
    last_reply: float = 0.0
    last_text: str = ""

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            return cls(**json.loads(path.read_text()))
        except (FileNotFoundError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self)))
        os.replace(tmp, path)


def normalize(text: str) -> str:
    """Lowercase and strip accents, so "Alguém" matches "alguem"."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@lru_cache(maxsize=256)
def compile_phrase(phrase: str) -> re.Pattern:
    """ "siga/bora * jogar" -> siga or bora, then any words, then jogar (whole words)."""
    parts, pending_gap = [], False
    for token in normalize(phrase).split():
        if token == "*":
            pending_gap = True
            continue
        alts = "|".join(re.escape(a) for a in token.split("/") if a)
        if parts:
            parts.append(r"(?:\W+\w+)*?\W+" if pending_gap else r"\W+")
        parts.append(f"(?:{alts})")
        pending_gap = False
    return re.compile(r"\b" + "".join(parts) + r"\b")


def matches(text: str, phrases: list[str]) -> bool:
    norm = normalize(text)
    return any(compile_phrase(p).search(norm) for p in phrases)


def should_reply(text: str, sender: str, me: str, now: float, state: State, cfg: dict,
                 paused: bool = False) -> tuple[bool, str]:
    """Return (reply?, reason). Reasons are what ends up in the log and the panel."""
    if sender.lower() in ("me", me.lower()):
        return False, "ignored:self"
    if LAUGH.match(text):
        return False, "ignored:laugh"
    words = len(text.split())
    game = matches(text, cfg["triggers"])
    short = matches(text, cfg["short_triggers"])
    if not (game or short):
        return False, "ignored:no-trigger"
    if not ((game and words <= cfg["max_words"]) or (short and words <= cfg["max_words_short"])):
        return False, "ignored:long"
    if paused:
        return False, "paused"
    if now - state.last_reply < cfg["cooldown_minutes"] * 60:
        return False, "cooldown"
    return True, "reply"


def pick_reply(replies: list[str], last: str, rng: random.Random = random._inst) -> str:
    return rng.choice([r for r in replies if r != last] or replies)
