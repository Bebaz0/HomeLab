"""Runnable check for the two bits of logic that fail silently: python -m reviewer.selfcheck"""

from .analyzers import Finding, run_syntax, scope_to_diff
from .github import changed_lines, split_by_file
from .main import analysable, keep

import pathlib
import shutil
import tempfile

IGNORE = ["**/*.lock", "**/node_modules/**"]
assert keep("src/app.py", IGNORE)
assert not keep("poetry.lock", IGNORE)          # root level, no dir prefix
assert not keep("sub/poetry.lock", IGNORE)
assert not keep("node_modules/x.js", IGNORE)
assert not keep("web/node_modules/x.js", IGNORE)

assert analysable("a.py") and analysable("a.ts") and analysable("App.swift")
assert analysable("main.dart") and analysable("k8s.yaml") and analysable("pom.xml")
assert analysable("ops/Dockerfile") and analysable("main.tf") and analysable("a.sh")
assert not analysable("a.md") and not analysable("style.css")   # no semgrep parser
assert not analysable("Dockerfile.j2")

DIFF = """diff --git a/.github/w.py b/.github/w.py
index 111..222 100644
--- a/.github/w.py
+++ b/.github/w.py
@@ -1,3 +1,4 @@
 import os
+import subprocess
 x = 1
diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""
touched = changed_lines(DIFF)
assert touched == {".github/w.py": {2}}, touched   # dot-dir survives; deleted file absent
assert set(split_by_file(DIFF)) == {".github/w.py", "gone.py"}

on, elsewhere = scope_to_diff(
    [Finding("bandit", ".github/w.py", 2, "B404", "error", "subprocess"),
     Finding("bandit", ".github/w.py", 3, "B105", "info", "other")],
    touched,
)
assert [f.line for f in on] == [2] and [f.line for f in elsewhere] == [3]

# --- run_syntax: the runner that exists because semgrep skips what it cannot parse
tree = pathlib.Path(tempfile.mkdtemp())
(tree / "ok.yaml").write_text("a: 1\nb: [2, 3]\n")
(tree / "bad.yaml").write_text("a: 1\n  b: 2\n")          # bad indent
(tree / "dup.yml").write_text("services:\n  web: {}\n  web: {}\n")  # silent data loss
(tree / "bad.xml").write_text("<a><b></a>\n")
(tree / "bad.json").write_text('{"a": 1,}\n')
(tree / "ok.css").write_text("body{color:red}\n")

got = {f.path: f for f in run_syntax(tree, ["ok.yaml", "bad.yaml", "dup.yml",
                                            "bad.xml", "bad.json", "ok.css"])}
assert set(got) == {"bad.yaml", "dup.yml", "bad.xml", "bad.json"}, sorted(got)
assert all(f.severity == "error" for f in got.values())
assert "duplicate key" in got["dup.yml"].message, got["dup.yml"].message
assert got["dup.yml"].line == 3, got["dup.yml"].line   # points at the second `web`
assert got["bad.json"].line == 1 and got["bad.xml"].line >= 1

# billion-laughs: must be refused, not expanded (bandit B314, found by this tool
# reviewing its own first PR)
(tree / "ok.xml").write_text("<a><b/></a>\n")
(tree / "bomb.xml").write_text(
    '<?xml version="1.0"?>\n<!DOCTYPE l [<!ENTITY a "xx"><!ENTITY b "&a;&a;&a;">]>\n'
    "<l>&b;</l>\n"
)
bombed = {f.path for f in run_syntax(tree, ["ok.xml", "bomb.xml"])}
assert bombed == {"bomb.xml"}, bombed
shutil.rmtree(tree, ignore_errors=True)

print("selfcheck ok")
