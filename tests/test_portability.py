"""Portability checks (SPEC §0, §11) by AST:

- every text-mode open()/Path.open()/read_text()/write_text() passes encoding=
- every import outside stdlib + {flask, werkzeug, jinja2, numpy} (+ this repo's own modules)
  sits inside a try/except (optional dependencies are lazy and guarded)
- both Flask apps register the text/javascript and text/css mimetypes
- every .py file is valid Python 3.11 syntax (compileall with a 3.11 interpreter if one is found)
"""
import ast
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["darkshelter", "scripts", "eval", "tests"]
ALLOWED_THIRD_PARTY = {"flask", "werkzeug", "jinja2", "numpy"}
NOT_FILE_OPENERS = {"webbrowser", "wave", "tarfile", "zipfile", "gzip", "bz2", "lzma", "sqlite3", "os", "urllib",
                    "request", "dbm", "shelve", "codecs"}


def py_files():
    files = [ROOT / "run_demo.py"]
    for d in SCAN_DIRS:
        for p in sorted((ROOT / d).rglob("*.py")):
            if ".venv" in p.parts or "__pycache__" in p.parts:
                continue
            files.append(p)
    return files


def local_module_names():
    names = {"darkshelter", "run_demo"}
    for d in ("scripts", "eval", "tests"):
        names.add(d)
        for p in (ROOT / d).rglob("*.py"):
            names.add(p.stem)
    return names


def stdlib_names():
    names = set(getattr(sys, "stdlib_module_names", ()))
    names |= set(sys.builtin_module_names)
    names |= {"__future__"}
    return names


def _mode_of(call):
    """Mode argument of open(...) if it is a string literal, else None (default 'r')."""
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
        return call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _attr_mode_of(call):
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def has_encoding(call):
    return any(kw.arg == "encoding" for kw in call.keywords) or any(kw.arg is None for kw in call.keywords)


def text_io_violations(tree):
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id == "open":
            mode = _mode_of(node)
            if (mode is None or "b" not in mode) and not has_encoding(node):
                out.append((node.lineno, "open() without encoding="))
        elif isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            if not has_encoding(node):
                out.append((node.lineno, f"{f.attr}() without encoding="))
        elif isinstance(f, ast.Attribute) and f.attr == "open":
            base = f.value
            base_name = base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else "")
            if base_name in NOT_FILE_OPENERS or base_name.lower().endswith("opener"):
                continue
            if base_name == "io":
                mode = _mode_of(node)
            else:
                mode = _attr_mode_of(node)
            if (mode is None or "b" not in mode) and not has_encoding(node):
                out.append((node.lineno, f"{base_name}.open() without encoding="))
    return out


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self, allowed):
        self.allowed = allowed
        self.try_depth = 0
        self.bad = []

    def visit_Try(self, node):
        self.try_depth += 1
        for n in node.body:
            self.visit(n)
        self.try_depth -= 1
        for part in node.handlers + node.orelse + node.finalbody:
            self.visit(part)

    visit_TryStar = visit_Try

    def _check(self, node, name):
        top = name.split(".")[0]
        if top in self.allowed or self.try_depth > 0:
            return
        self.bad.append((node.lineno, name))

    def visit_Import(self, node):
        for a in node.names:
            self._check(node, a.name)

    def visit_ImportFrom(self, node):
        if node.level and node.level > 0:
            return
        self._check(node, node.module or "")


class TestPortability(unittest.TestCase):
    def test_text_io_has_encoding(self):
        problems = []
        for p in py_files():
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for line, msg in text_io_violations(tree):
                problems.append(f"{p.relative_to(ROOT)}:{line}: {msg}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_imports_are_stdlib_or_guarded(self):
        allowed = stdlib_names() | ALLOWED_THIRD_PARTY | local_module_names()
        problems = []
        for p in py_files():
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            v = _ImportVisitor(allowed)
            v.visit(tree)
            for line, name in v.bad:
                problems.append(f"{p.relative_to(ROOT)}:{line}: unguarded import {name}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_checker_catches_violations(self):
        bad = ast.parse("open('x')\nopen('x', 'w')\nfrom pathlib import Path\nPath('a').read_text()\n"
                        "Path('a').open('w')\n")
        self.assertEqual(len(text_io_violations(bad)), 4)
        good = ast.parse("open('x', 'rb')\nopen('x', encoding='utf-8')\nPath('a').read_text(encoding='utf-8')\n"
                         "webbrowser.open('http://127.0.0.1')\n")
        self.assertEqual(text_io_violations(good), [])
        v = _ImportVisitor(stdlib_names())
        v.visit(ast.parse("import psutil\ntry:\n    import opencc\nexcept Exception:\n    pass\n"))
        self.assertEqual([n for _, n in v.bad], ["psutil"])

    def test_flask_apps_fix_mimetypes(self):
        for name in ("desk_app.py", "eoc_app.py"):
            src = (ROOT / "darkshelter" / name).read_text(encoding="utf-8")
            self.assertIn('mimetypes.add_type("text/javascript", ".js")', src, name)
            self.assertIn('mimetypes.add_type("text/css", ".css")', src, name)
            self.assertLess(src.index("mimetypes.add_type"), src.index("Flask(__name__"), name)

    def test_everything_binds_localhost(self):
        for p in py_files():
            if p.name == "test_portability.py":
                continue
            src = p.read_text(encoding="utf-8")
            self.assertNotIn('"0.0.0.0"', src, str(p))

    def test_python311_syntax(self):
        """Compile with Python 3.11 when available (DS_PY311 env, python3.11 on PATH, or conda env)."""
        candidates = [os.environ.get("DS_PY311"), shutil.which("python3.11"),
                      str(Path.home() / "anaconda3" / "envs" / "coco311" / "python.exe")]
        exe = next((c for c in candidates if c and Path(c).exists()), None)
        if exe is None:
            if sys.version_info[:2] == (3, 11):
                exe = sys.executable
            else:
                self.skipTest("no Python 3.11 interpreter found (set DS_PY311)")
        out = subprocess.run([exe, "-c", "import sys; print(sys.version_info[:2])"], capture_output=True, text=True,
                             encoding="utf-8")
        self.assertIn("(3, 11)", out.stdout)
        r = subprocess.run([exe, "-m", "compileall", "-q", "darkshelter", "scripts", "eval", "tests", "run_demo.py"],
                           cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
