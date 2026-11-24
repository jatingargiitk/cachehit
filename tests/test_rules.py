from helpers import codes, run_check

PUSH_MAIN = """
on:
  push:
    branches: [main]
  pull_request:
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/cache@v4
        with:
          path: x
          key: %s
%s
"""


def wf(key, extra=""):
    return {"ci.yml": PUSH_MAIN % (key, extra)}


def test_clean_cache_has_no_findings(tmp_path):
    f = run_check(tmp_path, wf("npm-${{ runner.os }}-${{ hashFiles('package-lock.json') }}"), ["package-lock.json"])
    assert f == []


def test_run_id_key_without_restore_keys_is_error(tmp_path):
    f = run_check(tmp_path, wf("tools-${{ github.run_id }}"))
    assert codes(f) == [("CH101", "error")]
    assert "github.run_id" in f[0].message


def test_sha_key_with_restore_keys_is_warning(tmp_path):
    extra = "          restore-keys: |\n            build-\n"
    f = run_check(tmp_path, wf("build-${{ github.sha }}", extra))
    assert codes(f) == [("CH101", "warn")]


def test_hashfiles_matching_nothing(tmp_path):
    f = run_check(tmp_path, wf("deps-${{ hashFiles('**/requirements.txt') }}"), ["src/app.py"])
    assert codes(f) == [("CH102", "warn")]


def test_hashfiles_double_star_matches_root_file(tmp_path):
    f = run_check(tmp_path, wf("deps-${{ hashFiles('**/requirements.txt') }}"), ["requirements.txt"])
    assert f == []


def test_hashfiles_negation(tmp_path):
    key = "deps-${{ hashFiles('**/*.lock', '!vendor/**') }}"
    f = run_check(tmp_path, wf(key), ["vendor/a.lock"])
    assert codes(f) == [("CH102", "warn")]
    f2 = run_check(tmp_path / "b", wf(key), ["vendor/a.lock", "app/b.lock"])
    assert f2 == []


def test_setup_python_dependency_path_missing(tmp_path):
    w = {
        "ci.yml": """
on: push
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with:
          cache: pip
          cache-dependency-path: requirements/*.txt
"""
    }
    assert codes(run_check(tmp_path, w)) == [("CH103", "error")]
    assert run_check(tmp_path / "b", w, ["requirements/dev.txt"]) == []


def test_setup_python_without_cache_is_ignored(tmp_path):
    w = {
        "ci.yml": """
on: pull_request
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
"""
    }
    assert run_check(tmp_path, w) == []


def test_restore_without_saver(tmp_path):
    w = {
        "ci.yml": """
on: push
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/cache/restore@v4
        with:
          path: m
          key: models-${{ hashFiles('m.lock') }}
"""
    }
    assert ("CH104", "error") in codes(run_check(tmp_path, w, ["m.lock"]))


def test_restore_with_saver_elsewhere(tmp_path):
    w = {
        "use.yml": """
on: pull_request
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/cache/restore@v4
        with:
          path: m
          key: models-${{ hashFiles('m.lock') }}
""",
        "fill.yml": """
on:
  schedule:
    - cron: '0 3 * * *'
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/cache/save@v4
        with:
          path: m
          key: models-${{ hashFiles('m.lock') }}
""",
    }
    assert run_check(tmp_path, w, ["m.lock"]) == []


def test_os_matrix_without_runner_os(tmp_path):
    w = {
        "ci.yml": """
on: push
jobs:
  t:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
    steps:
      - uses: actions/cache@v4
        with:
          path: x
          key: deps-${{ hashFiles('a.lock') }}
"""
    }
    assert codes(run_check(tmp_path, w, ["a.lock"])) == [("CH105", "warn")]
    fixed = w["ci.yml"].replace("key: deps-", "key: deps-${{ runner.os }}-")
    assert run_check(tmp_path / "b", {"ci.yml": fixed}, ["a.lock"]) == []


def test_linux_plus_windows_matrix_is_fine(tmp_path):
    w = {
        "ci.yml": """
on: push
jobs:
  t:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    steps:
      - uses: actions/cache@v4
        with:
          path: x
          key: deps-${{ hashFiles('a.lock') }}
"""
    }
    assert run_check(tmp_path, w, ["a.lock"]) == []


LOW = """
on:
  %s
jobs:
  t:
    runs-on: ubuntu-latest
%s    steps:
      - uses: actions/cache@v4
        with:
          path: x
          key: deps-${{ hashFiles('a.lock') }}
"""


def test_pull_request_target_never_saves(tmp_path):
    f = run_check(tmp_path, {"bot.yml": LOW % ("pull_request_target:", "")}, ["a.lock"])
    assert codes(f) == [("CH201", "error")]
    assert "pull_request_target=read" in f[0].message


