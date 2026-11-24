"""Read cache outcomes out of GitHub Actions job logs.

The lines come from the @actions/cache toolkit (shared by actions/cache,
setup-python/node/go/java, setup-uv, rust-cache, ...) and from the actions
themselves:

  Cache hit for: KEY                      exact hit (toolkit)
  Cache hit for restore-key: KEY          partial hit via restore-keys (toolkit)
  Cache restored from key: KEY            hit, reported by the action
  Cache not found for input keys: ...     miss (actions/cache)
  <pm> cache is not found / Cache is not found / No cache found.   miss (setup-*, rust-cache)
  Restore cache failed: MSG / Failed to restore: MSG               restore errored
  Cache mode: write                       effective cache-mode, printed by the runner at job set-up
  Cache restore skipped: the effective cache-mode 'X' ...          restore blocked by cache-mode
  Cache saved with key: KEY               saved (actions/cache post step)
  Cache hit occurred on the primary key KEY, not saving cache.     nothing to save
  Failed to save: MSG                     save failed (toolkit)
  Cache save skipped: the effective cache-mode 'X' ...             save blocked by cache-mode
"""

import re

TS = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ?")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

HIT_EXACT = re.compile(r"^Cache hit for: (.+)$")
HIT_PARTIAL = re.compile(r"^Cache hit for restore-key: (.+)$")
RESTORED = re.compile(r"^(?:\S+ )?[Cc]ache restored from key: (.+)$")
MISS = re.compile(
    r"^(Cache not found for input keys: .*|[\w .-]{0,60}[Cc]ache is not found|No cache found\.)$"
)
RESTORE_FAILED = re.compile(r"^(?:##\[warning\])?(?:Restore cache failed|Failed to restore): (.+)$")
JOB_MODE = re.compile(r"^Cache mode: (\S+)$")
RESTORE_BLOCKED = re.compile(r"^Cache restore skipped: the effective cache-mode '([^']*)'")
SAVED = re.compile(r"^(?:[\w .-]{0,60} )?[Cc]ache saved with (?:the )?key: (.+)$")
SAVE_NOOP = re.compile(r"^Cache hit occurred on (?:the primary )?key (.+?), not saving cache\.$")
SAVE_FAILED = re.compile(r"^(?:##\[(?:warning|error)\])?Failed to save: (.+)$")
SAVE_BLOCKED = re.compile(r"^Cache save skipped: the effective cache-mode '([^']*)'")
STEP = re.compile(r"^##\[group\]Run (.+)$")
INPUT = re.compile(r"^  ([A-Za-z0-9_-]+): (.*)$")

CACHE_ACTIONS = (
    "actions/cache",
    "actions/setup-python",
    "actions/setup-node",
    "actions/setup-go",
    "actions/setup-java",
    "astral-sh/setup-uv",
    "swatinem/rust-cache",
)


OUTCOMES = ("exact", "partial", "hit", "miss", "failed", "blocked")


def clean(line):
    return ANSI.sub("", TS.sub("", line.rstrip("\r\n")))


class StepOutcome:
    def __init__(self, label):
        self.label = label
        self.inputs = {}
        self.outcome = None  # exact | partial | hit | miss | failed | blocked
        self.matched = None

    def is_cache_action(self):
        head = self.label.split("@", 1)[0].lower()
        return any(head == a or head.startswith(a + "/") for a in CACHE_ACTIONS)


