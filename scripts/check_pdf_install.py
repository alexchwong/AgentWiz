#!/usr/bin/env python3
"""Check native PDF extraction and installed licence notices; no model/network calls.

CI uses: python -I scripts/check_pdf_install.py --require-installed
A WSL2 checkout may use: python scripts/check_pdf_install.py
"""
from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path
import platform
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-installed', action='store_true')
    args = parser.parse_args()
    import agentwiz
    from agentwiz import pdf_reader
    from agentwiz.tutorial_support import pdf_fixtures

    package = Path(agentwiz.__file__).resolve()
    if args.require_installed and 'site-packages' not in package.parts:
        raise RuntimeError('PDF check must import the installed wheel')
    dist = importlib.metadata.distribution('pypdfium2')
    notices = [entry for entry in dist.files or () if 'license' in str(entry).lower()]
    if not notices or not any('build_licenses' in str(entry).lower() for entry in notices):
        raise RuntimeError('PDFium third-party licence notices are absent')
    if not any('/licenses/' in str(entry).lower() and 'build_licenses' not in str(entry).lower()
               for entry in notices):
        raise RuntimeError('pypdfium2 licence notices are absent')
    if any(not dist.locate_file(entry).is_file() or dist.locate_file(entry).stat().st_size == 0
           for entry in notices):
        raise RuntimeError('installed licence notice is missing or empty')
    for name, pages in (('text', 1), ('multi_page', 3), ('partial', 3), ('no_text', 2), ('near_limit', 300)):
        result = pdf_reader.extract(getattr(pdf_fixtures, name)(), filename='fixture.pdf')
        if result['page_count'] != pages:
            raise RuntimeError('PDF fixture page count mismatch: ' + name)
        if name == 'text' and 'Order 007' not in pdf_reader.render(result)['text']:
            raise RuntimeError('PDF text extraction failed')
        if name == 'partial' and [block['page_start'] for block in result['blocks']] != [1, 3]:
            raise RuntimeError('partial PDF coverage mismatch')
        if name == 'no_text' and (result['blocks'] or not result['warnings']):
            raise RuntimeError('image-only PDF coverage mismatch')
        print('PDF fixture ok:', name)
    print('PDF install ok:', platform.system(), platform.machine(), platform.python_version(),
          'pypdfium2=' + dist.version, 'licence_notices=' + str(len(notices)), 'agentwiz=' + str(package))
    if sys.platform == 'darwin':
        print('Limitation: the macOS PDF worker has no memory containment; this check does not establish it.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
