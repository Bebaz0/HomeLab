"""GitHub API access, git mirror management and unified-diff parsing."""

import base64
import logging
import os
import re
import subprocess
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str, mirror_root: Path):
        self.token = token
        self.mirror_root = Path(mirror_root)
        self.mirror_root.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "homelab-prreview",
            }
        )

    # ---------- REST ----------

    def _get(self, path: str, **params):
        url = path if path.startswith("http") else f"{API}{path}"
        r = self.s.get(url, params=params or None, timeout=30)
        if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
            reset = r.headers.get("X-RateLimit-Reset", "?")
            raise GitHubError(f"rate limit exhausted, resets at epoch {reset}")
        if not r.ok:
            raise GitHubError(f"{r.status_code} {r.request.method} {url}: {r.text[:300]}")
        return r

    def open_pulls(self, repo: str) -> list[dict]:
        """All open PRs for owner/name, following pagination."""
        out, url = [], f"/repos/{repo}/pulls"
        params = {"state": "open", "per_page": 100, "sort": "updated", "direction": "desc"}
        while url:
            r = self._get(url, **params)
            out.extend(r.json())
            url = r.links.get("next", {}).get("url")
            params = {}
        return out

    def rate_remaining(self) -> int:
        try:
            return int(self._get("/rate_limit").json()["resources"]["core"]["remaining"])
        except Exception:
            return -1

    # ---------- git ----------

    def _git_env(self) -> dict:
        """Pass the token via GIT_CONFIG_* rather than argv or a stored remote URL.

        Anything in argv is visible in `ps` to every user on the host, and a token
        baked into the remote URL ends up in .git/config and the reflog.

        Basic, not Bearer: github.com's git endpoint ignores a Bearer header and
        falls through to prompting for a username, so the clone just hangs or
        dies. The REST API accepts either, which is what makes this confusing.
        """
        basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        return {
            **os.environ,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/true",
        }

    def _git(self, *args, cwd=None, timeout=600) -> str:
        p = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=self._git_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if p.returncode != 0:
            raise GitHubError(f"git {' '.join(args[:3])} failed: {p.stderr.strip()[:400]}")
        return p.stdout

    def mirror(self, repo: str) -> Path:
        return self.mirror_root / (repo.replace("/", "__") + ".git")

    def sync(self, repo: str) -> Path:
        """Clone the bare mirror if missing, then fetch branches and PR heads."""
        path = self.mirror(repo)
        if not path.exists():
            log.info("cloning mirror for %s", repo)
            self._git("clone", "--bare", f"https://github.com/{repo}.git", str(path))
        self._git(
            "-C", str(path), "fetch", "--prune", "--quiet", "origin",
            "+refs/heads/*:refs/heads/*",
            "+refs/pull/*/head:refs/pull/*/head",
        )
        return path

    def merge_base(self, repo: str, a: str, b: str) -> str:
        return self._git("-C", str(self.mirror(repo)), "merge-base", a, b).strip()

    def diff(self, repo: str, base: str, head: str) -> str:
        return self._git(
            "-C", str(self.mirror(repo)), "diff", "--no-color", "--unified=3", base, head
        )

    def extract(self, repo: str, sha: str, paths: list[str], dest: Path) -> None:
        """Materialise only the given paths at `sha` into dest, via git archive."""
        dest.mkdir(parents=True, exist_ok=True)
        if not paths:
            return
        git = subprocess.Popen(
            ["git", "-C", str(self.mirror(repo)), "archive", sha, "--", *paths],
            env=self._git_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tar = subprocess.Popen(["tar", "-x", "-C", str(dest)], stdin=git.stdout)
        git.stdout.close()
        tar.communicate(timeout=300)
        git.wait(timeout=10)
        # Without this a bad pathspec yields an empty tree and a silently empty review.
        if git.returncode != 0:
            err = git.stderr.read().decode(errors="replace").strip()[:400]
            raise GitHubError(f"git archive {sha[:8]} failed: {err}")
        if tar.returncode != 0:
            raise GitHubError(f"tar -x failed with rc={tar.returncode}")


# ---------- diff parsing ----------

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Map each file in the diff to the set of line numbers it adds or modifies.

    This is what turns a lint dump into a review: findings on lines the PR did
    not touch are pre-existing debt, not something to raise here.
    """
    files: dict[str, set[int]] = {}
    cur, new_line = None, 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            cur = None
        elif line.startswith("+++ "):
            p = line[4:].strip()
            cur = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
            if cur:
                files.setdefault(cur, set())
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            if m:
                new_line = int(m.group(1))
        elif cur is not None:
            if line.startswith("+"):
                files[cur].add(new_line)
                new_line += 1
            elif line.startswith("-") or line.startswith("\\"):
                pass
            else:
                new_line += 1
    return files


def split_by_file(diff_text: str) -> dict[str, str]:
    """Split a combined diff into one chunk of text per file path."""
    out, cur, buf = {}, None, []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if cur:
                out[cur] = "".join(buf)
            buf, cur = [line], None
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line.strip())
            if m:
                cur = m.group(2)
        else:
            buf.append(line)
    if cur:
        out[cur] = "".join(buf)
    return out
