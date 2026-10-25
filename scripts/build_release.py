#!/usr/bin/env python3
# Adapted from modelSpell scripts/build_release.py at commit 1759c6e3c28a637731434a73c46088048583e358
# (MIT, Copyright (c) 2026 Alex Wong), per AgentWiz techspec 11.1.
"""Build and smoke-test the AgentWiz release wheel from committed files.

The wheel is the only release artifact. It is built from `git archive HEAD`, so uncommitted edits
never leak in. Following the testing directive (T2), this script asserts nothing about what any
file contains: wheel membership, metadata and the NEWS section are checked by the manual release
review (DEVEL.md, M1-M4). Usage, from the repository root:

    python scripts/build_release.py [--output-dir dist] [--smoke] [--no-isolation]
    python scripts/build_release.py --changed-since v0.1.0   # exit 1 if shipped files changed
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "agentwiz"
# Files whose change requires a version bump: everything that ends up in the wheel or its metadata.
PAYLOAD = ["agentwiz", "pyproject.toml", "README.md", "LICENSE"]
VERSION_RE = re.compile(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.M)


def fail(message: str) -> None:
    raise SystemExit(f"build_release: {message}")


def git(*args: str, **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, **kw)
    except FileNotFoundError:
        fail("git is required")
    except subprocess.CalledProcessError as exc:
        fail(f"git {' '.join(args)} failed: {exc.stderr.decode(errors='replace').strip()}")


def read_version(tree: Path) -> str:
    """Names the release; not a check of file contents."""
    match = VERSION_RE.search((tree / PACKAGE / "_version.py").read_text(encoding="utf-8"))
    return match.group(1) if match else "unknown"


def export_head(dest: Path) -> None:
    archive = dest / "head.tar"
    git("archive", "--format=tar", f"--output={archive}", "HEAD")
    with tarfile.open(archive) as tar:
        if sys.version_info >= (3, 12) or hasattr(tarfile, "data_filter"):
            tar.extractall(dest / "src", filter="data")
        else:  # Python 3.10.0-3.10.11 lack extraction filters; the archive is our own HEAD
            tar.extractall(dest / "src")


def build_wheel(src: Path, out: Path, isolation: bool) -> Path:
    cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(out)]
    cmd += [] if isolation else ["--no-build-isolation"]
    cmd.append(str(src))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"wheel build failed:\n{result.stdout}{result.stderr}")
    wheels = list(out.glob("*.whl"))
    if len(wheels) != 1:
        fail(f"expected one wheel, found {wheels}")
    return wheels[0]


def smoke(wheel: Path, src: Path, scratch: Path) -> None:
    env_dir = scratch / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    py = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    # --no-index forbids anything from a package index; the pinned modelSpell wheel URL is still fetched.
    subprocess.run([py, "-m", "pip", "install", "-q", "--no-index", str(wheel)], check=True)
    run = subprocess.run([py, "-I", str(src / "scripts" / "smoke_installed.py")], cwd=scratch,
                         capture_output=True, text=True)
    if run.returncode != 0:
        fail(f"smoke test failed:\n{run.stdout}{run.stderr}")
    if str(env_dir) not in run.stdout:
        fail(f"smoke test did not import the installed wheel: {run.stdout}")
    print(run.stdout.strip())


def changed_since(tag: str) -> bool:
    git("rev-parse", "--verify", f"{tag}^{{commit}}")
    diff = subprocess.run(["git", "diff", "--quiet", f"{tag}..HEAD", "--", *PAYLOAD], cwd=ROOT)
    return diff.returncode != 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--smoke", action="store_true", help="install into a fresh venv and run the smoke test")
    parser.add_argument("--no-isolation", action="store_true",
                        help="build with the current environment's hatchling (offline builds)")
    parser.add_argument("--changed-since", metavar="TAG",
                        help="only report whether shipped files changed since TAG (exit 1 if they did)")
    args = parser.parse_args()

    if args.changed_since:
        changed = changed_since(args.changed_since)
        print(f"shipped files {'changed' if changed else 'unchanged'} since {args.changed_since}")
        raise SystemExit(1 if changed else 0)

    if git("status", "--porcelain").stdout.strip():
        print("build_release: note: uncommitted changes are NOT included (building from HEAD)")
    with tempfile.TemporaryDirectory(prefix="agentwiz-release-") as tmp:
        scratch = Path(tmp)
        export_head(scratch)
        src = scratch / "src"
        print(f"building AgentWiz {read_version(src)} from HEAD")
        wheel = build_wheel(src, scratch / "dist", isolation=not args.no_isolation)
        if args.smoke:
            smoke(wheel, src, scratch)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        target = args.output_dir / wheel.name
        shutil.copy2(wheel, target)  # copy, not replace: the temp dir may be on another filesystem
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"built {target}\nsha256 {digest}")


if __name__ == "__main__":
    main()
