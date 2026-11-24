"""The two glob flavours this tool needs.

* hashFiles() patterns, matched against files on disk (relative to the repo root).
* Branch filters in `on.push.branches`, matched against a branch name.

Both treat `**` as "any characters including /", `*` as "anything but /" and `?`
as one character. A leading `!` negates (later patterns win).
"""

import os
import re


def _to_regex(pattern):
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                # "**/" also matches zero directories.
                if pattern[i : i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j < 0:
                out.append(re.escape(c))
            else:
                out.append(pattern[i : j + 1])
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def match_any(name, patterns):
    """Ordered include/exclude matching: the last matching pattern decides."""
    result = False
    for pat in patterns:
        neg = pat.startswith("!")
        body = pat[1:] if neg else pat
        if _to_regex(body).match(name):
            result = not neg
    return result


SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox"}


def list_files(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            files.append(rel)
    return sorted(files)


def hashfiles_matches(patterns, files):
    """Files hashFiles(patterns) would hash. Patterns are relative to the workspace."""
    cleaned = []
    for p in patterns:
        p = p.strip()
        neg = p.startswith("!")
        body = p[1:] if neg else p
        if body.startswith("./"):
            body = body[2:]
        cleaned.append(("!" if neg else "") + body)
    return [f for f in files if match_any(f, cleaned)]


HASHFILES = re.compile(r"hashFiles\(([^)]*)\)")
QUOTED = re.compile(r"'((?:[^']|'')*)'")


def hashfiles_calls(text):
    """Yield (raw_call, [patterns]) for each hashFiles(...) in an expression string."""
    for m in HASHFILES.finditer(text):
        pats = [q.replace("''", "'") for q in QUOTED.findall(m.group(1))]
        yield m.group(0), pats
