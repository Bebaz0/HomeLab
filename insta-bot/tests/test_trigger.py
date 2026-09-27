import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import DEFAULTS  # noqa: E402
from trigger import State, matches, pick_reply, should_reply  # noqa: E402

REPLIES = DEFAULTS["replies"]

ME = "bebaz"
NOW = 1_000_000.0


def check(text, sender="amigo", state=None, paused=False, now=NOW, cfg=None):
    return should_reply(text, sender, ME, now, state or State(), {**DEFAULTS, **(cfg or {})}, paused)


@pytest.mark.parametrize("text", [
    "siga lol", "LOL?", "bora flex", "flex", "alguém lol hoje?", "Flex!",
    "Siga jogar", "Vamos jogar ?", "bora jogar", "quem joga?", "alguém umas rankeds?",
    "alguém quer jogar umas rankeds hoje à noite?", "aram?", "bora duo q", "clash hoje",
    "partiu league", "vamo jogar lol",
])
def test_triggers(text):
    assert check(text) == (True, "reply")


@pytest.mark.parametrize("text", ["lol", "LOOOL", "kkkk lol", "lol!!", "hahaha lol"])
def test_laugh_ignored(text):
    assert check(text) == (False, "ignored:laugh")


@pytest.mark.parametrize("text", ["lolol", "lolada", "flexível", "reflex", "vamos jantar",
                                  "o jogo de ontem foi top", "bora", "siga", "jogar à bola"])
def test_no_trigger(text):
    assert check(text) == (False, "ignored:no-trigger")


@pytest.mark.parametrize("text", [
    "isto foi tão engraçado lol a sério",
    "ontem à noite perdi três rankeds seguidas e agora estou tilted demais para continuar",
])
def test_long_message_ignored(text):
    assert check(text) == (False, "ignored:long")


@pytest.mark.parametrize("sender", ["me", "bebaz", "BEBAZ"])
def test_self_ignored(sender):
    assert check("bora lol", sender=sender) == (False, "ignored:self")


def test_paused():
    assert check("bora lol", paused=True) == (False, "paused")


def test_cooldown_global_and_expires():
    st = State(last_reply=NOW - 600, last_text="bora")
    assert check("bora flex", state=st) == (False, "cooldown")
    assert check("bora flex", state=st, now=st.last_reply + 3600) == (True, "reply")


def test_cooldown_survives_restart(tmp_path):
    path = tmp_path / "state.json"
    State(last_reply=NOW - 60, last_text="eu vou").save(path)
    assert check("siga lol", state=State.load(path)) == (False, "cooldown")


def test_state_load_missing_or_corrupt(tmp_path):
    assert State.load(tmp_path / "nope.json") == State()
    (tmp_path / "bad.json").write_text("{not json")
    assert State.load(tmp_path / "bad.json") == State()


def test_pick_reply_never_repeats():
    rng = random.Random(0)
    last = ""
    for _ in range(200):
        r = pick_reply(REPLIES, last, rng)
        assert r in REPLIES and r != last
        last = r


@pytest.mark.parametrize("text,phrases,hit", [
    ("Alguem joga?", ["quem/alguém * joga"], True),          # accents ignored both ways
    ("ALGUÉM JOGA", ["quem/alguem * joga"], True),
    ("siga, jogar", ["siga * jogar"], True),                  # punctuation between words
    ("siga lá malta jogar", ["siga * jogar"], True),          # words in between
    ("jogar siga", ["siga * jogar"], False),                  # order matters
    ("duo q?", ["duo q"], True),
    ("duoqueue", ["duoq"], False),                            # whole words only
    ("bora jantar", ["bora * jogar/game"], False),
])
def test_phrase_syntax(text, phrases, hit):
    assert matches(text, phrases) is hit


def test_edited_config_is_used():
    cfg = {"triggers": ["tft"], "short_triggers": ["lol"], "cooldown_minutes": 5}
    assert check("bora tft", cfg=cfg) == (True, "reply")
    assert check("bora flex", cfg=cfg) == (False, "ignored:no-trigger")
    st = State(last_reply=NOW - 301)
    assert check("bora tft", cfg=cfg, state=st) == (True, "reply")


def test_pick_reply_single_item():
    assert pick_reply(["só esta"], "só esta") == "só esta"
