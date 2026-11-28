#!/usr/bin/env python3
# Adapted from modelSpell scripts/build_release.py at commit 1759c6e3c28a637731434a73c46088048583e358
# (MIT, Copyright (c) 2026 Alex Wong), per AgentWiz techspec 11.1 (0.1.0), 11.4 (0.1.1) and 13.1 (0.1.2).
"""Build, check and smoke-test the AgentWiz release wheel from committed files.

The wheel is the only release artifact. It is built from `git archive HEAD`, so uncommitted edits
never leak in. Every build then runs the release checks, before any smoke test, reading only the
archived HEAD tree and the wheel:

    RC1  NEWS.md has exactly one `## <version>` section, with bullets of at most 20 words
    RC2  the wheel's name and members (agentwiz/** and the five .dist-info files), including the PDF reader and
         worker, and every starter-library entry that agentwiz/library/index.json lists
    RC3  the wheel's METADATA: name, version, license, required jsonschema and pypdfium2, and exactly the two
         pinned direct-URL dependencies (modelSpell and scriptSpell)

These are release tooling, not tests: the owner's exception to the testing directive T2 (techspec
0.1.1, Section 11.4). Nothing else about any file's contents is asserted. A failed check prints its
reason, copies nothing to --output-dir, and exits non-zero. Usage, from the repository root:

    python scripts/build_release.py [--output-dir dist] [--smoke] [--no-isolation]
    python scripts/build_release.py --smoke --no-isolation --modelspell-wheel PATH \
        --scriptspell-wheel PATH --dependency-wheels DIRECTORY                     # offline (0.2.2, 3)
    python scripts/build_release.py --changed-since v0.1.0   # exit 1 if shipped files changed (no build)

--modelspell-wheel and --scriptspell-wheel name local copies of the two pinned wheels; they are given together.
Each file name must be the pinned URL's last path component and each sha256 the pin's. The offline smoke
(0.2.2, section 3) then installs jsonschema, PyYAML, pydantic-monty, pypdfium2 and their transitive requirements from
--dependency-wheels with `--no-index --find-links`, installs the AgentWiz, modelSpell and scriptSpell wheels
together with `pip install --no-index --no-deps` (pip would otherwise follow the direct URLs in AgentWiz's
metadata) and runs `pip check`, so nothing is fetched. Without them, the smoke installs the same four
requirements from the index first, then the AgentWiz wheel with `--no-index`, so only the two pinned URLs are
fetched, and runs `pip check`.
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
SCRIPTSPELL_URL_RE = re.compile(r"https://github\.com/alexchwong/scriptSpell/releases/download/v([0-9][0-9A-Za-z.+-]*)/"
                                r"scriptspell-\1-py3-none-any\.whl#sha256=[0-9a-f]{64}\Z")
PINNED = {"modelspell": MODELSPELL_URL_RE, "scriptspell": SCRIPTSPELL_URL_RE}
# The transitive requirements the two pinned wheels declare, with exactly their constraints (0.2.2, section 3).
TRANSITIVE = ("jsonschema>=4.26,<5", "PyYAML>=6,<7", "pydantic-monty==1.5.0")
# AgentWiz's own non-URL requirement besides jsonschema (addendum 4.3); the smoke preloads it with the above.
PDF_REQUIREMENT = "pypdfium2>=5.13,<6"
PRELOAD = (*TRANSITIVE, PDF_REQUIREMENT)
REQUIRED_MEMBERS = ("agentwiz/ui/index.html", "agentwiz/model-servers.ini.template", "agentwiz/pdf_reader.py",
                    "agentwiz/pdf_worker.py", "agentwiz/tutorial_support/pdf_fixtures.py",
                    "agentwiz/library/index.json", "agentwiz/ui/company.js",
                    "agentwiz/company_assets/manifest.json", "agentwiz/company_assets/example.txt",
                    "agentwiz/tutorial_support/company_trial.json")


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
    for member in REQUIRED_MEMBERS:
        if member not in names:
            problems.append(f"wheel member {member} is missing")
    if "agentwiz/library/index.json" in names:
        problems += check_library_members(wheel, set(names))
    if "agentwiz/company_assets/manifest.json" in names:
        import hashlib
        import json
        with zipfile.ZipFile(wheel) as zf:
            prefix = "agentwiz/company_assets/"
            manifest = json.loads(zf.read(prefix + "manifest.json"))
            if len({row["id"] for row in manifest["presets"]}) != 10:
                problems.append("company catalogue must contain ten distinct presets")
            for relative, digest in manifest["files"].items():
                name = prefix + relative
                if name not in names or hashlib.sha256(zf.read(name)).hexdigest() != digest:
                    problems.append(f"invalid packaged company asset {name}")
    info = {n[len(dist_info):] for n in names if n.startswith(dist_info)}
    problems += [f"wheel member {dist_info}{n} is missing" for n in sorted(DIST_INFO_FILES - info)]
    problems += [f"unexpected wheel member {dist_info}{n}" for n in sorted(info - DIST_INFO_FILES)]
    return problems


def check_library_members(wheel: Path, names: set[str]) -> list[str]:
    """RC2, the starter library (addendum 5.1): every key the index lists ships its block.json and library.json,
    and a model entry its two prompt files."""
    import json
    with zipfile.ZipFile(wheel) as zf:
        try:
            index = json.loads(zf.read("agentwiz/library/index.json").decode("utf-8"))
            keys = index["keys"]
        except (KeyError, ValueError, TypeError):
            return ["wheel member agentwiz/library/index.json is not a readable library index"]
        problems = []
        for key in keys:
            folder = f"agentwiz/library/{key}/"
            for member in ("block.json", "library.json"):
                if folder + member not in names:
                    problems.append(f"wheel member {folder}{member} is missing")
            try:
                entry = json.loads(zf.read(folder + "library.json").decode("utf-8"))
            except (KeyError, ValueError):
                continue
            if entry.get("group") == "llm":
                problems += [f"wheel member {folder}{p} is missing" for p in ("prompts/system.md", "prompts/user.md")
                             if folder + p not in names]
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
    validators = [r for r in runtime if re.fullmatch(r'jsonschema\s*(?:>=4\.26,<5|<5,>=4\.26)', r)]
    if len(validators) != 1:
        problems.append('METADATA must require exactly jsonschema>=4.26,<5')
    readers = [r for r in runtime if re.fullmatch(r'pypdfium2\s*(?:>=5\.13,<6|<6,>=5\.13)', r)]
    if len(readers) != 1:
        problems.append('METADATA must require exactly pypdfium2>=5.13,<6')
    runtime = [r for r in runtime if r not in validators and r not in readers]
    pyproject = (src / "pyproject.toml").read_text(encoding="utf-8")
    declared = DIRECT_DEP_RE.findall(pyproject)
    if len(declared) != 2:
        problems.append(f"pyproject.toml has {len(declared)} direct-URL dependencies (exactly two required: "
                        "modelSpell and scriptSpell)")
    if len(runtime) != 2:
        problems.append(f"METADATA has {len(runtime)} other runtime Requires-Dist lines (exactly modelSpell and "
                        f"scriptSpell required): {runtime}")
    if problems:
        return problems
    found = {}
    for line in runtime:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*) @ (\S+)", line)
        if not match:
            return [f"METADATA Requires-Dist is not of the form '<name> @ <url>': {line}"]
        found.setdefault(normalise(match.group(1)), []).append(("METADATA", match.group(2)))
    for name, url in declared:
        found.setdefault(normalise(name), []).append(("pyproject.toml", url))
    for name, pattern in PINNED.items():
        rows = found.pop(name, [])
        where = {w for w, _url in rows}
        if where != {"METADATA", "pyproject.toml"} or len(rows) != 2:
            problems.append(f"{name} must be pinned exactly once in pyproject.toml and once in METADATA")
            continue
        urls = {w: url for w, url in rows}
        if urls["METADATA"] != urls["pyproject.toml"]:
            problems.append(f"METADATA Requires-Dist URL for {name} differs from pyproject.toml's "
                            f"({urls['METADATA']} != {urls['pyproject.toml']})")
        if not pattern.match(urls["pyproject.toml"]):
            problems.append(f"dependency URL is not a pinned {name} release wheel with sha256: {urls['pyproject.toml']}")
    for name in sorted(found):
        problems.append(f"dependency name {name} is neither modelspell nor scriptspell")
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


def pinned_wheel(src: Path, project: str) -> tuple[str, str]:
    """(file name, sha256) of the `project` wheel pinned in the archived pyproject.toml, as RC3 reads it."""
    declared = DIRECT_DEP_RE.findall((src / "pyproject.toml").read_text(encoding="utf-8"))
    urls = [url for name, url in declared if normalise(name) == project]
    if len(urls) != 1 or "#sha256=" not in urls[0]:
        fail(f"pyproject.toml does not pin exactly one {project} wheel URL with a sha256")
    url, digest = urls[0].split("#sha256=", 1)
    return url.rsplit("/", 1)[1], digest


def check_local_wheel(src: Path, wheel: Path, project: str = "modelspell") -> None:
    """Refuse a local wheel that is not the pinned one (name and sha256)."""
    name, digest = pinned_wheel(src, project)
    option = f"--{project}-wheel"
    if not wheel.is_file():
        fail(f"{option} {wheel} is not a file")
    if wheel.name != name:
        fail(f"{option} must be named {name} (the pinned URL's file name), not {wheel.name}")
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if actual != digest:
        fail(f"{option} has sha256 {actual}; the pin is {digest}")


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


def smoke(wheel: Path, src: Path, scratch: Path, modelspell_wheel: Path | None = None,
          dependency_wheels: Path | None = None, scriptspell_wheel: Path | None = None) -> None:
    env_dir = scratch / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    py = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    # 1. The transitive requirements, resolved apart from the two pinned direct URLs: from the index online,
    #    from the platform-matched wheelhouse offline. A missing wheel fails here; nothing is skipped.
    first = [str(py), '-m', 'pip', 'install', '-q']
    if dependency_wheels is not None:
        first.extend(['--no-index', '--find-links', str(dependency_wheels)])
    subprocess.run([*first, *PRELOAD], check=True)
    if modelspell_wheel is None:
        # --no-index forbids anything from a package index; only the two pinned wheel URLs are fetched.
        subprocess.run([py, "-m", "pip", "install", "-q", "--no-index", str(wheel)], check=True)
    else:
        # 2. was done by the caller (check_local_wheel on both). 3. All three wheels together, --no-deps:
        #    pip would otherwise follow the direct URLs in AgentWiz's metadata even with the wheels as arguments.
        subprocess.run([py, "-m", "pip", "install", "-q", "--no-index", "--no-deps", str(wheel),
                        str(modelspell_wheel), str(scriptspell_wheel)], check=True)
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
    parser.add_argument("--scriptspell-wheel", type=Path, metavar="PATH",
                        help="with --smoke and --modelspell-wheel: the local copy of the pinned scriptSpell wheel")
    parser.add_argument('--dependency-wheels', type=Path, metavar='DIRECTORY',
                        help='with --smoke: offline wheelhouse for jsonschema, PyYAML, pydantic-monty, pypdfium2 '
                             'and their dependencies (platform-matched)')
    parser.add_argument("--changed-since", metavar="TAG",
                        help="only report whether shipped files changed since TAG (exit 1 if they did)")
    args = parser.parse_args()

    if args.changed_since:
        changed = changed_since(args.changed_since)
        print(f"shipped files {'changed' if changed else 'unchanged'} since {args.changed_since}")
        raise SystemExit(1 if changed else 0)

    if args.modelspell_wheel is not None and not args.smoke:
        parser.error("--modelspell-wheel requires --smoke")
    if args.dependency_wheels is not None and not args.smoke:
        parser.error('--dependency-wheels requires --smoke')
    if (args.modelspell_wheel is None) != (args.scriptspell_wheel is None):
        parser.error("--modelspell-wheel and --scriptspell-wheel are given together (offline smoke)")
    if args.modelspell_wheel is not None and args.dependency_wheels is None:
        parser.error("the offline smoke (--modelspell-wheel) needs --dependency-wheels")
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
        local_scripts = args.scriptspell_wheel.resolve() if args.scriptspell_wheel is not None else None
        if local is not None:
            check_local_wheel(src, local, "modelspell")
            check_local_wheel(src, local_scripts, "scriptspell")
        print(f"building AgentWiz {read_version(src)} from HEAD")
        wheel = build_wheel(src, scratch / "dist", isolation=not args.no_isolation)
        if not release_checks(src, wheel):
            fail("release checks failed; nothing was copied to the output directory")
        if args.smoke:
            smoke(wheel, src, scratch, local,
                  args.dependency_wheels.resolve() if args.dependency_wheels is not None else None, local_scripts)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        target = args.output_dir / wheel.name
        shutil.copy2(wheel, target)  # copy, not replace: the temp dir may be on another filesystem
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"built {target}\nsha256 {digest}")


if __name__ == "__main__":
    main()
