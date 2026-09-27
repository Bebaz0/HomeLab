"""Render review pages as self-contained static HTML."""

import html
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

CSS = """
:root{--bg:#fbfaf9;--fg:#1a1a19;--muted:#6b6a67;--line:#e3e1dd;--card:#fff;
--err:#b4291f;--warn:#a86b0a;--info:#5a6b7c;--accent:#1c5d99;--code:#f4f2ef}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--fg:#e8e6e3;--muted:#94918b;
--line:#2e2d2b;--card:#1e1e1c;--err:#e5726a;--warn:#d9a441;--info:#8fa3b5;
--accent:#7ab3e8;--code:#232321}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem;background:var(--bg);color:var(--fg);
font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:56rem;margin:0 auto}
a{color:var(--accent)}
h1{font-size:1.35rem;margin:0 0 .25rem}
h2{font-size:1rem;margin:2rem 0 .5rem;font-weight:600}
.meta{color:var(--muted);font-size:.85rem;margin-bottom:1.5rem}
.counts{display:flex;gap:.5rem;flex-wrap:wrap;margin:1rem 0}
.pill{border:1px solid var(--line);border-radius:999px;padding:.15rem .6rem;
font-size:.8rem;color:var(--muted)}
.f{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--info);
border-radius:6px;padding:.75rem .9rem;margin:.6rem 0}
.f.error{border-left-color:var(--err)}
.f.warning{border-left-color:var(--warn)}
.f-head{display:flex;gap:.5rem;flex-wrap:wrap;align-items:baseline;font-size:.82rem;
color:var(--muted);margin-bottom:.35rem}
.f-head .loc{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--fg)}
.f-head .code{font-family:ui-monospace,monospace}
.f-msg{margin:0 0 .5rem}
pre{background:var(--code);border-radius:5px;padding:.6rem .75rem;overflow-x:auto;
margin:.4rem 0 0;font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
pre .hl{display:block;background:color-mix(in srgb,var(--warn) 18%,transparent);
margin:0 -.75rem;padding:0 .75rem}
details{margin:.75rem 0}
summary{cursor:pointer;color:var(--muted);font-size:.88rem}
table{width:100%;border-collapse:collapse;font-size:.9rem}
td,th{text-align:left;padding:.5rem .4rem;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500;font-size:.8rem}
.empty{color:var(--muted);padding:2rem 0}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _snippet(tree: Path, path: str, line: int, context: int = 2) -> str:
    try:
        lines = (tree / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    lo, hi = max(1, line - context), min(len(lines), line + context)
    out = []
    for n in range(lo, hi + 1):
        text = html.escape(lines[n - 1])
        marker = f"{n:>5} \u2502 {text}"
        out.append(f"<span class=\"hl\">{marker}</span>" if n == line else marker)
    return "<pre>" + "\n".join(out) + "</pre>"


def _finding_html(f, tree: Path, repo: str, sha: str) -> str:
    link = f"https://github.com/{repo}/blob/{sha}/{f.path}#L{f.line}"
    url = (f.extra or {}).get("url")
    ref = f' \u00b7 <a href="{html.escape(url)}" target="_blank" rel="noopener">docs</a>' if url else ""
    return (
        f'<div class="f {html.escape(f.severity)}">'
        f'<div class="f-head"><span class="loc">'
        f'<a href="{html.escape(link)}" target="_blank" rel="noopener">'
        f"{html.escape(f.path)}:{f.line}</a></span>"
        f'<span class="code">{html.escape(f.tool)} {html.escape(f.code)}</span>'
        f"<span>{html.escape(f.severity)}</span>{ref}</div>"
        f'<p class="f-msg">{html.escape(f.message)}</p>'
        f"{_snippet(tree, f.path, f.line)}</div>"
    )


def render_pr(out_dir: Path, repo: str, pr: dict, on_diff, elsewhere, diffs: dict, tree: Path) -> Path:
    sha = pr["head"]["sha"]
    counts = {s: sum(1 for f in on_diff if f.severity == s) for s in ("error", "warning", "info")}
    body = [
        f'<h1>#{pr["number"]} \u2014 {html.escape(pr["title"])}</h1>',
        f'<div class="meta">{html.escape(repo)} \u00b7 '
        f'{html.escape(pr["user"]["login"])} \u00b7 '
        f'<code>{sha[:8]}</code> \u00b7 '
        f'<a href="{html.escape(pr["html_url"])}" target="_blank" rel="noopener">abrir no GitHub</a>'
        f" \u00b7 revisto {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>",
        '<div class="counts">'
        + "".join(f'<span class="pill">{v} {k}</span>' for k, v in counts.items() if v)
        + f'<span class="pill">{len(diffs)} ficheiros</span>'
        + (f'<span class="pill">{len(elsewhere)} fora do diff</span>' if elsewhere else "")
        + "</div>",
    ]

    if on_diff:
        body.append("<h2>Nas linhas alteradas</h2>")
        body += [_finding_html(f, tree, repo, sha) for f in on_diff]
    else:
        body.append('<p class="empty">Nenhum achado nas linhas alteradas.</p>')

    if elsewhere:
        body.append(
            f"<details><summary>{len(elsewhere)} achados noutras linhas dos mesmos "
            "ficheiros (d\u00edvida pr\u00e9-existente)</summary>"
            + "".join(_finding_html(f, tree, repo, sha) for f in elsewhere[:80])
            + "</details>"
        )

    body.append("<h2>Diff</h2>")
    for path, text in diffs.items():
        body.append(
            f"<details><summary>{html.escape(path)}</summary>"
            f"<pre>{html.escape(text)}</pre></details>"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"pr-{pr['number']}.html"
    dest.write_text(_page(f"#{pr['number']} {pr['title']}", "".join(body)), encoding="utf-8")
    return dest


def render_index(out_root: Path, rows: list[tuple]) -> Path:
    if rows:
        table = [
            "<table><tr><th>PR</th><th>Repo</th><th>Achados</th><th>Revisto</th></tr>"
        ]
        for repo, number, title, findings, reviewed_at in rows:
            slug = repo.replace("/", "__")
            table.append(
                f'<tr><td><a href="{slug}/pr-{number}.html">#{number} '
                f"{html.escape(title[:70])}</a></td>"
                f"<td>{html.escape(repo)}</td><td>{findings}</td>"
                f"<td>{html.escape(reviewed_at[:16])}</td></tr>"
            )
        table.append("</table>")
        body = "<h1>Code review</h1>" + "".join(table)
    else:
        body = '<h1>Code review</h1><p class="empty">Ainda sem reviews.</p>'
    out_root.mkdir(parents=True, exist_ok=True)
    dest = out_root / "index.html"
    dest.write_text(_page("Code review", body), encoding="utf-8")
    return dest
