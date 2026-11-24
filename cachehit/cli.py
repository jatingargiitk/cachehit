"""cachehit command line."""

import argparse
import json
import os
import subprocess
import sys

from . import __version__, caches, globs, logs, rules
from .gh import GhError, api_json, api_lines, api_text
from .workflows import load_repo

SEV_LABEL = {"error": "ERROR", "warn": "WARN ", "info": "INFO "}


def detect_default_branch(root):
    try:
        out = subprocess.run(
            ["git", "-C", root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
        )
        if out.returncode == 0 and "/" in out.stdout:
            return out.stdout.strip().split("/", 1)[1]
    except OSError:
        pass
    return "main"


def cmd_check(args):
    root = args.path
    workflows = load_repo(root)
    if not workflows:
        print("cachehit: no workflows in %s" % os.path.join(root, ".github", "workflows"), file=sys.stderr)
        return 2
    branch = args.default_branch or detect_default_branch(root)
    files = None if args.no_files else globs.list_files(root)
    findings = rules.check(workflows, files, default_branch=branch)
    if not args.info:
        findings = [f for f in findings if f.severity != "info"]
    nsteps = sum(len(j.steps) for w in workflows for j in w.jobs)
    errors = sum(1 for f in findings if f.severity == "error")
    warns = sum(1 for f in findings if f.severity == "warn")
    if args.json:
        print(
            json.dumps(
                {
                    "default_branch": branch,
                    "workflows": len(workflows),
                    "cache_steps": nsteps,
                    "findings": [f.as_dict() for f in findings],
                },
                indent=2,
            )
        )
    else:
        print(
            "cachehit: %d workflow%s, %d cache step%s, default branch %s"
            % (len(workflows), "" if len(workflows) == 1 else "s", nsteps, "" if nsteps == 1 else "s", branch)
        )
        for w in workflows:
            if w.error:
                print("  skipped %s: %s" % (os.path.relpath(w.path, root), w.error))
        for f in findings:
            where = "%s:%d" % (os.path.relpath(f.workflow, root), f.line)
            print("")
            print("%s %s  %s  job %s, step %s" % (SEV_LABEL[f.severity], f.code, where, f.job, f.step))
            print("      %s" % f.message)
            for d in f.details:
                print("      %s" % d)
            if f.fix:
                print("      fix: %s" % f.fix)
        print("")
        print("%d error%s, %d warning%s" % (errors, "" if errors == 1 else "s", warns, "" if warns == 1 else "s"))
    if errors or (args.fail_on == "warn" and warns):
        return 1
    return 0


def render_tally(t, title):
    lines = [title, ""]
    if not t.rows:
        lines.append("no cache restores found in these logs")
    else:
        width = max(len(" / ".join(k)) for k in t.rows)
        width = min(max(width, 20), 70)
        cols = ["exact", "partial", "hit", "miss", "failed", "blocked"]
        used = [c for c in cols if any(r[c] for r in t.rows.values())]
        lines.append(("%-" + str(width) + "s  %5s  ") % ("step", "jobs") + "  ".join("%7s" % c for c in used) + "  hit-rate")
        for key, r in sorted(t.rows.items()):
            n = sum(r.values())
            hits = r["exact"] + r["partial"] + r["hit"]
            label = " / ".join(key)
            if len(label) > width:
                label = label[: width - 3] + "..."
            lines.append(
                ("%-" + str(width) + "s  %5d  ") % (label, n)
                + "  ".join("%7d" % r[c] for c in used)
                + "  %7.0f%%" % (100.0 * hits / n if n else 0)
            )
    for key, reasons in sorted(t.reasons.items()):
        lines.append("")
        lines.append("%s:" % " / ".join(key))
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append("  %dx %s" % (n, why))
    if t.saves:
        lines.append("")
        lines.append("saves:")
        for (wf, status, reason), n in sorted(t.saves.items()):
            lines.append("  %-30s %-10s %4d%s" % (wf[:30], status, n, ("  " + reason) if reason else ""))
    if t.modes:
        lines.append("")
        lines.append("cache-mode reported by the runner: " + ", ".join("%s x%d" % kv for kv in sorted(t.modes.items())))
    return "\n".join(lines)


def cmd_logs(args):
    t = logs.Tally()
    for path in args.files:
        text = open(path, encoding="utf-8", errors="replace").read()
        t.add(args.workflow, os.path.basename(path), text)
    print(render_tally(t, "cachehit: %d job log%s" % (t.jobs, "" if t.jobs == 1 else "s")))
    return 0


def cmd_runs(args):
    repo = args.repo
    q = "per_page=%d&status=completed" % min(args.limit, 100)
    if args.branch:
        q += "&branch=%s" % args.branch
    if args.event:
        q += "&event=%s" % args.event
    base = "repos/%s/actions/workflows/%s/runs" % (repo, args.workflow) if args.workflow else "repos/%s/actions/runs" % repo
    try:
        runs = (api_json("%s?%s" % (base, q)) or {}).get("workflow_runs", [])[: args.limit]
        t = logs.Tally()
        for run in runs:
            jobs = api_json("repos/%s/actions/runs/%d/jobs?per_page=100" % (repo, run["id"])).get("jobs", [])
            for job in jobs:
                if job.get("conclusion") in ("skipped", None) or not job.get("steps"):
                    continue
                try:
                    text = api_text("repos/%s/actions/jobs/%d/logs" % (repo, job["id"]))
                except GhError:
                    continue  # logs expired or not accessible
                t.add(run.get("name") or "?", job.get("name") or "?", text)
                if args.save_logs:
                    os.makedirs(args.save_logs, exist_ok=True)
                    with open(os.path.join(args.save_logs, "%d-%d.txt" % (run["id"], job["id"])), "w", encoding="utf-8") as fh:
                        fh.write(text)
    except GhError as exc:
        print("cachehit: %s" % exc, file=sys.stderr)
        return 2
    title = "cachehit: %s, last %d completed run%s%s, %d job logs read" % (
        repo,
        len(runs),
        "" if len(runs) == 1 else "s",
        (" of " + args.workflow) if args.workflow else "",
        t.jobs,
    )
    print(render_tally(t, title))
    return 0


def cmd_caches(args):
    if args.from_file:
        data = json.load(open(args.from_file, encoding="utf-8"))
        entries = data.get("actions_caches", data) if isinstance(data, dict) else data
        repo = args.repo or os.path.basename(args.from_file)
    else:
        if not args.repo:
            print("cachehit caches: give OWNER/REPO or --from FILE", file=sys.stderr)
            return 2
        repo = args.repo
        try:
            entries = api_lines("repos/%s/actions/caches?per_page=100" % repo, ".actions_caches[]")
        except GhError as exc:
            print("cachehit: %s" % exc, file=sys.stderr)
            return 2
    limit = int(args.limit_gb * 1024**3)
    print(caches.render(repo, entries, limit=limit, top=args.top))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="cachehit",
        description="Why your GitHub Actions caches miss, and whether they can save at all.",
    )
    p.add_argument("--version", action="version", version="cachehit %s" % __version__)
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="static check of .github/workflows (offline)")
    c.add_argument("path", nargs="?", default=".", help="repo root (default: .)")
    c.add_argument("--default-branch", help="default branch (default: origin/HEAD, else main)")
    c.add_argument("--no-files", action="store_true", help="don't look at files on disk (skips hashFiles checks)")
    c.add_argument("--info", action="store_true", help="also show info-level notes")
    c.add_argument("--json", action="store_true", help="JSON output")
    c.add_argument("--fail-on", choices=["error", "warn"], default="error", help="exit 1 on (default: error)")
    c.set_defaults(func=cmd_check)

    l = sub.add_parser("logs", help="tally cache outcomes in saved job log files")
    l.add_argument("files", nargs="+")
    l.add_argument("--workflow", default="logs", help="label for the rows (default: logs)")
    l.set_defaults(func=cmd_logs)

    r = sub.add_parser("runs", help="tally cache outcomes over recent runs (uses `gh api`)")
    r.add_argument("repo", help="OWNER/REPO")
    r.add_argument("--workflow", help="workflow file name or id, e.g. ci.yml")
    r.add_argument("--branch")
    r.add_argument("--event", help="e.g. push, pull_request")
    r.add_argument("--limit", type=int, default=5, help="runs to read (default 5)")
    r.add_argument("--save-logs", metavar="DIR", help="also write each job log to DIR (re-read later with `cachehit logs`)")
    r.set_defaults(func=cmd_runs)

    k = sub.add_parser("caches", help="summarise the repo's cache entries (uses `gh api`)")
    k.add_argument("repo", nargs="?", help="OWNER/REPO")
    k.add_argument("--from", dest="from_file", help="read a saved `GET /actions/caches` JSON instead")
    k.add_argument("--limit-gb", type=float, default=10.0, help="repo cache limit in GB (default 10)")
    k.add_argument("--top", type=int, default=10)
    k.set_defaults(func=cmd_caches)
    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 2
    return args.func(args)
