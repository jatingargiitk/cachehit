# cachehit

Why do your GitHub Actions caches miss, and can they save at all?

`cachehit` reads your workflows and tells you which cache steps can never hit, never save
or never restore. It reads your run logs and gives you the real hit rate per cache step.
It also breaks down what's using your repo's cache storage.

GitHub now only lets `push`, `schedule`, `workflow_dispatch` and a few other triggers write
caches for the default branch. This has been in the docs since June 2026, and the `cache-mode` key since September 2026. `pull_request_target`, `issue_comment`,
`workflow_run` and other default-branch events get **read-only** cache access unless the job
sets the new `cache-mode` key. A save step on one of those triggers is now skipped with a
warning, and the job stays green. `cachehit check` flags those steps, along with the older
traps:

| code  | severity | what it means |
|-------|----------|---------------|
| CH101 | error / warn | `key` contains `github.run_id`, `github.sha`, `run_number`... It never repeats, so the step never hits (error), or with `restore-keys` only gets partial hits and uploads a new entry every run (warn) |
| CH102 | warn  | `hashFiles(...)` matches no file. It hashes to `''`, so the key never changes when your dependencies do |
| CH103 | error | `cache-dependency-path` of setup-python / setup-node matches no file, so the action stops with an error |
| CH104 | error | `actions/cache/restore` key that no workflow in the repo ever saves, so it always misses |
| CH105 | warn  | `runs-on: ${{ matrix.os }}` covers Linux and macOS but the key has no `runner.os`, so the two OSes share one entry |
| CH201 | error | the step saves, but cache access is `read`/`none` on every trigger of the workflow, so it never saves |
| CH202 | error | `cache-mode: none` / `write-only` on a step that restores, so it never restores |
| CH203 | warn  | the cache is only saved by `pull_request` runs. Those go to `refs/pull/<n>/merge`, which only re-runs of that same PR can read, and nothing saves it on the default branch, so every new PR starts cold |
| CH204 | info  | some triggers of the workflow are read-only, so the save is skipped on those runs (`--info`) |

