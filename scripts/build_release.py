#!/usr/bin/env python3
# Adapted from modelSpell scripts/build_release.py at commit 1759c6e3c28a637731434a73c46088048583e358
# (MIT, Copyright (c) 2026 Alex Wong), per AgentWiz techspec 11.1 (0.1.0), 11.4 (0.1.1) and 13.1 (0.1.2).
"""Build, check and smoke-test the AgentWiz release wheel from committed files.

The wheel is the only release artifact. It is built from `git archive HEAD`, so uncommitted edits
never leak in. Every build then runs the release checks, before any smoke test, reading only the
archived HEAD tree and the wheel:

    RC1  NEWS.md has exactly one `## <version>` section, with bullets of at most 20 words
    RC2  the wheel's name and members (agentwiz/** and the five .dist-info files)
    RC3  the wheel's METADATA: name, version, license, and the one pinned modelSpell dependency

These are release tooling, not tests: the owner's exception to the testing directive T2 (techspec
0.1.1, Section 11.4). Nothing else about any file's contents is asserted. A failed check prints its
reason, copies nothing to --output-dir, and exits non-zero. Usage, from the repository root:

    python scripts/build_release.py [--output-dir dist] [--smoke] [--no-isolation]
    python scripts/build_release.py --smoke --no-isolation --modelspell-wheel PATH   # offline (0.1.2, 13.1)
    python scripts/build_release.py --changed-since v0.1.0   # exit 1 if shipped files changed (no build)

--modelspell-wheel names a local copy of the pinned modelSpell wheel. Its file name must be the pinned
URL's last path component and its sha256 the pin's; the smoke then installs both wheels with
`pip install --no-index --no-deps` and runs `pip check`, so nothing is fetched. Without it, the smoke
installs the AgentWiz wheel as before, and pip fetches the pinned modelSpell URL.
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
import zipfile
from email.parser import HeaderParser
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


# --- release checks (techspec 11.4) ------------------------------------------------------------
DIST_INFO_FILES = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "licenses/LICENSE"}
DIRECT_DEP_RE = re.compile(r'"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*@\s*([^"\s;]+)\s*"')
MODELSPELL_URL_RE = re.compile(r"https://github\.com/alexchwong/modelSpell/releases/download/v([0-9][0-9A-Za-z.+-]*)/"
                               r"modelspell-\1-py3-none-any\.whl#sha256=[0-9a-f]{64}\Z")


def normalise(name: str) -> str:
    """The standard project-name normalisation: lowercase, runs of '.', '_' or '-' become '-'."""
    return re.sub(r"[-_.]+", "-", name).lower()


def check_news(src: Path, version: str) -> list[str]:
    """RC1. Whether each bullet is user-visible stays a human judgement."""
    lines = (src / "NEWS.md").read_text(encoding="utf-8").splitlines()
    heading = f"## {version}"
    found = [i for i, line in enumerate(lines) if line == heading]
    if len(found) != 1:
        return [f"NEWS.md has {len(found)} lines equal to '{heading}' (exactly one required)"]
    start = found[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    bullets = [(i + 1, lines[i][2:]) for i in range(start, end) if lines[i].startswith("- ")]
    if not bullets:
        return [f"NEWS.md section '{heading}' has no bullet ('- ')"]
    return [f"NEWS.md line {n}: bullet has {len(text.split())} words (at most 20)"
            for n, text in bullets if len(text.split()) > 20]


def check_members(wheel: Path, version: str) -> list[str]:
    """RC2. New modules under agentwiz/ never need this changed."""
    problems = []
    expected = f"agentwiz-{version}-py3-none-any.whl"
    if wheel.name != expected:
        problems.append(f"wheel is named {wheel.name}, expected {expected}")
    dist_info = f"agentwiz-{version}.dist-info/"
    with zipfile.ZipFile(wheel) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
    for name in names:
        if not (name.startswith("agentwiz/") or name.startswith(dist_info)):
            problems.append(f"unexpected wheel member {name}")
    if "agentwiz/ui/index.html" not in names:
        problems.append("wheel member agentwiz/ui/index.html is missing")
    info = {n[len(dist_info):] for n in names if n.startswith(dist_info)}
    problems += [f"wheel member {dist_info}{n} is missing" for n in sorted(DIST_INFO_FILES - info)]
    problems += [f"unexpected wheel member {dist_info}{n}" for n in sorted(info - DIST_INFO_FILES)]
    return problems


def check_metadata(src: Path, wheel: Path, version: str) -> list[str]:
    """RC3. The dependency's name is compared normalised (Hatchling lowercases it); its URL exactly."""
    problems = []
    with zipfile.ZipFile(wheel) as zf:
        try:
            raw = zf.read(f"agentwiz-{version}.dist-info/METADATA").decode("utf-8")
        except KeyError:
            return ["METADATA is missing"]
    meta = HeaderParser().parsestr(raw)
    for field, wanted in (("Name", "AgentWiz"), ("Version", version), ("License-Expression", "MIT")):
        if meta.get_all(field) != [wanted]:
            problems.append(f"METADATA field {field} is {meta.get_all(field)}, expected {wanted!r}")
    runtime = [r.strip() for r in meta.get_all("Requires-Dist") or [] if "extra ==" not in r]
    pyproject = (src / "pyproject.toml").read_text(encoding="utf-8")
    declared = DIRECT_DEP_RE.findall(pyproject)
    if len(declared) != 1:
        problems.append(f"pyproject.toml has {len(declared)} direct-URL dependencies (exactly one required)")
    if len(runtime) != 1:
        problems.append(f"METADATA has {len(runtime)} runtime Requires-Dist lines (exactly one required): "
                        f"{runtime}")
    if problems:
        return problems
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*) @ (\S+)", runtime[0])
    if not match:
        return [f"METADATA Requires-Dist is not of the form '<name> @ <url>': {runtime[0]}"]
    (dep_name, dep_url), (meta_name, meta_url) = declared[0], match.groups()
    for where, name in (("pyproject.toml", dep_name), ("METADATA", meta_name)):
        if normalise(name) != "modelspell":
            problems.append(f"{where} dependency name {name} does not normalise to modelspell")
    if meta_url != dep_url:
        problems.append("METADATA Requires-Dist URL differs from pyproject.toml's "
                        f"({meta_url} != {dep_url})")
    if not MODELSPELL_URL_RE.match(dep_url):
        problems.append(f"dependency URL is not a pinned modelSpell release wheel with sha256: {dep_url}")
    return problems


