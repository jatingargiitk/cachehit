import glob
import os

from cachehit import logs

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "logs")


def read(name):
    return open(os.path.join(EX, name), encoding="utf-8").read()


def test_partial_hit_and_save():
    restores, saves, mode = logs.parse(read("ci-ubuntu-run1.txt"))
    assert mode == "write"
    assert [(r.label, r.outcome) for r in restores] == [("actions/cache@v4", "partial")]
    assert restores[0].inputs["key"] == "tox-Linux-3c1f0b7e9a"
    assert saves == [("saved", "tox-Linux-3c1f0b7e9a")]


def test_exact_hit_not_saved():
    restores, saves, _ = logs.parse(read("ci-ubuntu-run2.txt"))
    assert [r.outcome for r in restores] == ["exact"]
    assert saves == [("not-needed", "tox-Linux-3c1f0b7e9a")]


def test_miss_and_reserve_failure():
    restores, saves, _ = logs.parse(read("ci-macos-run1.txt"))
    assert [r.outcome for r in restores] == ["miss"]
    assert saves[0][0] == "failed"
    assert "key already taken" in logs.save_reason(saves[0][1])


def test_read_mode_save_blocked():
    restores, saves, mode = logs.parse(read("bot-run1.txt"))
    assert mode == "read"
    assert [(r.label, r.outcome) for r in restores] == [
        ("actions/setup-node@v4", "miss"),
        ("actions/cache/restore@v4", "miss"),
    ]
    assert saves == [("blocked", "cache-mode 'read'")]


def test_restore_failed_warning():
    restores, _, _ = logs.parse(read("go-run1.txt"))
    assert restores[0].outcome == "failed"
    assert "go.mod" in restores[0].matched


def test_restored_from_key_without_toolkit_line():
    text = "\n".join(
        [
            "##[group]Run actions/cache@v3",
            "with:",
            "  path: x",
            "  key: a-1",
            "##[endgroup]",
            "Cache restored from key: a-0",
        ]
    )
    restores, _, _ = logs.parse(text)
    assert restores[0].outcome == "partial"


def test_setup_action_hit_without_key_is_plain_hit():
    text = "##[group]Run actions/setup-python@v5\nwith:\n  cache: pip\n##[endgroup]\nCache restored from key: setup-python-Linux-abc\n"
    restores, _, _ = logs.parse(text)
    assert restores[0].outcome == "hit"


def test_cache_action_with_no_outcome_line_counts_as_miss():
    text = "##[group]Run Swatinem/rust-cache@v2\n##[endgroup]\nsomething else\n"
    restores, _, _ = logs.parse(text)
    assert restores[0].outcome == "miss"


def test_setup_without_cache_input_ignored():
    text = "##[group]Run actions/setup-node@v4\nwith:\n  node-version: 22\n##[endgroup]\n"
    restores, _, _ = logs.parse(text)
    assert restores == []


def test_restore_blocked_by_mode():
    text = "##[group]Run actions/cache@v4\n##[endgroup]\nCache restore skipped: the effective cache-mode 'write-only' does not permit reads.\n"
    restores, _, _ = logs.parse(text)
    assert (restores[0].outcome, restores[0].matched) == ("blocked", "write-only")


def test_ansi_and_bom_stripped():
    text = "\ufeff2026-11-20T05:00:01.0000000Z \x1b[36;1mCache mode: write\x1b[0m\n"
    _, _, mode = logs.parse(text)
    assert mode == "write"


def test_tally_groups_by_step_and_shortens_sha():
    t = logs.Tally()
    sha = "668228422ae6a00e4ad889ee87cd7109ec5666a7"
    text = "##[group]Run actions/cache@%s\n##[endgroup]\nCache hit for: k\n" % sha
    t.add("CI", "job", text)
    t.add("CI", "job", text)
    assert t.rows == {("CI", "actions/cache@6682284"): {"exact": 2, "partial": 0, "hit": 0, "miss": 0, "failed": 0, "blocked": 0}}
    assert t.jobs == 2
