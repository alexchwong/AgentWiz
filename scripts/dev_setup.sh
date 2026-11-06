#!/usr/bin/env bash
# Developer setup for an AgentWiz checkout: ./.venv with AgentWiz installed editable, pytest, the JSON
# Schema checks and the page checks' browser (techspec 0.1.3, 16.3).
#
#   scripts/dev_setup.sh [--python PATH]
#
# Run from the checkout root. Creates ./.venv if it does not exist, then runs
# `python -m pip install -e '.[dev]'` and `python -m playwright install chromium`. Also converts a .venv made by install.sh (wheel only, no
# pytest) into the developer setup. This file is not shipped in the wheel.
set -euo pipefail

python_bin="python3"

usage() {
    cat <<'USAGE'
Set up ./.venv for AgentWiz development (editable install, pytest, JSON Schema checks and Chromium).

  scripts/dev_setup.sh [--python PATH]

Options:
  --python PATH    interpreter used to create ./.venv (default: python3)
  -h, --help       show this message

Run from the root of an AgentWiz checkout.
USAGE
}

die() {
    printf 'dev_setup: %s\n' "$1" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --python)  [ "$#" -ge 2 ] || die "--python needs a path"; python_bin="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

[ -f pyproject.toml ] && grep -Eq '^name *= *"AgentWiz"' pyproject.toml \
    || die "run this from the root of an AgentWiz checkout (no pyproject.toml naming AgentWiz here)"

if [ ! -x ./.venv/bin/python ]; then
    if [ -e ./.venv ]; then
        die "./.venv exists but holds no virtual environment; move it aside and run this again"
    fi
    command -v "$python_bin" >/dev/null 2>&1 || die "$python_bin not found; AgentWiz needs Python 3.10 or later"
    printf 'Creating ./.venv with %s\n' "$python_bin"
    "$python_bin" -m venv ./.venv || die "cannot create ./.venv with $python_bin"
fi

printf 'Installing AgentWiz (editable) and the test dependencies into ./.venv\n'
./.venv/bin/python -m pip install -e '.[dev]' || die "pip install -e '.[dev]' failed (see pip's output above)"

# The page checks drive headless Chromium. Where PLAYWRIGHT_BROWSERS_PATH already holds the build that
# Playwright expects, nothing is downloaded.
printf 'Installing Chromium for the page checks\n'
./.venv/bin/python -m playwright install chromium || die "the Chromium download failed (see the output above); retry with: ./.venv/bin/python -m playwright install chromium. Without it the page checks (tests/test_page.py) cannot run, and the test gate stays blocked."

cat <<'NEXT'

Developer setup ready. Next:

    . .venv/bin/activate
    python -m pytest -m "not live"      # offline suite, page checks included
    python -m pytest -m live -s         # optional live checks; variables in DEVEL.md, Tests
    agentwiz --no-browser               # run the UI from this checkout
NEXT