def release_checks(src: Path, wheel: Path) -> bool:
    """Run RC1-RC3 (all of them, whatever fails first); print each result; True if all passed."""
    version = read_version(src)
    ok = True
    for name, problems in (("RC1", check_news(src, version)), ("RC2", check_members(wheel, version)),
                           ("RC3", check_metadata(src, wheel, version))):
        if problems:
            ok = False
            for problem in problems:
                print(f"{name} FAILED: {problem}")
        else:
            print(f"{name} ok")
    return ok


def export_head(dest: Path) -> None:
    archive = dest / "head.tar"
    git("archive", "--format=tar", f"--output={archive}", "HEAD")
    with tarfile.open(archive) as tar:
        if sys.version_info >= (3, 12) or hasattr(tarfile, "data_filter"):
            tar.extractall(dest / "src", filter="data")
        else:  # Python 3.10.0-3.10.11 lack extraction filters; the archive is our own HEAD
            tar.extractall(dest / "src")


def pinned_modelspell(src: Path) -> tuple[str, str]:
    """(file name, sha256) of the modelSpell wheel pinned in the archived pyproject.toml, as RC3 reads it."""
    declared = DIRECT_DEP_RE.findall((src / "pyproject.toml").read_text(encoding="utf-8"))
    urls = [url for name, url in declared if normalise(name) == "modelspell"]
    if len(urls) != 1 or "#sha256=" not in urls[0]:
        fail("pyproject.toml does not pin exactly one modelSpell wheel URL with a sha256")
    url, digest = urls[0].split("#sha256=", 1)
    return url.rsplit("/", 1)[1], digest


