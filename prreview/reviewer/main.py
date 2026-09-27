"""Poll GitHub for open PRs, analyse the ones whose head SHA changed, publish HTML."""

import fnmatch
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from .analyzers import analyse, scope_to_diff
from .github import GitHub, GitHubError, changed_lines, split_by_file
from .render import render_index, render_pr

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("prreview")

DATA = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG = Path(os.environ.get("CONFIG_FILE", "/config/config.yaml"))
OUT = DATA / "out"
MIRRORS = DATA / "mirrors"
DB = DATA / "state.db"


# ---------- state ----------

def db() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS reviews(
             repo TEXT NOT NULL, pr INTEGER NOT NULL, head_sha TEXT NOT NULL,
             title TEXT, findings INTEGER, reviewed_at TEXT,
             PRIMARY KEY(repo, pr, head_sha))"""
    )
    con.commit()
    return con


def already_done(con, repo: str, pr: int, sha: str) -> bool:
    return con.execute(
        "SELECT 1 FROM reviews WHERE repo=? AND pr=? AND head_sha=?", (repo, pr, sha)
    ).fetchone() is not None


def record(con, repo: str, pr: int, sha: str, title: str, findings: int) -> None:
    con.execute(
        "INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?)",
        (repo, pr, sha, title, findings, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()


def index_rows(con) -> list[tuple]:
    return con.execute(
        """SELECT repo, pr, title, findings, reviewed_at FROM reviews r
           WHERE reviewed_at = (SELECT MAX(reviewed_at) FROM reviews
                                WHERE repo=r.repo AND pr=r.pr)
           ORDER BY reviewed_at DESC LIMIT 200"""
    ).fetchall()


# ---------- notifications ----------

def notify(title: str, message: str, priority: str = "default", tags: str = "") -> None:
    url = os.environ.get("NTFY_URL")
    if not url:
        return
    try:
        requests.post(
            url,
            data=message.encode(),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            timeout=10,
        )
    except Exception:
        log.warning("ntfy notification failed", exc_info=True)


# ---------- filtering ----------

def keep(path: str, ignore: list[str]) -> bool:
    # ponytail: fnmatch has no `**`, so `**/*.lock` misses a root-level poetry.lock.
    # Testing the pattern with the prefix stripped covers both depths.
    return not any(
        fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, pat.removeprefix("**/"))
        for pat in ignore
    )


# Every language semgrep's default build parses, by extension. Each runner filters
# this list down to what it can handle: ruff/bandit to .py, syntax to yaml/xml/json,
# semgrep to the rest.
# Absent on purpose: .css (semgrep has no CSS parser) and the languages semgrep
# supports with no file extension to key off (promql, generic, regex).
ANALYSABLE = (
    # Dockerfile has no extension; endswith still matches `ops/Dockerfile`.
    "Dockerfile",
    ".py", ".pyi",                                       # python
    ".js", ".jsx", ".mjs", ".cjs",                       # javascript
    ".ts", ".tsx", ".vue",                               # typescript, vue
    ".go", ".rs", ".rb", ".php", ".java", ".scala",      # go rust ruby php java scala
    ".kt", ".kts", ".swift", ".dart",                    # kotlin swift dart
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp",    # c, c++
    ".cs",                                               # c#
    ".ex", ".exs",                                       # elixir
    ".clj", ".cljs", ".cljc", ".edn",                    # clojure
    ".lisp", ".cl", ".el", ".scm", ".ss",                # lisp, scheme
    ".ml", ".mli",                                       # ocaml
    ".lua", ".jl", ".r", ".R",                           # lua, julia, r
    ".sh", ".bash",                                      # bash
    ".ps1", ".psm1",                                     # powershell
    ".sol", ".cairo", ".circom", ".move",                # solidity, cairo, circom, move
    ".ql", ".qll",                                       # ql
    ".cls", ".trigger",                                  # apex
    ".proto",                                            # protobuf
    ".tf", ".tfvars", ".hcl",                            # terraform, hcl
    ".jsonnet", ".libsonnet",                            # jsonnet
    ".yaml", ".yml", ".xml", ".json", ".html", ".htm",   # markup and config
)


def analysable(path: str) -> bool:
    return path.endswith(ANALYSABLE)


# ---------- work ----------

def review_pr(gh: GitHub, con, cfg: dict, repo: str, pr: dict) -> None:
    number, sha = pr["number"], pr["head"]["sha"]
    base = gh.merge_base(repo, pr["base"]["sha"], sha)
    diff_text = gh.diff(repo, base, sha)

    touched = changed_lines(diff_text)
    diffs = split_by_file(diff_text)

    paths = [p for p in touched if keep(p, cfg["ignore_globs"])]
    if len(paths) > cfg["max_changed_files"]:
        log.warning("PR #%s touches %d files, capping at %d",
                    number, len(paths), cfg["max_changed_files"])
        paths = paths[: cfg["max_changed_files"]]

    targets = [p for p in paths if analysable(p)]
    diffs = {p: t for p, t in diffs.items() if p in paths}

    tree = Path(tempfile.mkdtemp(prefix="prreview-"))
    try:
        gh.extract(repo, sha, targets, tree)
        # Drop anything that came out larger than the cap: a single vendored blob
        # can dominate the run for no review value.
        cap = cfg["max_file_kb"] * 1024
        targets = [p for p in targets if (tree / p).exists() and (tree / p).stat().st_size <= cap]

        findings = analyse(tree, targets, cfg["analyzers"], cfg["semgrep_config"]) if targets else []
        on_diff, elsewhere = scope_to_diff(findings, touched)

        out_dir = OUT / repo.replace("/", "__")
        render_pr(out_dir, repo, pr, on_diff, elsewhere, diffs, tree)
    finally:
        shutil.rmtree(tree, ignore_errors=True)

    record(con, repo, number, sha, pr["title"], len(on_diff))
    render_index(OUT, index_rows(con))

    log.info("reviewed %s#%s (%s): %d on-diff findings",
             repo, number, sha[:8], len(on_diff))
    base_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    link = f"{base_url}/{repo.replace('/', '__')}/pr-{number}.html" if base_url else ""
    notify(
        f"PR #{number} \u2014 {len(on_diff)} achados",
        f"{repo}: {pr['title']}\n{link}",
        priority="default" if on_diff else "low",
        tags="mag" if on_diff else "white_check_mark",
    )


def process_repo(gh: GitHub, con, cfg: dict, entry) -> None:
    entry = {"name": entry} if isinstance(entry, str) else entry
    repo = entry["name"]
    label = entry.get("label") or ""
    pulls = gh.open_pulls(repo)
    todo = []
    for pr in pulls:
        if pr.get("draft") and not cfg["review_drafts"]:
            continue
        if label and label not in [l["name"] for l in pr.get("labels", [])]:
            continue
        if already_done(con, repo, pr["number"], pr["head"]["sha"]):
            continue
        todo.append(pr)

    if not todo:
        return
    gh.sync(repo)
    for pr in todo:
        try:
            review_pr(gh, con, cfg, repo, pr)
        except Exception as e:
            log.exception("review of %s#%s failed", repo, pr["number"])
            notify("Code review falhou", f"{repo}#{pr['number']}: {e}",
                   priority="high", tags="warning")


def name_of(entry) -> str:
    return entry if isinstance(entry, str) else entry.get("name", "?")


def load_config() -> dict:
    cfg = yaml.safe_load(CONFIG.read_text()) or {}
    cfg.setdefault("poll_interval_seconds", 300)
    cfg.setdefault("repos", [])
    cfg.setdefault("ignore_globs", [])
    cfg.setdefault("max_file_kb", 256)
    cfg.setdefault("max_changed_files", 100)
    cfg.setdefault("review_drafts", False)
    cfg.setdefault("semgrep_config", "p/default")
    cfg.setdefault("analyzers", {"syntax": True, "ruff": True, "bandit": True, "semgrep": False})
    return cfg


def main() -> int:
    # Publish the index before anything can fail, so the site serves a real page
    # from the first boot instead of a Caddy 404.
    con = db()
    OUT.mkdir(parents=True, exist_ok=True)
    render_index(OUT, index_rows(con))

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("GITHUB_TOKEN is not set")
        return 1

    gh = GitHub(token, MIRRORS)

    while True:
        try:
            cfg = load_config()  # re-read each cycle: edit config.yaml without a restart
        except Exception:
            log.exception("bad config, retrying in 60s")
            time.sleep(60)
            continue

        for entry in cfg["repos"]:
            try:
                process_repo(gh, con, cfg, entry)
            except GitHubError as e:
                log.error("%s: %s", name_of(entry), e)
                notify("Code review: erro GitHub", str(e), priority="high", tags="warning")
            except Exception:
                log.exception("unhandled error on %s", name_of(entry))

        time.sleep(cfg["poll_interval_seconds"])


if __name__ == "__main__":
    sys.exit(main())
