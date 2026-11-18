"""The PDF worker: the child-process entry point and the only module that imports `pypdfium2` (addendum 4.2).

Launched by `pdf_reader` as `[sys.executable, "-I", "-m", "agentwiz.pdf_worker"]` with a minimal environment, a
fresh temporary directory as its working directory and no arguments derived from the file. It reads the PDF's
bytes from stdin and writes one JSON object to stdout: a document result under attachment interface version 1, or
`{"error": <code>, "reason": <code>}`. It sets its own limits first and only then imports `pypdfium2`.

The parent trusts nothing here: it parses the output within its caps, checks the interface shape and every limit,
and builds every message itself. This process is not a sandbox; it runs with the application user's privileges.
Standard library plus `pypdfium2`; never imports the rest of AgentWiz.
"""
from __future__ import annotations

import json
import re
import sys

CPU_SECONDS = 30
ADDRESS_SPACE = 256 << 20         # Linux and WSL2 only (macOS does not enforce RLIMIT_AS); headroom above the largest fixture
PAGE_LIMIT = 300
RENDERED_LIMIT = 16 << 20          # bytes of rendered text, page markers included
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def set_limits(address_space: int = ADDRESS_SPACE) -> None:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))


def normalise(text: str) -> str:
    """Line endings to newlines; C0 controls other than newline and tab removed (and DEL)."""
    return _CONTROLS.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def _emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.flush()


def extract(data: bytes, pdfium) -> dict:
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        password = exc.err_code == pdfium.raw.FPDF_ERR_PASSWORD
        return {"error": "damaged_file", "reason": "password" if password else "unreadable"}
    try:
        page_count = len(document)
        if page_count < 1:
            return {"error": "damaged_file", "reason": "no_pages"}
        blocks, warnings = [], []
        rendered = 0
        read = min(page_count, PAGE_LIMIT)
        last_read = 0
        stopped = None
        for index in range(read):
            number = index + 1
            try:
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range()
                    finally:
                        textpage.close()
                finally:
                    page.close()
            except Exception:  # noqa: BLE001 - one damaged page costs that page only
                warnings.append({"code": "page_skipped", "page": number})
                last_read = number
                continue
            text = normalise(text).strip("\n")
            if not text.strip():
                warnings.append({"code": "no_text_layer", "page": number})
                last_read = number
                continue
            size = len(f"--- page {number} ---\n".encode("utf-8")) + len(text.encode("utf-8")) + 2
            if rendered + size > RENDERED_LIMIT:
                stopped = "rendered_text"
                break
            rendered += size
            last_read = number
            blocks.append({"type": "text", "text": text, "page_start": number, "page_end": number})
        if stopped is None and page_count > PAGE_LIMIT:
            stopped = "pages"
        if stopped is not None:
            warnings.append({"code": "content_limited", "page": last_read or None, "limit": stopped})
        if not blocks:
            warnings.append({"code": "no_extractable_text", "page": None})
        return {"interface_version": 1, "format": "pdf", "page_count": page_count, "blocks": blocks,
                "warnings": warnings}
    finally:
        document.close()


def main() -> None:
    set_limits()
    data = sys.stdin.buffer.read()
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw  # noqa: F401 - the error codes
    except ModuleNotFoundError:
        return _emit({"error": "pdf_unavailable", "reason": "not_installed"})
    except Exception:  # noqa: BLE001 - ImportError, OSError from the native library
        return _emit({"error": "pdf_unavailable", "reason": "load_failed"})
    _emit(extract(data, pdfium))


if __name__ == "__main__":
    main()
