"""Load workflow files and pull out the steps that touch the Actions cache."""

import os
import re

import yaml

EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)


class Workflow:
    def __init__(self, path, name, triggers, cache_mode, jobs, error=None):
        self.path = path
        self.name = name
        self.triggers = triggers  # dict: event -> config (dict or None)
        self.cache_mode = cache_mode
        self.jobs = jobs  # list of Job
        self.error = error


class Job:
    def __init__(self, job_id, name, cache_mode, runs_on, matrix, steps, uses=None):
        self.id = job_id
        self.name = name
        self.cache_mode = cache_mode
        self.runs_on = runs_on
        self.matrix = matrix
        self.steps = steps  # list of CacheStep
        self.uses = uses  # reusable workflow call


class CacheStep:
    """A step that restores and/or saves a cache.

    kind: "cache" (actions/cache: restore + post-save), "restore", "save",
    or "setup" (setup-python/node/go/java, rust-cache, setup-uv: the action
    builds its own key internally).
    """

    def __init__(self, index, name, uses, kind, inputs, line):
        self.index = index
        self.name = name
        self.uses = uses
        self.kind = kind
        self.inputs = inputs
        self.line = line

    @property
    def action(self):
        return self.uses.split("@", 1)[0].lower()

    @property
    def restores(self):
        return self.kind in ("cache", "restore", "setup")

    @property
    def saves(self):
        return self.kind in ("cache", "save", "setup")

    @property
    def key(self):
        return str(self.inputs.get("key", "") or "")

    @property
    def restore_keys(self):
        raw = self.inputs.get("restore-keys")
        if not raw:
            return []
        return [k.strip() for k in str(raw).splitlines() if k.strip()]

    def label(self):
        return self.name or self.uses

    def identity(self):
        """What makes two steps share cache entries, as far as we can tell statically.

        For actions/cache* it is the literal key prefix (text before the first
        ${{ }}); for setup-style actions it is the action plus its cache type.
        """
        if self.kind == "setup":
            return ("setup", self.action, str(self.inputs.get("cache", "")).lower())
        return ("key", literal_prefix(self.key))


def literal_prefix(text):
    m = EXPR.search(text)
    return (text[: m.start()] if m else text).strip()


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("", "false", "0", "no", "off")


def classify_step(uses, inputs):
    """Return the cache kind for a `uses:` step, or None if it doesn't cache."""
    if not uses or uses.startswith("./") or uses.startswith("docker://"):
        return None
    action, _, ref = uses.partition("@")
    action = action.lower()
    if action == "actions/cache":
        return "cache"
    if action == "actions/cache/restore":
        return "restore"
    if action == "actions/cache/save":
        return "save"
    if action in ("actions/setup-python", "actions/setup-node", "actions/setup-java"):
        return "setup" if _truthy(inputs.get("cache", "")) else None
    if action == "actions/setup-go":
        # setup-go caches by default since v4.
        if "cache" in inputs:
            return "setup" if _truthy(inputs["cache"]) else None
        m = re.match(r"v(\d+)", ref)
        return "setup" if (m and int(m.group(1)) >= 4) else None
    if action == "astral-sh/setup-uv":
        return "setup" if _truthy(inputs.get("enable-cache", "")) else None
    if action == "swatinem/rust-cache":
        return "setup"
    return None


def _line_of(text, needle, start=0):
    idx = text.find(needle, start)
    if idx < 0:
        return 0, start
    return text.count("\n", 0, idx) + 1, idx + len(needle)


def _triggers(on):
    if on is None:
        return {}
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {str(e): None for e in on}
    if isinstance(on, dict):
        return {str(k): v for k, v in on.items()}
    return {}


def load_workflow(path):
    text = open(path, encoding="utf-8").read()
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return Workflow(path, os.path.basename(path), {}, None, [], error="YAML error: %s" % exc)
    if not isinstance(doc, dict):
        return Workflow(path, os.path.basename(path), {}, None, [], error="not a mapping")
    # PyYAML (YAML 1.1) reads the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    jobs = []
    cursor = 0
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        _, cursor = _line_of(text, "%s:" % job_id, cursor)
        steps = []
        for i, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            uses = str(step.get("uses", "") or "")
            inputs = step.get("with") or {}
            if not isinstance(inputs, dict):
                inputs = {}
            inputs = {str(k): v for k, v in inputs.items()}
            line, after = _line_of(text, uses, cursor) if uses else (0, cursor)
            if uses:
                cursor = after
            kind = classify_step(uses, inputs)
            if kind:
                steps.append(CacheStep(i, step.get("name"), uses, kind, inputs, line))
        strategy = job.get("strategy") or {}
        matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
        jobs.append(
            Job(
                str(job_id),
                job.get("name"),
                job.get("cache-mode"),
                job.get("runs-on"),
                matrix if isinstance(matrix, dict) else None,
                steps,
                uses=job.get("uses"),
            )
        )
    return Workflow(path, doc.get("name") or os.path.basename(path), _triggers(on), doc.get("cache-mode"), jobs)


def load_repo(root):
    wf_dir = os.path.join(root, ".github", "workflows")
    out = []
    if not os.path.isdir(wf_dir):
        return out
    for fn in sorted(os.listdir(wf_dir)):
        if fn.endswith((".yml", ".yaml")):
            out.append(load_workflow(os.path.join(wf_dir, fn)))
    return out
