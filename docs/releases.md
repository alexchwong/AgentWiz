# Release operations

For the repository owner or a maintainer explicitly authorized to release. This document is
not permission for a coding agent to commit, push, merge, tag or publish. Agent requirements
and documentation placement are governed by `../DEVEL.md`.

## Validation and optional review

Release gates are the offline CI matrix and the release tooling's checks and installed-wheel
smoke. Real-backend live checks and walking the tutorial are optional, not sign-off gates.
The live-run quickstart is in `../DEVEL.md`.

Subjective UI review covers presentation (readability, spacing, clipping/overlap), clarity of
guidance/errors/status and discoverability/understandability of interactions. Use the built-in
sandbox tutorial with tips and prepared inputs; no manual setup runbook, screenshots, evidence
dossier or acknowledgement is required. Objective regression checks belong in automation.
Historical owner dispositions are recorded in `decisions.md`, not as current test results.

## Installed-wheel smoke

Run from the repository root in the activated checkout `.venv`:

```bash
python scripts/build_release.py --smoke
```

The script builds committed `HEAD`, not the working tree. For uncommitted work, transfer changes
to a throwaway clone and commit only there. Do not commit the implementation checkout merely
to run the smoke. Prepare hatchling and the hash-checked pinned modelSpell wheel before an
offline smoke; do not rely on a warm pip cache as proof of preparation.

```bash
python scripts/build_release.py --smoke --no-isolation --modelspell-wheel ../agentwiz-prep/wheels/modelspell-0.3.0-py3-none-any.whl --dependency-wheels ../agentwiz-prep/wheels
```

`--modelspell-wheel` refuses a file whose name is not the pinned URL's file name or whose sha256 is
not the pin; the smoke then installs both wheels with `pip install --no-index --no-deps` and runs
`pip check`. Prepare `jsonschema>=4.26,<5` and its transitive wheels in the directory supplied
with `--dependency-wheels`; they are installed with `--no-index --find-links`. Without that
option the validator dependencies are installed from the package index. Without a local modelSpell
wheel pip fetches the pinned URL. A warm pip cache
can hide a missing prerequisite, so it is not preparation.

## Branches and releases

Work happens on `devel`; `main` only receives merges from `devel`. `tests.yml` runs on pushes to
both and on pull requests (Ubuntu and macOS × Python 3.10 and 3.13).

`release.yml` runs on every push to `main`. It reruns the tests, then, from the commit that
passed them (`github.sha`, never a re-read of `main`):

- **If `vX.Y.Z` is not yet released:** builds the wheel from that commit, runs the release checks
  RC1–RC3 on it, installs it into a fresh venv with `--no-index` (the pinned modelSpell wheel is
  still fetched) and runs the smoke check, then creates release `vX.Y.Z` tagged at that commit, with
  the wheel, the NEWS section as notes and a pinned install line with the sha256. A failed check
  stops the job before anything is published.
- **If `vX.Y.Z` already exists:** fails if any shipped file changed since the tag; otherwise skips
  building and publishing the wheel.
- **Finally, once the release is there** (new, or unchanged with the guard passing): re-attaches
  `scripts/install.sh` to `vX.Y.Z` with `--clobber`, so `releases/latest/download/install.sh`
  always serves the current installer without a new release.

**Shipped-file rule (defined in `../DEVEL.md`).** Any change to `agentwiz/**` (Python, HTML, CSS or
JavaScript), `pyproject.toml`, `README.md` or `LICENSE` needs a version bump and a NEWS entry before
a merge into `main`. Changes to tests, scripts, workflows, `DEVEL.md`, `docs/**` and `.clinerules` merge without
a release. The rule is enforced by `release.yml` (`build_release.py --changed-since <tag>`), so
`devel` may carry shipped-file edits without a bump; the run for that merge fails if the bump is
missing, and no release is published.

**Release checks.** `build_release.py` runs these on every build (not with `--changed-since`),
after building and before any smoke test, reading only the archived `HEAD` tree and the wheel.
They are release tooling under the owner's exception to T2, not tests:

| Check | Passes when |
| --- | --- |
| RC1 | `NEWS.md` has exactly one line `## <version>`; its section has at least one `- ` bullet, each of at most 20 words. |
| RC2 | The wheel is `agentwiz-<version>-py3-none-any.whl`; every member is under `agentwiz/` or `agentwiz-<version>.dist-info/`; `agentwiz/ui/index.html` is present; the `.dist-info` members are exactly `METADATA`, `WHEEL`, `RECORD`, `entry_points.txt`, `licenses/LICENSE`. |
| RC3 | `METADATA` has `Name: AgentWiz`, `Version: <version>`, `License-Expression: MIT`, the required `jsonschema>=4.26,<5`, and exactly one other runtime dependency: modelSpell's direct URL, identical to the pinned release-wheel URL and sha256 in `pyproject.toml`. |

Each prints `RCn ok` or `RCn FAILED: <reason>`; all three run; on any failure nothing is copied to
`--output-dir` and the script exits non-zero. Whether NEWS bullets are user-visible stays a human
judgement.

### Release checklist

1. On `devel`, bump `agentwiz/_version.py` for shipped changes, add the version section to
   `NEWS.md` and update user documentation as needed. Commit and push when authorized.
2. Confirm the offline CI matrix passes on Ubuntu and macOS, Python 3.10 and 3.13. A Linux-only
   local pass does not verify macOS. Optional subjective review/live checks are not gates.
3. Run `python scripts/build_release.py --smoke` on the intended committed tree; require
   RC1–RC3 and the installed-wheel smoke S1–S15 to pass.
4. Merge `devel` into `main` when authorized. The release workflow reruns tests and checks
   before publishing; inspect its result.

NEWS authoring and version-bump requirements are defined in `../DEVEL.md`.

Release 0.2.0 also runs S15 in the installed smoke. It creates a captured
Workshop script starter, publishes it through a bound plan, runs it through the
normal coordinator, checks the returned count and verifies duplicate Start identity.
This exercises packaged starter runtime/license resources without requiring the
model backend. Validate the final installed UI using `tests/test_page.py` and `tests/test_tutorial.py`
against an environment where the built wheel replaces the editable package and the
existing dev dependencies are installed. Use `python -I -m pytest` from outside
the checkout and verify AgentWiz imports from that environment's site-packages. This
runs the completion journey, narrow/desktop authoring, retained utilities and all
fourteen tutorial paths against the installed package. These are objective automated
checks; tutorial walking remains optional.
