"""Tiny wrapper around the GitHub CLI (`gh api`), so cachehit never handles tokens."""

import json
import shutil
import subprocess


class GhError(RuntimeError):
    pass


def _run(args):
    if not shutil.which("gh"):
        raise GhError("the GitHub CLI `gh` is not on PATH (needed for runs/caches; `check` works offline)")
    proc = subprocess.run(["gh"] + args, capture_output=True)
    if proc.returncode != 0:
        raise GhError("gh %s: %s" % (" ".join(args), proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout.decode("utf-8", "replace")


def api_json(path):
    return json.loads(_run(["api", path]) or "null")


def api_lines(path, jq):
    """Paginate and return one JSON value per line produced by `jq`."""
    out = _run(["api", "--paginate", path, "--jq", jq])
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def api_text(path):
    # Job logs contain ANSI colour codes; newer gh refuses to print them without this flag.
    try:
        return _run(["api", "--allow-escape-sequences", path])
    except GhError as exc:
        if "unknown flag" not in str(exc):
            raise
        return _run(["api", path])
