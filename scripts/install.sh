#!/usr/bin/env bash
# Install the newest AgentWiz release into a virtual environment.
#
#   curl -fsSL https://github.com/alexchwong/AgentWiz/releases/latest/download/install.sh | bash
#   install.sh [--version X.Y.Z] [--wheel PATH] [--sha256 HEX] [--dir PATH] [--python PATH]
#
# The wheel comes from a GitHub release and is checked against the sha256 that release publishes:
# the asset digest, or the "sha256:" line in the release notes when the digest is absent. This file
# is installer logic only; it is not shipped in the wheel.
set -euo pipefail

REPO="alexchwong/AgentWiz"
API="https://api.github.com/repos/$REPO/releases"
MIN_PYTHON="3.10"

version=""
wheel=""
sha256=""
venv_dir="./.venv"
python_bin="python3"

usage() {
    cat <<'USAGE'
Install AgentWiz from a GitHub release.

  install.sh                     newest release, into ./.venv
  install.sh --version 0.1.0     that release instead of the newest
  install.sh --wheel PATH        a wheel you downloaded yourself

Options:
  --version X.Y.Z  release to install (default: the newest)
  --wheel PATH     install this .whl file instead of downloading one
  --sha256 HEX     expected sha256; also verifies a --wheel file
  --dir PATH       virtual environment to create or update (default: ./.venv)
  --python PATH    interpreter used to create it (default: python3)
  -h, --help       show this message

Re-running the same command upgrades to a newer release.
USAGE
}

die() {
    printf 'install: %s\n' "$1" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version) [ "$#" -ge 2 ] || die "--version needs a value"; version="${2#v}"; shift 2 ;;
        --wheel)   [ "$#" -ge 2 ] || die "--wheel needs a path";   wheel="$2"; shift 2 ;;
        --sha256)  [ "$#" -ge 2 ] || die "--sha256 needs a value"; sha256="$2"; shift 2 ;;
        --dir)     [ "$#" -ge 2 ] || die "--dir needs a path";     venv_dir="$2"; shift 2 ;;
        --python)  [ "$#" -ge 2 ] || die "--python needs a path";  python_bin="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

command -v "$python_bin" >/dev/null 2>&1 || die "$python_bin not found; AgentWiz needs Python $MIN_PYTHON or later"
"$python_bin" -c "import sys; raise SystemExit(0 if sys.version_info >= tuple(int(p) for p in '$MIN_PYTHON'.split('.')) else 1)" >/dev/null 2>&1 \
    || die "$python_bin is older than Python $MIN_PYTHON"

# One release document in, three lines out: download URL, sha256 (may be empty), file name.
read_release() {
    "$python_bin" -c '
import json, re, sys
try:
    release = json.load(sys.stdin)
except ValueError:
    sys.exit(2)
wheels = [a for a in release.get("assets") or [] if str(a.get("name") or "").endswith(".whl")]
if not wheels:
    sys.exit(3)
named = [a for a in wheels if str(a.get("name") or "").startswith("agentwiz-")]
asset = (named or wheels)[0]
digest = str(asset.get("digest") or "")
if digest.startswith("sha256:"):
    digest = digest[len("sha256:"):]
if not re.fullmatch(r"[0-9a-f]{64}", digest):
    found = re.search(r"sha256\s*[:=]\s*`?([0-9a-f]{64})", release.get("body") or "")
    digest = found.group(1) if found else ""
print(asset.get("browser_download_url") or "")
print(digest)
print(asset.get("name") or "")
'
}

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/agentwiz-install.XXXXXX")" || die "cannot create a temporary directory"
trap 'rm -rf "$tmp_dir"' EXIT

wheel_path=""
expected=""

if [ -n "$wheel" ]; then
    [ -f "$wheel" ] || die "no such file: $wheel"
    case "$wheel" in
        *.whl) ;;
        *) die "not a wheel file: $wheel" ;;
    esac
    wheel_path="$wheel"
    expected="$sha256"
else
    command -v curl >/dev/null 2>&1 || die "curl not found; download the wheel and use --wheel"
    if [ -n "$version" ]; then
        release_url="$API/tags/v$version"
        label="v$version"
    else
        release_url="$API/latest"
        label="the newest release"
    fi
    release_json="$(curl -fsSL "$release_url" 2>/dev/null)" || die "cannot read $label from $release_url"
    status=0
    printf '%s' "$release_json" | read_release > "$tmp_dir/release.txt" 2>/dev/null || status=$?
    case "$status" in
        0) ;;
        3) die "$label has no wheel asset" ;;
        *) die "cannot make sense of the release information for $label" ;;
    esac
    wheel_url=""; expected=""; wheel_name=""
    { read -r wheel_url || true; read -r expected || true; read -r wheel_name || true; } < "$tmp_dir/release.txt"
    [ -n "$wheel_url" ] || die "the release information for $label has no download URL"
    wheel_name="${wheel_name:-$(basename "$wheel_url")}"
    wheel_path="$tmp_dir/$wheel_name"
    curl -fsSL --retry 2 -o "$wheel_path" "$wheel_url" 2>/dev/null \
        || die "cannot download $wheel_name from $wheel_url"
    if [ -n "$sha256" ]; then
        expected="$sha256"
    fi
    [ -n "$expected" ] || die "$label publishes no sha256 for $wheel_name; pass --sha256 HEX to install it anyway"
fi

if [ -n "$expected" ]; then
    expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"
    case "$expected" in
        sha256:*) expected="${expected#sha256:}" ;;
    esac
    actual="$("$python_bin" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$wheel_path" 2>/dev/null)" \
        || die "cannot read $wheel_path"
    [ "$actual" = "$expected" ] \
        || die "sha256 mismatch for $(basename "$wheel_path"): expected $expected, got $actual; nothing was installed"
else
    printf 'install: no --sha256 given, installing %s unverified\n' "$(basename "$wheel_path")" >&2
fi

fresh=1
if [ -x "$venv_dir/bin/python" ]; then
    fresh=0
elif [ -d "$venv_dir" ] && [ -n "$(ls -A "$venv_dir")" ]; then
    die "$venv_dir exists and holds no virtual environment; pass another --dir"
fi

if [ "$fresh" = 1 ]; then
    "$python_bin" -m venv "$venv_dir" >/dev/null 2>&1 || die "cannot create a virtual environment in $venv_dir; check Python venv support and directory permissions"
fi
venv_python="$venv_dir/bin/python"

pip_args=(install --disable-pip-version-check)
if [ "$fresh" = 0 ]; then
    pip_args+=(--upgrade)
fi
printf 'Installing AgentWiz and its dependencies...\n'
"$venv_python" -m pip "${pip_args[@]}" "$wheel_path" >/dev/null 2>&1 \
    || die "pip could not install $(basename "$wheel_path"); check network access and the wheel's compatibility"

venv_abs="$(cd "$venv_dir" && pwd)"
printf '\nAgentWiz is installed in %s\n\n' "$venv_abs"
printf 'Launch it with\n\n    %q\n\n' "$venv_abs/bin/agentwiz"
printf 'or activate the environment first\n\n    . %q\n\n' "$venv_abs/bin/activate"
installed_version="$("$venv_dir/bin/agentwiz" --version 2>/dev/null)" || die "the installed AgentWiz does not start"
printf '%s\n' "$installed_version"