The trigger rules follow GitHub's
[dependency caching reference](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
(cache access for low-trust triggers, `cache-mode`, cache scope by ref, the 10 GB default limit).

## Install

You need Python 3.9+ and one dependency, [PyYAML](https://pypi.org/project/PyYAML/). It isn't on PyPI, so install it from GitHub:

```sh
pip install git+https://github.com/jatingargiitk/cachehit
# or
pipx install git+https://github.com/jatingargiitk/cachehit
```

`check` and `logs` work offline. `runs` and `caches` call the GitHub API through the
[GitHub CLI](https://cli.github.com/) (`gh api`), so they use whatever `gh auth` you already have.
cachehit never reads or stores a token itself.

## Usage

The first three examples use the files in [`examples/`](examples). Every `console` block below
is the real output of the command shown. CI re-runs them and fails if the README drifts
(`python scripts/check_readme.py`).

### Check workflows (offline)

[`examples/demo`](examples/demo/.github/workflows) has four workflows with the usual mistakes:

```console
$ cachehit check examples/demo --default-branch main
cachehit: 4 workflows, 7 cache steps, default branch main

ERROR CH201  .github/workflows/bot.yml:11  job lint, step actions/setup-node@v4
      this step never saves: cache access is read-only or none on every trigger (issue_comment=read, pull_request_target=read)
      GitHub only lets push, workflow_dispatch, repository_dispatch, delete, registry_package, page_build and schedule write default-branch caches; pull_request_target, issue_comment, workflow_run and other default-branch events get read-only access unless cache-mode says otherwise
      fix: save from a push/schedule workflow on main and use actions/cache/restore here

ERROR CH104  .github/workflows/bot.yml:16  job lint, step Restore model weights
      actions/cache/restore with key prefix 'models-', but no workflow in this repo saves a matching key: this step always misses
      fix: add actions/cache/save (or actions/cache) with the same key somewhere that runs on main

WARN  CH102  .github/workflows/bot.yml:16  job lint, step Restore model weights
      hashFiles('models.lock') matches no file in the repo, so it hashes to '' and the key never changes when your dependencies do (stale cache)
      fix: point hashFiles at a file that exists, e.g. a lockfile

WARN  CH101  .github/workflows/build.yml:11  job build, step Cache build output
      key changes every run (github.sha): never an exact hit, only restore-keys partial hits, and every run uploads a new entry (on purpose for incremental caches like ccache; otherwise it just fills the repo's cache quota)
      key: build-${{ runner.os }}-${{ github.sha }}
      fix: key on what the cached files depend on, e.g. ${{ runner.os }}-x-${{ hashFiles('<lockfile>') }}

ERROR CH101  .github/workflows/build.yml:18  job build, step Cache tools
      key changes every run (github.run_id) and there are no restore-keys: this step can never restore anything
      key: tools-${{ runner.os }}-${{ github.run_id }}
      fix: key on what the cached files depend on, e.g. ${{ runner.os }}-x-${{ hashFiles('<lockfile>') }}

ERROR CH103  .github/workflows/ci.yml:12  job test, step actions/setup-python@v5
      cache-dependency-path ['requirements/*.txt'] matches no file: actions/setup-python stops with an error instead of caching
      fix: fix the path, or check out the repo before this step

WARN  CH203  .github/workflows/ci.yml:12  job test, step actions/setup-python@v5
      nothing saves this cache on main: pull_request runs save to refs/pull/<n>/merge, which only re-runs of that same PR can restore, so every new PR starts cold
      fix: also run this workflow on push to main (or a schedule) so PRs can restore the default branch's cache

WARN  CH102  .github/workflows/ci.yml:18  job test, step Cache tox envs
      hashFiles('requirements.txt') matches no file in the repo, so it hashes to '' and the key never changes when your dependencies do (stale cache)
      fix: point hashFiles at a file that exists, e.g. a lockfile

WARN  CH105  .github/workflows/ci.yml:18  job test, step Cache tox envs
      runs-on uses matrix.os (macos-latest, ubuntu-latest, windows-latest) but the key has no runner.os or matrix.os: Linux and macOS jobs read and write the same entry
      the cache version covers paths and compression, not the OS (only Windows gets its own version), so whichever job saves first wins and the other OS restores its files
      fix: put ${{ runner.os }} in the key and restore-keys

WARN  CH203  .github/workflows/ci.yml:18  job test, step Cache tox envs
      nothing saves this cache on main: pull_request runs save to refs/pull/<n>/merge, which only re-runs of that same PR can restore, so every new PR starts cold
      fix: also run this workflow on push to main (or a schedule) so PRs can restore the default branch's cache

4 errors, 6 warnings
```

The exit code is 1 when there are errors. Use `--fail-on warn` to fail on warnings too, `--json` for machine output,
and `--info` to also list the CH204 notes. In CI:

```yaml
- uses: actions/checkout@v4
- run: pipx run --spec git+https://github.com/jatingargiitk/cachehit cachehit check .
```

### Hit rate from job logs

`cachehit logs` tallies saved job logs (for example from `cachehit runs --save-logs DIR` or
`gh api repos/O/R/actions/jobs/ID/logs`). The files in `examples/logs` are short hand-written
samples. They use the exact lines that `actions/cache`, the `@actions/cache` toolkit, setup-node and
setup-go print:

```console
$ cachehit logs --workflow CI examples/logs/ci-ubuntu-run1.txt examples/logs/ci-ubuntu-run2.txt examples/logs/ci-macos-run1.txt examples/logs/bot-run1.txt examples/logs/go-run1.txt
cachehit: 5 job logs

step                            jobs    exact  partial     miss   failed  hit-rate
CI / actions/cache/restore@v4      1        0        0        1        0        0%
CI / actions/cache@v4              3        1        1        1        0       67%
CI / actions/setup-go@v5           1        0        0        0        1        0%
CI / actions/setup-node@v4         1        0        0        1        0        0%

CI / actions/setup-go@v5:
  1x Dependencies file is not found in /home/runner/work/app/app. Supported file pattern: go.mod

saves:
  CI                             blocked       1  cache-mode 'read'
  CI                             failed        1  key already taken (another job saved it first, or it exists for this scope)
  CI                             not-needed    1
  CI                             saved         1

cache-mode reported by the runner: read x1, write x4
```

* `exact`: `Cache hit for: KEY` (or `Cache restored from key:` equal to the step's `key`)
* `partial`: restored through `restore-keys`
* `hit`: a setup-* action restored something (it builds the key itself)
* `miss`: a "not found" line, or a cache step that logged no hit at all
* `failed`: the restore errored (`Restore cache failed: ...`)
* `blocked`: `cache-mode` didn't allow the restore

The runner prints the job's effective cache-mode as `Cache mode: write` during job set-up, and cachehit counts those lines too.

### What's using the cache storage

```console
$ cachehit caches --from examples/caches.json example/repo
example/repo: 11 cache entries, 9.7 GB of 10.0 GB (97%; 10 GB is the default limit, --limit-gb if yours is raised)
  near the limit: GitHub evicts entries once the repo is over it, so new saves push out caches other jobs still need (thrashing)

by ref:
  refs/heads/main                 6     3.9 GB
  refs/pull/412/merge             1     1.2 GB  only re-runs of PR #412 can restore these
  refs/pull/415/merge             1     1.2 GB  only re-runs of PR #415 can restore these
  refs/pull/418/merge             1     1.2 GB  only re-runs of PR #418 can restore these
  refs/pull/421/merge             1     1.2 GB  only re-runs of PR #421 can restore these
  refs/pull/422/merge             1     1.2 GB  only re-runs of PR #422 can restore these
  5 of 11 entries (5.9 GB) are PR-scoped; other PRs and the default branch can't use them

key families with 3+ entries (many entries of one family usually means a key that changes too often):
  tox-Linux-*                                                     5     5.9 GB  on 5 refs
  build-Linux-*                                                   4     3.5 GB  on 1 ref

never restored since saved: 10 of 11 entries (last_accessed_at within 60s of created_at)
  tox-Linux-52e6b438f2a74de4                                      1.2 GB  refs/pull/412/merge
  tox-Linux-269e0d376513270e                                      1.2 GB  refs/pull/415/merge
  tox-Linux-a6a3a4500c5c7fd0                                      1.2 GB  refs/pull/418/merge
  tox-Linux-128b2f33d23f0824                                      1.2 GB  refs/pull/421/merge
  tox-Linux-892f902b1818e811                                      1.2 GB  refs/pull/422/merge
  build-Linux-81e74ef5e8e25d940ed904759531985d5d9dc9f8          900.0 MB  refs/heads/main
  build-Linux-6b0d549b6f03675a1600a35a099950d836f675cc          900.0 MB  refs/heads/main
  build-Linux-6cad4a268d116ece1738f7d93d9c172411e20b8f          900.0 MB  refs/heads/main
  build-Linux-f28c105d1fb17c2390c192cfd3ac94af0f21ddb6          900.0 MB  refs/heads/main
  tools-Linux-1918233445                                         40.0 MB  refs/heads/main
```

"Never restored" means `last_accessed_at` is within 60 seconds of `created_at`. A key family is the key
with long hex/number runs replaced by `*`.

### On real repositories

These two blocks were captured on 2026-11-24 against public repos. They aren't re-run in CI, because
the numbers change with every run.

```text
$ cachehit runs pallets/flask --workflow tests.yaml --limit 3
cachehit: pallets/flask, last 3 completed runs of tests.yaml, 36 job logs read

step                                 jobs    exact     miss  hit-rate
Tests / actions/cache@55cc834           3        2        1       67%
Tests / astral-sh/setup-uv@c771a70     36       22       14       61%

saves:
  Tests                          failed        9  key already taken (another job saved it first, or it exists for this scope)
  Tests                          not-needed   24
  Tests                          saved        15

cache-mode reported by the runner: write x36
```

In these three runs, 9 saves failed with "Unable to reserve cache": the key was already taken, usually because another job saved it first.

```text
$ cachehit caches pydantic/pydantic --top 5
pydantic/pydantic: 243 cache entries, 9.8 GB of 10.0 GB (98%; 10 GB is the default limit, --limit-gb if yours is raised)
  near the limit: GitHub evicts entries once the repo is over it, so new saves push out caches other jobs still need (thrashing)

by ref:
  refs/heads/main               125     1.6 GB
  refs/pull/14358/merge           6   648.3 MB  only re-runs of PR #14358 can restore these
  refs/pull/14351/merge           5   645.6 MB  only re-runs of PR #14351 can restore these
  refs/pull/14367/merge           5   644.7 MB  only re-runs of PR #14367 can restore these
  refs/pull/14368/merge           5   644.3 MB  only re-runs of PR #14368 can restore these
  ... 37 more refs
  118 of 243 entries (8.2 GB) are PR-scoped; other PRs and the default branch can't use them

key families with 3+ entries (many entries of one family usually means a key that changes too often):
  sccache/.sccache_check                                         40      520 B  on 40 refs
  v0-rust-core-bench-Linux-x64-*-*                               22    25.9 KB  on 20 refs
  sccache/e/b/7/*                                                15     1.1 MB  on 15 refs
  sccache/f/0/e/*                                                15    10.1 MB  on 15 refs
  v0-rust-codspeed-profiling-Linux-x64-*-*                       15     8.5 GB  on 15 refs

never restored since saved: 79 of 243 entries (last_accessed_at within 60s of created_at)
  v0-rust-codspeed-profiling-Linux-x64-2fd77104-4a810a17        644.8 MB  refs/pull/14351/merge
  v0-rust-codspeed-profiling-Linux-x64-2fd77104-4a810a17        644.0 MB  refs/pull/14367/merge
  v0-rust-codspeed-profiling-Linux-x64-2fd77104-4a810a17        643.5 MB  refs/pull/14368/merge
  v0-rust-codspeed-profiling-Linux-x64-2fd77104-4a810a17        642.6 MB  refs/pull/14357/merge
  v0-rust-codspeed-profiling-Linux-x64-2fd77104-4a810a17        642.5 MB  refs/pull/14358/merge
```

In this snapshot, most of the storage is PR-scoped profiling caches that hadn't been restored since they were saved.

## What it can't see

* **Static rules don't evaluate expressions.** A key built from `steps.x.outputs...` or `env.X` is
  treated as unknown. CH104 and CH203 match savers to restorers by the literal key prefix before the first
  `${{`, so two different caches with the same prefix count as one.
* **setup-* actions build their keys internally.** For setup-python, setup-node, setup-go, setup-java,
  setup-uv and rust-cache, cachehit only checks triggers, `cache-mode` and `cache-dependency-path`.
  setup-go is treated as caching by default from `@v4`. setup-node's automatic caching (v5, from
  `packageManager`) and setup-uv's `enable-cache: auto` aren't detected unless the input is set explicitly.
* **Reusable workflows.** A workflow whose only trigger is `workflow_call` gets its cache access from the
  caller, so the trigger rules skip it. Composite actions that cache internally aren't followed.
* **Low-trust event list.** The read-only list is GitHub's named triggers (`pull_request_target`,
  `issue_comment`, `workflow_run`) plus the other events whose runs resolve to the default branch
  (`issues`, `label`, `discussion`, `check_run`, ...). Events cachehit doesn't classify (`release`, `create`,
  `merge_group`, `workflow_call`, ...) are left alone.
* **Logs only show what the actions print.** Toolkit-level misses are debug-only, so a cache step that
  logged no hit counts as a miss. Lines from actions that cache with their own wording (other than the
  ones listed above) aren't recognised. Job logs expire with your retention setting.
* **`caches` shows a snapshot.** Entries that were already evicted don't appear. The 10 GB limit is GitHub's
  default; pass `--limit-gb` if your repo's limit was raised.
* **GitHub Enterprise Server** doesn't have `cache-mode`, and the default-branch write restriction differs there.
  The trigger rules assume github.com.

## Development

```sh
pip install -e '.[test]'
python -m pytest
python scripts/check_readme.py          # README examples match real output
python scripts/check_readme.py --update # rewrite them after a change
```

## License

MIT
