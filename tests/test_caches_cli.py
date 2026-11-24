import json
import os
import subprocess
import sys

from cachehit import caches
from cachehit.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_family_collapses_hashes():
    assert caches.family("tox-Linux-52e6b438f2a74de4") == "tox-Linux-*"
    assert caches.family("tools-Linux-1918233445") == "tools-Linux-*"
    assert caches.family("npm-Linux-v1") == "npm-Linux-v1"


def test_summarize_example():
    data = json.load(open(os.path.join(ROOT, "examples", "caches.json")))
    s = caches.summarize(data["actions_caches"])
    assert s["count"] == 11
    assert len(s["never"]) == 10
    assert s["refs"]["refs/heads/main"][0] == 6


def test_render_mentions_pr_scope_and_limit():
    data = json.load(open(os.path.join(ROOT, "examples", "caches.json")))
    out = caches.render("x/y", data["actions_caches"])
    assert "near the limit" in out
    assert "only re-runs of PR #412 can restore these" in out
    out2 = caches.render("x/y", data["actions_caches"], limit=50 * 1024**3)
    assert "near the limit" not in out2


def test_check_exit_codes(capsys):
    demo = os.path.join(ROOT, "examples", "demo")
    assert main(["check", demo, "--default-branch", "main"]) == 1
    out = capsys.readouterr().out
    assert "CH201" in out and "CH203" in out


def test_check_json(capsys):
    demo = os.path.join(ROOT, "examples", "demo")
    main(["check", demo, "--default-branch", "main", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["cache_steps"] == 7
    assert {f["code"] for f in data["findings"]} >= {"CH101", "CH102", "CH103", "CH104", "CH105", "CH201", "CH203"}


def test_check_clean_repo_exit_zero(tmp_path, capsys):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n")
    assert main(["check", str(tmp_path)]) == 0


def test_fail_on_warn(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "on: pull_request\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/cache@v4\n        with:\n          path: x\n          key: k-${{ hashFiles('a') }}\n"
    )
    (tmp_path / "a").write_text("1")
    assert main(["check", str(tmp_path), "--default-branch", "main"]) == 0
    assert main(["check", str(tmp_path), "--default-branch", "main", "--fail-on", "warn"]) == 1


def test_no_workflows_exit_2(tmp_path):
    assert main(["check", str(tmp_path)]) == 2


def test_module_entrypoint_version():
    out = subprocess.run([sys.executable, "-m", "cachehit", "--version"], capture_output=True, text=True, cwd=ROOT)
    assert out.stdout.strip().startswith("cachehit 0.1.0")


def test_runs_with_fake_gh(monkeypatch, tmp_path, capsys):
    from cachehit import cli

    log = open(os.path.join(ROOT, "examples", "logs", "ci-ubuntu-run2.txt")).read()

    def fake_json(path):
        if path.startswith("repos/o/r/actions/workflows/ci.yml/runs"):
            assert "status=completed" in path
            return {"workflow_runs": [{"id": 1, "name": "CI"}, {"id": 2, "name": "CI"}]}
        if path.endswith("/jobs?per_page=100"):
            return {"jobs": [{"id": 10, "name": "test", "conclusion": "success", "steps": [{}]},
                             {"id": 11, "name": "skip", "conclusion": "skipped", "steps": []}]}
        raise AssertionError(path)

    monkeypatch.setattr(cli, "api_json", fake_json)
    monkeypatch.setattr(cli, "api_text", lambda path: log)
    rc = cli.main(["runs", "o/r", "--workflow", "ci.yml", "--limit", "2", "--save-logs", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "2 job logs read" in out
    assert "CI / actions/cache@v4" in out and "100%" in out
    assert sorted(os.listdir(tmp_path)) == ["1-10.txt", "2-10.txt"]


def test_runs_without_gh(monkeypatch, capsys):
    from cachehit import gh

    monkeypatch.setattr(gh.shutil, "which", lambda name: None)
    assert main(["runs", "o/r"]) == 2
    assert "gh" in capsys.readouterr().err
