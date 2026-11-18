#!/usr/bin/env python3
"""Run page/tutorial checks outside the checkout against an already installed wheel.

Install the candidate wheel and dev dependencies in this interpreter first. No build, publish,
model call or owner interaction is performed here. The repository is never copied as a package.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    with tempfile.TemporaryDirectory(prefix='agentwiz-installed-ui-') as folder:
        destination = Path(folder)
        for name in ('tests', 'scripts'):
            shutil.copytree(ROOT / name, destination / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        result = subprocess.run([sys.executable, '-I', '-c',
                                 'import agentwiz,json;print(json.dumps(agentwiz.__file__))'],
                                cwd=destination, capture_output=True, text=True, check=True)
        package = Path(json.loads(result.stdout)).resolve()
        if 'site-packages' not in package.parts or ROOT in package.parents:
            raise RuntimeError('page checks must import the installed candidate wheel, not the checkout')
        print('Installed UI package:', package, flush=True)
        return subprocess.run([sys.executable, '-I', '-m', 'pytest', '-q',
                               'tests/test_page.py', 'tests/test_page_library.py', 'tests/test_tutorial.py'],
                              cwd=destination).returncode


if __name__ == '__main__':
    raise SystemExit(main())