def test_issue_comment_and_workflow_run_never_save(tmp_path):
    for i, ev in enumerate(["issue_comment:", "workflow_run:\n    workflows: [CI]"]):
        f = run_check(tmp_path / str(i), {"bot.yml": LOW % (ev, "")}, ["a.lock"])
        assert codes(f) == [("CH201", "error")]


def test_explicit_cache_mode_write_lifts_restriction(tmp_path):
    f = run_check(tmp_path, {"bot.yml": LOW % ("pull_request_target:", "    cache-mode: write\n")}, ["a.lock"])
    assert f == []


def test_cache_mode_none_never_restores_or_saves(tmp_path):
    f = run_check(tmp_path, {"ci.yml": LOW % ("push:", "    cache-mode: none\n")}, ["a.lock"])
    assert codes(f) == [("CH201", "error"), ("CH202", "error")]


def test_workflow_level_cache_mode_read(tmp_path):
    text = "cache-mode: read\n" + LOW % ("push:", "")
    f = run_check(tmp_path, {"ci.yml": text}, ["a.lock"])
    assert codes(f) == [("CH201", "error")]


def test_job_cache_mode_overrides_workflow(tmp_path):
    text = "cache-mode: read\n" + LOW % ("push:", "    cache-mode: write\n")
    assert run_check(tmp_path, {"ci.yml": text}, ["a.lock"]) == []


def test_mixed_triggers_info_only(tmp_path):
    f = run_check(tmp_path, {"ci.yml": LOW % ("push:\n  issue_comment:", "")}, ["a.lock"])
    assert codes(f) == [("CH204", "info")]


def test_pr_only_workflow_starts_cold(tmp_path):
    f = run_check(tmp_path, {"ci.yml": LOW % ("pull_request:", "")}, ["a.lock"])
    assert codes(f) == [("CH203", "warn")]


def test_pr_only_covered_by_push_workflow(tmp_path):
    w = {"ci.yml": LOW % ("pull_request:", ""), "main.yml": LOW % ("push:\n    branches: [main]", "")}
    assert run_check(tmp_path, w, ["a.lock"]) == []


def test_push_to_other_branch_does_not_cover_default(tmp_path):
    w = {"ci.yml": LOW % ("pull_request:", ""), "rel.yml": LOW % ("push:\n    branches: ['release/**']", "")}
    f = run_check(tmp_path, w, ["a.lock"])
    assert ("CH203", "warn") in codes(f)


def test_push_tags_only_does_not_cover_default(tmp_path):
    w = {"ci.yml": LOW % ("pull_request:", ""), "rel.yml": LOW % ("push:\n    tags: ['v*']", "")}
    assert ("CH203", "warn") in codes(run_check(tmp_path, w, ["a.lock"]))


def test_default_branch_option(tmp_path):
    w = {"ci.yml": LOW % ("pull_request:", ""), "main.yml": LOW % ("push:\n    branches: [main]", "")}
    f = run_check(tmp_path, w, ["a.lock"], default_branch="trunk")
    assert ("CH203", "warn") in codes(f)


def test_branches_ignore(tmp_path):
    w = {"ci.yml": LOW % ("pull_request:", ""), "main.yml": LOW % ("push:\n    branches-ignore: [main]", "")}
    assert ("CH203", "warn") in codes(run_check(tmp_path, w, ["a.lock"]))


def test_on_as_list_and_string(tmp_path):
    for i, on in enumerate(["[pull_request_target]", "pull_request_target"]):
        text = LOW.replace("on:\n  %s", "on: %s") % (on, "")
        assert codes(run_check(tmp_path / str(i), {"ci.yml": text}, ["a.lock"])) == [("CH201", "error")]


def test_reusable_only_workflow_skips_trigger_rules(tmp_path):
    f = run_check(tmp_path, {"ci.yml": LOW % ("workflow_call:", "")}, ["a.lock"])
    assert f == []


def test_setup_go_default_cache_v5(tmp_path):
    w = {
        "bot.yml": """
on: issue_comment
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-go@v5
        with:
          go-version: '1.23'
"""
    }
    assert codes(run_check(tmp_path, w)) == [("CH201", "error")]
    v3 = w["bot.yml"].replace("setup-go@v5", "setup-go@v3")
    assert run_check(tmp_path / "b", {"bot.yml": v3}) == []


def test_broken_yaml_is_skipped(tmp_path):
    f = run_check(tmp_path, {"bad.yml": "on: [push\njobs: {"})
    assert f == []


def test_line_numbers_point_at_step(tmp_path):
    f = run_check(tmp_path, wf("tools-${{ github.run_id }}"))
    assert f[0].line == 10
