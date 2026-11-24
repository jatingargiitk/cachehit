import os
import textwrap

from cachehit import rules
from cachehit.workflows import load_repo


def make_repo(tmp_path, workflows, files=()):
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    for name, text in workflows.items():
        (wf_dir / name).write_text(textwrap.dedent(text))
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
    return str(tmp_path)


def run_check(tmp_path, workflows, files=(), default_branch="main"):
    from cachehit import globs

    root = make_repo(tmp_path, workflows, files)
    return rules.check(load_repo(root), globs.list_files(root), default_branch=default_branch)


def codes(findings):
    return sorted((f.code, f.severity) for f in findings)
