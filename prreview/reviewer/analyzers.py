"""Run static analyzers over an extracted tree and normalise their output.

Every tool here is a *parser*, not an interpreter: ruff, bandit and semgrep read
source into an AST and never import or execute it. That is why it is acceptable
to run them in this container alongside the GitHub token. The moment you add a
step that runs `pip install`, a build, or the project's tests, that stops being
true and the analysis has to move into an ephemeral `--network none` container.
"""

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from xml.parsers import expat

import yaml

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    tool: str
    path: str
    line: int
    code: str
    severity: str
    message: str
    extra: dict = field(default_factory=dict)

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 3)


def _run(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str, str]:
    """Analyzers exit non-zero when they find something. Never check=True here."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        log.warning("%s timed out after %ss", cmd[0], timeout)
        return -1, "", "timeout"
    except FileNotFoundError:
        log.warning("%s not installed, skipping", cmd[0])
        return -1, "", "missing"


def run_ruff(tree: Path, files: list[str]) -> list[Finding]:
    files = [f for f in files if f.endswith((".py", ".pyi"))]
    if not files:
        return []
    rc, out, err = _run(["ruff", "check", "--output-format", "json", "--force-exclude", *files], tree)
    if not out.strip():
        if rc not in (0, 1):
            log.warning("ruff: %s", err.strip()[:200])
        return []
    findings = []
    for it in json.loads(out):
        code = it.get("code") or "RUFF"
        findings.append(
            Finding(
                tool="ruff",
                path=str(Path(it["filename"]).relative_to(tree)) if Path(it["filename"]).is_absolute() else it["filename"],
                line=int(it["location"]["row"]),
                code=code,
                # ruff has no severity model; E9/F8 are real errors, the rest is style
                severity="error" if code.startswith(("E9", "F8", "F6")) else "info",
                message=it.get("message", ""),
                extra={"url": it.get("url"), "fixable": bool(it.get("fix"))},
            )
        )
    return findings


def run_bandit(tree: Path, files: list[str]) -> list[Finding]:
    py = [f for f in files if f.endswith(".py")]
    if not py:
        return []
    rc, out, err = _run(["bandit", "-f", "json", "-q", *py], tree)
    if not out.strip():
        if rc not in (0, 1):
            log.warning("bandit: %s", err.strip()[:200])
        return []
    data = json.loads(out)
    sev = {"HIGH": "error", "MEDIUM": "warning", "LOW": "info"}
    return [
        Finding(
            tool="bandit",
            path=r["filename"].removeprefix("./"),
            line=int(r["line_number"]),
            code=r.get("test_id", "B000"),
            severity=sev.get(r.get("issue_severity", "LOW").upper(), "info"),
            message=r.get("issue_text", ""),
            extra={"confidence": r.get("issue_confidence"), "url": r.get("more_info")},
        )
        for r in data.get("results", [])
    ]


def run_semgrep(tree: Path, files: list[str], config="p/default") -> list[Finding]:
    cmd = ["semgrep", "scan", "--json", "--quiet", "--metrics", "off"]
    for c in [config] if isinstance(config, str) else config:
        cmd += ["--config", c]
    rc, out, err = _run([*cmd, *files], tree, timeout=900)
    if not out.strip():
        if rc not in (0, 1):
            log.warning("semgrep: %s", err.strip()[:200])
        return []
    data = json.loads(out)
    sev = {"ERROR": "error", "WARNING": "warning", "INFO": "info"}
    return [
        Finding(
            tool="semgrep",
            path=r["path"].removeprefix("./"),
            line=int(r["start"]["line"]),
            code=r.get("check_id", "semgrep").split(".")[-1],
            severity=sev.get(r.get("extra", {}).get("severity", "INFO").upper(), "info"),
            message=r.get("extra", {}).get("message", "").strip(),
            extra={"url": (r.get("extra", {}).get("metadata") or {}).get("source")},
        )
        for r in data.get("results", [])
    ]


# ---------- structured-file syntax ----------

STRUCTURED = {".yaml", ".yml", ".xml", ".json"}


class _StrictLoader(yaml.SafeLoader):
    """PyYAML keeps the last of duplicate keys without a word. In a compose file
    that silently drops a whole service, so treat it as a parse error."""


def _no_duplicates(loader, node, deep=False):
    seen = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", k.start_mark
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def _parse_xml(text: str) -> None:
    """Well-formedness only, via expat: no tree is built and no entity is expanded.

    XML from a PR is untrusted and ElementTree expands internal entities, so a few
    hundred bytes of nested <!ENTITY> exhausts memory (bandit B314). Refusing the
    declaration outright is both safer and all this check needs.
    """
    parser = expat.ParserCreate()

    def reject(*_args):
        raise ValueError(
            f"XML entity declaration rejected at line {parser.CurrentLineNumber}"
        )

    parser.EntityDeclHandler = reject
    parser.Parse(text, True)


def _err_line(e: Exception) -> int:
    mark = getattr(e, "problem_mark", None)          # PyYAML
    if mark is not None:
        return mark.line + 1
    lineno = getattr(e, "lineno", None)              # json.JSONDecodeError, expat
    return lineno if isinstance(lineno, int) else 1


def run_syntax(tree: Path, files: list[str]) -> list[Finding]:
    """Actually parse yaml/xml/json. Semgrep skips a file it cannot parse and says
    nothing, so a malformed config sails through every other check here.
    """
    findings = []
    for rel in files:
        ext = Path(rel).suffix.lower()
        if ext not in STRUCTURED:
            continue
        try:
            text = (tree / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            if ext in (".yaml", ".yml"):
                for _ in yaml.load_all(text, _StrictLoader):  # multi-doc aware
                    pass
            elif ext == ".json":
                json.loads(text)
            else:
                _parse_xml(text)
        except Exception as e:
            findings.append(
                Finding(
                    tool="syntax",
                    path=rel,
                    line=_err_line(e),
                    code=type(e).__name__,
                    severity="error",
                    message=" ".join(str(e).split())[:300],
                )
            )
    return findings


RUNNERS = {
    "syntax": run_syntax,
    "ruff": run_ruff,
    "bandit": run_bandit,
    "semgrep": run_semgrep,
}


def analyse(tree: Path, files: list[str], enabled: dict, semgrep_config: str) -> list[Finding]:
    findings: list[Finding] = []
    for name, runner in RUNNERS.items():
        if not enabled.get(name):
            continue
        try:
            got = runner(tree, files, semgrep_config) if name == "semgrep" else runner(tree, files)
            log.info("%s: %d findings", name, len(got))
            findings.extend(got)
        except Exception:
            log.exception("%s failed", name)
    return findings


def scope_to_diff(
    findings: list[Finding], touched: dict[str, set[int]]
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into those on lines this PR touched, and the rest."""
    on_diff, elsewhere = [], []
    for f in findings:
        (on_diff if f.line in touched.get(f.path, set()) else elsewhere).append(f)
    on_diff.sort(key=lambda f: (f.rank, f.path, f.line))
    elsewhere.sort(key=lambda f: (f.rank, f.path, f.line))
    return on_diff, elsewhere