def parse(text):
    """Return (restores, saves, mode): restores is a list of StepOutcome that touched
    the cache, saves a list of (status, detail), mode the job's effective cache-mode
    if the runner printed it."""
    mode = None
    steps = []
    saves = []
    cur = None
    in_inputs = False
    for raw in text.splitlines():
        line = clean(raw)
        if mode is None and cur is None and not steps:
            jm = JOB_MODE.match(line)
            if jm:
                mode = jm.group(1)
                continue
        m = STEP.match(line)
        if m:
            cur = StepOutcome(m.group(1).strip())
            steps.append(cur)
            in_inputs = False
            continue
        if line.startswith("Post job cleanup."):
            cur = None
            continue
        if cur is not None:
            if line == "with:":
                in_inputs = True
                continue
            if in_inputs:
                im = INPUT.match(line)
                if im:
                    cur.inputs[im.group(1)] = im.group(2)
                    continue
                in_inputs = False
            m = HIT_EXACT.match(line)
            if m:
                cur.outcome, cur.matched = "exact", m.group(1)
                continue
            m = HIT_PARTIAL.match(line)
            if m:
                cur.outcome, cur.matched = "partial", m.group(1)
                continue
            m = RESTORED.match(line)
            if m and cur.outcome is None:
                key = cur.inputs.get("key")
                if key:
                    cur.outcome = "exact" if m.group(1) == key else "partial"
                else:
                    cur.outcome = "hit"
                cur.matched = m.group(1)
                continue
            if MISS.match(line) and cur.outcome is None:
                cur.outcome = "miss"
                continue
            m = RESTORE_FAILED.match(line)
            if m and cur.outcome is None:
                cur.outcome, cur.matched = "failed", m.group(1)
                continue
            m = RESTORE_BLOCKED.match(line)
            if m:
                cur.outcome, cur.matched = "blocked", m.group(1)
                continue
        m = SAVED.match(line)
        if m:
            saves.append(("saved", m.group(1)))
            continue
        m = SAVE_NOOP.match(line)
        if m:
            saves.append(("not-needed", m.group(1)))
            continue
        m = SAVE_FAILED.match(line)
        if m:
            saves.append(("failed", m.group(1)))
            continue
        m = SAVE_BLOCKED.match(line)
        if m:
            saves.append(("blocked", "cache-mode '%s'" % m.group(1)))
    restores = []
    for st in steps:
        if st.outcome is None and st.is_cache_action():
            head = st.label.split("@", 1)[0].lower()
            # actions/cache/save never restores; setup-* only cache when asked to
            if head == "actions/cache/save":
                continue
            if head.startswith("actions/setup-") and not st.inputs.get("cache"):
                if head != "actions/setup-go":
                    continue
            if head == "astral-sh/setup-uv" and st.inputs.get("enable-cache", "").lower() != "true":
                continue
            st.outcome = "miss"
        if st.outcome:
            restores.append(st)
    return restores, saves, mode


def save_reason(detail):
    if "Unable to reserve cache" in detail or "another job may be creating this cache" in detail:
        return "key already taken (another job saved it first, or it exists for this scope)"
    if "cache-mode" in detail:
        return detail
    return detail


SHA = re.compile(r"@([0-9a-f]{7})[0-9a-f]{33}\b")


def short_label(label):
    return SHA.sub(r"@\1", label)


class Tally:
    def __init__(self):
        self.rows = {}  # (workflow, step) -> counts
        self.reasons = {}  # (workflow, step) -> {reason: n}
        self.saves = {}
        self.jobs = 0
        self.modes = {}

    def add(self, workflow, job, text):
        self.jobs += 1
        restores, saves, mode = parse(text)
        if mode:
            self.modes[mode] = self.modes.get(mode, 0) + 1
        for st in restores:
            key = (workflow, short_label(st.label))
            row = self.rows.setdefault(key, dict.fromkeys(OUTCOMES, 0))
            row[st.outcome] += 1
            if st.outcome in ("failed", "blocked"):
                why = st.matched if st.outcome == "failed" else "cache-mode '%s'" % st.matched
                r = self.reasons.setdefault(key, {})
                r[why] = r.get(why, 0) + 1
        for status, detail in saves:
            k = (workflow, status, save_reason(detail) if status in ("failed", "blocked") else "")
            self.saves[k] = self.saves.get(k, 0) + 1