def check_local_wheel(src: Path, wheel: Path) -> None:
    """Refuse a local modelSpell wheel that is not the pinned one (name and sha256)."""
    name, digest = pinned_modelspell(src)
    if not wheel.is_file():
        fail(f"--modelspell-wheel {wheel} is not a file")
    if wheel.name != name:
        fail(f"--modelspell-wheel must be named {name} (the pinned URL's file name), not {wheel.name}")
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if actual != digest:
        fail(f"--modelspell-wheel has sha256 {actual}; the pin is {digest}")


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


def smoke(wheel: Path, src: Path, scratch: Path, modelspell_wheel: Path | None = None) -> None:
    env_dir = scratch / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    py = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if modelspell_wheel is None:
        # --no-index forbids anything from a package index; the pinned modelSpell wheel URL is still fetched.
        subprocess.run([py, "-m", "pip", "install", "-q", "--no-index", str(wheel)], check=True)
    else:
        # Offline (13.1): both local wheels, no dependency resolved remotely, then pip check.
        subprocess.run([py, "-m", "pip", "install", "-q", "--no-index", "--no-deps", str(wheel),
                        str(modelspell_wheel)], check=True)
        checked = subprocess.run([py, "-m", "pip", "check"], capture_output=True, text=True)
        if checked.returncode != 0:
            fail(f"pip check failed:\n{checked.stdout}{checked.stderr}")
        print("pip check ok")
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
    parser.add_argument("--smoke", action="store_true",
                        help="after the release checks, install into a fresh venv and run the smoke test")
    parser.add_argument("--no-isolation", action="store_true",
                        help="build with the current environment's hatchling (offline builds)")
    parser.add_argument("--modelspell-wheel", type=Path, metavar="PATH",
                        help="with --smoke: install this local copy of the pinned modelSpell wheel (offline)")
    parser.add_argument("--changed-since", metavar="TAG",
                        help="only report whether shipped files changed since TAG (exit 1 if they did)")
    args = parser.parse_args()

    if args.changed_since:
        changed = changed_since(args.changed_since)
        print(f"shipped files {'changed' if changed else 'unchanged'} since {args.changed_since}")
        raise SystemExit(1 if changed else 0)

    if args.modelspell_wheel is not None and not args.smoke:
        parser.error("--modelspell-wheel requires --smoke")
    if args.no_isolation:
        probe = subprocess.run([sys.executable, "-c", "import hatchling"], capture_output=True)
        if probe.returncode != 0:
            fail("--no-isolation needs hatchling installed in this interpreter "
                 "(python -m pip install 'hatchling>=1.27')")
    if git("status", "--porcelain").stdout.strip():
        print("build_release: note: uncommitted changes are NOT included (building from HEAD)")
    with tempfile.TemporaryDirectory(prefix="agentwiz-release-") as tmp:
        scratch = Path(tmp)
        export_head(scratch)
        src = scratch / "src"
        local = args.modelspell_wheel.resolve() if args.modelspell_wheel is not None else None
        if local is not None:
            check_local_wheel(src, local)
        print(f"building AgentWiz {read_version(src)} from HEAD")
        wheel = build_wheel(src, scratch / "dist", isolation=not args.no_isolation)
        if not release_checks(src, wheel):
            fail("release checks failed; nothing was copied to the output directory")
        if args.smoke:
            smoke(wheel, src, scratch, local)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        target = args.output_dir / wheel.name
        shutil.copy2(wheel, target)  # copy, not replace: the temp dir may be on another filesystem
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"built {target}\nsha256 {digest}")


if __name__ == "__main__":
    main()
