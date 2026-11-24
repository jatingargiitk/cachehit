"""Static checks: cache steps that can't hit, can't save, or can't restore."""

import re

from . import globs

TRUSTED = {
    "push",
    "workflow_dispatch",
    "repository_dispatch",
    "delete",
    "registry_package",
    "page_build",
    "schedule",
}
PR_EVENTS = {"pull_request", "pull_request_review", "pull_request_review_comment"}
# Events whose runs resolve to the default branch but are not on GitHub's list of
# triggers allowed to write default-branch caches: they get read-only cache access.
LOW_TRUST = {
    "pull_request_target",
    "issue_comment",
    "issues",
    "workflow_run",
    "discussion",
    "discussion_comment",
    "fork",
    "gollum",
    "label",
    "milestone",
    "watch",
    "status",
    "check_run",
    "check_suite",
    "branch_protection_rule",
    "public",
    "project",
    "project_card",
    "project_column",
}

VOLATILE = re.compile(
    r"\b(github\.run_id|github\.run_number|github\.run_attempt|github\.sha|"
    r"github\.event\.after|github\.event\.head_commit\.id|github\.event\.pull_request\.head\.sha)\b"
)
OS_REFS = re.compile(r"\b(runner\.os|runner\.arch|runner\.name)\b")

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


class Finding:
    def __init__(self, code, severity, wf, job, step, message, fix=None, details=None):
        self.code = code
        self.severity = severity
        self.workflow = wf.path
        self.line = step.line if step else 0
        self.job = job.id if job else None
        self.step = step.label() if step else None
        self.message = message
        self.fix = fix
        self.details = details or []

    def as_dict(self):
        return {
            "code": self.code,
            "severity": self.severity,
            "workflow": self.workflow,
            "line": self.line,
            "job": self.job,
            "step": self.step,
            "message": self.message,
            "details": self.details,
            "fix": self.fix,
        }


def trigger_class(event):
    if event in TRUSTED:
        return "trusted"
    if event in PR_EVENTS:
        return "pr"
    if event in LOW_TRUST:
        return "low"
    return None


def effective_mode(event, wf, job):
    explicit = job.cache_mode or wf.cache_mode
    if explicit:
        return str(explicit)
    cls = trigger_class(event)
    if cls in ("trusted", "pr"):
        return "write"
    if cls == "low":
        return "read"
    return None


def can_write(mode):
    return mode in ("write", "write-only")


def can_read(mode):
    return mode in ("read", "write")


def push_covers_branch(cfg, branch):
    if cfg is None or not isinstance(cfg, dict):
        return True
    if "branches" in cfg:
        pats = cfg.get("branches") or []
        if isinstance(pats, str):
            pats = [pats]
        return globs.match_any(branch, [str(p) for p in pats])
    if "branches-ignore" in cfg:
        pats = cfg.get("branches-ignore") or []
        if isinstance(pats, str):
            pats = [pats]
        return not globs.match_any(branch, [str(p) for p in pats])
    if "tags" in cfg or "tags-ignore" in cfg:
        return False
    return True


def writes_default_branch(wf, job, default_branch):
    """Does some trigger of this workflow save caches in the default branch's scope?"""
    for event, cfg in wf.triggers.items():
        if not can_write(effective_mode(event, wf, job)):
            continue
        if event == "push":
            if push_covers_branch(cfg, default_branch):
                return True
        elif event in TRUSTED:
            return True
        elif trigger_class(event) == "low":
            # explicit write cache-mode on a low-trust trigger: runs on the default branch
            return True
    return False


def _identity_match(a, b):
    if a[0] != b[0]:
        return False
    if a[0] == "setup":
        return a == b
    pa, pb = a[1], b[1]
    if not pa or not pb:
        return True  # key is entirely an expression; can't rule it out
    return pa.startswith(pb) or pb.startswith(pa)


def _restore_identities(step):
    ids = [step.identity()]
    for rk in step.restore_keys:
        from .workflows import literal_prefix

        ids.append(("key", literal_prefix(rk)))
    return ids


