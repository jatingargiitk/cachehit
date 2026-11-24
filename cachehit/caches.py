"""Summarise a repository's Actions cache entries (GET /repos/{o}/{r}/actions/caches)."""

import re
from datetime import datetime

DEFAULT_LIMIT = 10 * 1024**3
HEXISH = re.compile(r"[0-9a-f]{7,}|\d{6,}", re.I)


def family(key):
    return HEXISH.sub("*", key)


def _ts(value):
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def summarize(entries):
    total = sum(e.get("size_in_bytes", 0) for e in entries)
    refs = {}
    fams = {}
    never = []
    for e in entries:
        ref = e.get("ref", "?")
        r = refs.setdefault(ref, [0, 0])
        r[0] += 1
        r[1] += e.get("size_in_bytes", 0)
        f = fams.setdefault(family(e.get("key", "")), [0, 0, set()])
        f[0] += 1
        f[1] += e.get("size_in_bytes", 0)
        f[2].add(ref)
        try:
            if (_ts(e["last_accessed_at"]) - _ts(e["created_at"])).total_seconds() <= 60:
                never.append(e)
        except (KeyError, ValueError):
            pass
    return {"count": len(entries), "total": total, "refs": refs, "families": fams, "never": never}


def render(repo, entries, limit=DEFAULT_LIMIT, top=10):
    s = summarize(entries)
    lines = []
    lines.append(
        "%s: %d cache entries, %s of %s (%.0f%%; 10 GB is the default limit, --limit-gb if yours is raised)"
        % (repo, s["count"], human(s["total"]), human(limit), 100.0 * s["total"] / limit if limit else 0)
    )
    if s["total"] > 0.9 * limit:
        lines.append(
            "  near the limit: GitHub evicts entries once the repo is over it, so new saves push out "
            "caches other jobs still need (thrashing)"
        )
    lines.append("")
    lines.append("by ref:")
    pr_count = 0
    for ref, (n, size) in sorted(s["refs"].items(), key=lambda kv: (-kv[1][1], kv[0]))[:top]:
        note = ""
        if ref.startswith("refs/pull/"):
            note = "  only re-runs of PR #%s can restore these" % ref.split("/")[2]
        lines.append("  %-28s %4d  %9s%s" % (ref, n, human(size), note))
    for ref, (n, size) in s["refs"].items():
        if ref.startswith("refs/pull/"):
            pr_count += n
    if len(s["refs"]) > top:
        lines.append("  ... %d more refs" % (len(s["refs"]) - top))
    if pr_count:
        pr_size = sum(sz for ref, (n, sz) in s["refs"].items() if ref.startswith("refs/pull/"))
        lines.append(
            "  %d of %d entries (%s) are PR-scoped; other PRs and %s can't use them"
            % (pr_count, s["count"], human(pr_size), "the default branch")
        )
    lines.append("")
    churn = [(f, v) for f, v in s["families"].items() if v[0] >= 3]
    if churn:
        lines.append("key families with 3+ entries (many entries of one family usually means a key that changes too often):")
        for f, (n, size, refs) in sorted(churn, key=lambda kv: (-kv[1][0], kv[0]))[:top]:
            lines.append("  %-60s %4d  %9s  on %d ref%s" % (f[:60], n, human(size), len(refs), "" if len(refs) == 1 else "s"))
        lines.append("")
    lines.append(
        "never restored since saved: %d of %d entries (last_accessed_at within 60s of created_at)"
        % (len(s["never"]), s["count"])
    )
    for e in sorted(s["never"], key=lambda e: -e.get("size_in_bytes", 0))[:top]:
        lines.append("  %-60s %9s  %s" % (e.get("key", "")[:60], human(e.get("size_in_bytes", 0)), e.get("ref", "")))
    return "\n".join(lines)