def _matrix_dims(runs_on):
    text = runs_on if isinstance(runs_on, str) else " ".join(str(x) for x in (runs_on or []) if x)
    if isinstance(runs_on, dict):
        text = " ".join(str(v) for v in runs_on.values())
    return re.findall(r"matrix\.([A-Za-z0-9_-]+)", text)


def _matrix_values(matrix, dim):
    vals = []
    if not matrix:
        return vals
    v = matrix.get(dim)
    if isinstance(v, list):
        vals.extend(str(x) for x in v)
    for inc in matrix.get("include") or []:
        if isinstance(inc, dict) and dim in inc:
            vals.append(str(inc[dim]))
    return sorted(set(vals))


def _os_family(label):
    label = label.lower()
    for fam in ("windows", "macos", "ubuntu"):
        if fam in label:
            return fam
    return None


def check(workflows, files, default_branch="main"):
    findings = []
    all_steps = [(wf, job, st) for wf in workflows for job in wf.jobs for st in job.steps]

    for wf in workflows:
        if wf.error:
            continue
        events = list(wf.triggers)
        only_reusable = events and all(e == "workflow_call" for e in events)
        for job in wf.jobs:
            for st in job.steps:
                # ---- key shape (actions/cache*) ----
                if st.kind in ("cache", "save", "restore") and st.key:
                    m = VOLATILE.search(st.key)
                    if m and st.kind != "save":
                        if st.restore_keys:
                            findings.append(
                                Finding(
                                    "CH101",
                                    "warn",
                                    wf,
                                    job,
                                    st,
                                    "key changes every run (%s): never an exact hit, only restore-keys "
                                    "partial hits, and every run uploads a new entry (on purpose for "
                                    "incremental caches like ccache; otherwise it just fills the repo's "
                                    "cache quota)" % m.group(1),
                                    fix="key on what the cached files depend on, e.g. "
                                    "${{ runner.os }}-x-${{ hashFiles('<lockfile>') }}",
                                    details=["key: %s" % st.key],
                                )
                            )
                        else:
                            findings.append(
                                Finding(
                                    "CH101",
                                    "error",
                                    wf,
                                    job,
                                    st,
                                    "key changes every run (%s) and there are no restore-keys: "
                                    "this step can never restore anything" % m.group(1),
                                    fix="key on what the cached files depend on, e.g. "
                                    "${{ runner.os }}-x-${{ hashFiles('<lockfile>') }}",
                                    details=["key: %s" % st.key],
                                )
                            )
                    if files is not None:
                        for raw, pats in globs.hashfiles_calls(st.key):
                            if pats and not globs.hashfiles_matches(pats, files):
                                findings.append(
                                    Finding(
                                        "CH102",
                                        "warn",
                                        wf,
                                        job,
                                        st,
                                        "%s matches no file in the repo, so it hashes to '' and the key "
                                        "never changes when your dependencies do (stale cache)" % raw,
                                        fix="point hashFiles at a file that exists, e.g. a lockfile",
                                    )
                                )
                    dims = _matrix_dims(job.runs_on)
                    for dim in dims:
                        vals = _matrix_values(job.matrix, dim)
                        fams = {_os_family(v) for v in vals} - {None, "windows"}
                        if len(fams) > 1 and not OS_REFS.search(st.key) and ("matrix.%s" % dim) not in st.key:
                            findings.append(
                                Finding(
                                    "CH105",
                                    "warn",
                                    wf,
                                    job,
                                    st,
                                    "runs-on uses matrix.%s (%s) but the key has no runner.os or matrix.%s: "
                                    "Linux and macOS jobs read and write the same entry" % (dim, ", ".join(vals), dim),
                                    fix="put ${{ runner.os }} in the key and restore-keys",
                                    details=[
                                        "the cache version covers paths and compression, not the OS (only "
                                        "Windows gets its own version), so whichever job saves first wins and "
                                        "the other OS restores its files"
                                    ],
                                )
                            )

                # ---- dependency paths for setup-* ----
                if st.kind == "setup" and files is not None and st.action in (
                    "actions/setup-python",
                    "actions/setup-node",
                ):
                    dep = st.inputs.get("cache-dependency-path")
                    if dep and "${{" not in str(dep):
                        pats = [p.strip() for p in str(dep).splitlines() if p.strip()]
                        if not globs.hashfiles_matches(pats, files):
                            findings.append(
                                Finding(
                                    "CH103",
                                    "error",
                                    wf,
                                    job,
                                    st,
                                    "cache-dependency-path %s matches no file: %s stops with an error "
                                    "instead of caching" % (pats, st.action),
                                    fix="fix the path, or check out the repo before this step",
                                )
                            )

                if only_reusable or not events:
                    continue

                # ---- cache-mode / trigger reachability ----
                modes = {}
                for event in events:
                    mode = effective_mode(event, wf, job)
                    if mode is not None:
                        modes[event] = mode
                if not modes:
                    continue
                if st.saves and not any(can_write(m) for m in modes.values()):
                    findings.append(
                        Finding(
                            "CH201",
                            "error",
                            wf,
                            job,
                            st,
                            "this step never saves: cache access is read-only or none on every trigger (%s)"
                            % ", ".join("%s=%s" % kv for kv in sorted(modes.items())),
                            fix="save from a push/schedule workflow on %s and use actions/cache/restore here"
                            % default_branch,
                            details=[
                                "GitHub only lets push, workflow_dispatch, repository_dispatch, delete, "
                                "registry_package, page_build and schedule write default-branch caches; "
                                "pull_request_target, issue_comment, workflow_run and other default-branch "
                                "events get read-only access unless cache-mode says otherwise"
                            ],
                        )
                    )
                elif st.saves:
                    blocked = sorted(e for e, m in modes.items() if not can_write(m))
                    if blocked:
                        findings.append(
                            Finding(
                                "CH204",
                                "info",
                                wf,
                                job,
                                st,
                                "on %s runs the save is skipped (cache access is read-only there)"
                                % ", ".join(blocked),
                            )
                        )
                if st.restores and not any(can_read(m) for m in modes.values()):
                    findings.append(
                        Finding(
                            "CH202",
                            "error",
                            wf,
                            job,
                            st,
                            "this step never restores: cache-mode is %s, which does not permit reads"
                            % "/".join(sorted(set(modes.values()))),
                            fix="use cache-mode: write (or read) for jobs that restore",
                        )
                    )

                # ---- PR-only savers ----
                if st.saves and st.kind != "restore":
                    pr_write = any(trigger_class(e) == "pr" and can_write(m) for e, m in modes.items())
                    if pr_write and not writes_default_branch(wf, job, default_branch):
                        covered = False
                        for wf2, job2, st2 in all_steps:
                            if st2.saves and _identity_match(st.identity(), st2.identity()):
                                if writes_default_branch(wf2, job2, default_branch):
                                    covered = True
                                    break
                        if not covered:
                            findings.append(
                                Finding(
                                    "CH203",
                                    "warn",
                                    wf,
                                    job,
                                    st,
                                    "nothing saves this cache on %s: pull_request runs save to "
                                    "refs/pull/<n>/merge, which only re-runs of that same PR can restore, "
                                    "so every new PR starts cold" % default_branch,
                                    fix="also run this workflow on push to %s (or a schedule) so PRs can "
                                    "restore the default branch's cache" % default_branch,
                                )
                            )

                # ---- restore with no saver ----
                if st.kind == "restore":
                    ids = _restore_identities(st)
                    saver = False
                    for wf2, job2, st2 in all_steps:
                        if st2.kind in ("cache", "save") and any(
                            _identity_match(i, st2.identity()) for i in ids
                        ):
                            saver = True
                            break
                    if not saver:
                        findings.append(
                            Finding(
                                "CH104",
                                "error",
                                wf,
                                job,
                                st,
                                "actions/cache/restore with key prefix %r, but no workflow in this repo "
                                "saves a matching key: this step always misses" % st.identity()[1],
                                fix="add actions/cache/save (or actions/cache) with the same key somewhere "
                                "that runs on %s" % default_branch,
                            )
                        )

    findings.sort(key=lambda f: (f.workflow, f.line, SEVERITY_ORDER[f.severity], f.code))
    return findings
