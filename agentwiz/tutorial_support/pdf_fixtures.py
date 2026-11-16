"""Repository-built PDF fixtures (starter blocks addendum, 4.4 and 4.3): small, deterministic PDFs written with
the standard library, packaged so the installed-wheel smoke and the tutorial use the same bytes as the tests.

`document(pages)` writes one page per entry: a list of text lines, or None for a page with no text layer (a
drawn rectangle, as a scan would be). `encrypted()` is protected with a user password (RC4, revision 2), so it
cannot be opened without one. `truncated()` is cut short. Nothing here reads or writes a file.
"""
from __future__ import annotations

import hashlib

_PAD = bytes.fromhex("28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a")
FILE_ID = bytes.fromhex("6167656e7477697a2d666978747572")


def _escape(text: str) -> bytes:
    raw = text.encode("latin-1", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _content(lines: list[str] | None) -> bytes:
    if lines is None:
        return b"0.5 g 72 600 200 100 re f"
    out = [b"BT /F1 12 Tf 14 TL 72 720 Td"]
    for line in lines:
        out.append(b"(" + _escape(line) + b") Tj T*")
    out.append(b"ET")
    return b"\n".join(out)


def _rc4(key: bytes, data: bytes) -> bytes:
    box = list(range(256))
    j = 0
    for i in range(256):
        j = (j + box[i] + key[i % len(key)]) % 256
        box[i], box[j] = box[j], box[i]
    out, i, j = bytearray(), 0, 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + box[i]) % 256
        box[i], box[j] = box[j], box[i]
        out.append(byte ^ box[(box[i] + box[j]) % 256])
    return bytes(out)


def _write(objects: list[bytes], trailer_extra: bytes = b"") -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<< /Size %d /Root 1 0 R /ID [<%s> <%s>] " % (len(objects) + 1, FILE_ID.hex().encode(),
                                                                     FILE_ID.hex().encode())
            + trailer_extra + b">>\nstartxref\n%d\n%%%%EOF\n" % xref)
    return bytes(out)


def document(pages: list[list[str] | None], *, encrypt_with: str | None = None) -> bytes:
    """A PDF with one page per entry."""
    count = len(pages)
    font = 3
    first_page = 4
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>",
                            b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % (first_page + 2 * i)
                                                                    for i in range(count))
                            + b"] /Count %d >>" % count,
                            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for index, lines in enumerate(pages):
        content = _content(lines)
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 %d 0 R "
                       b">> >> /Contents %d 0 R >>" % (font, first_page + 2 * index + 1))
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    if encrypt_with is None:
        return _write(objects)
    # The standard security handler, revision 2, 40-bit RC4 (PDF 1.7, 7.6.3): algorithms 3, 2 and 4.
    user = (encrypt_with.encode("latin-1") + _PAD)[:32]
    owner = _rc4(hashlib.md5(user).digest()[:5], user)
    permissions = (-4).to_bytes(4, "little", signed=True)
    key = hashlib.md5(user + owner + permissions + FILE_ID).digest()[:5]
    check = _rc4(key, _PAD)
    encrypted = []
    for number, body in enumerate(objects, 1):
        if b"stream\n" in body:
            head, rest = body.split(b"stream\n", 1)
            data = rest[:-len(b"\nendstream")]
            object_key = hashlib.md5(key + number.to_bytes(3, "little") + b"\x00\x00").digest()[:10]
            body = head + b"stream\n" + _rc4(object_key, data) + b"\nendstream"
        encrypted.append(body)
    encrypted.append(b"<< /Filter /Standard /V 1 /R 2 /O <%s> /U <%s> /P -4 >>" % (owner.hex().encode(),
                                                                                    check.hex().encode()))
    return _write(encrypted, b"/Encrypt %d 0 R " % len(encrypted))


def text() -> bytes:
    return document([["AgentWiz PDF fixture.", "Order 007 ships on 3 March."]])


def multi_page() -> bytes:
    return document([["Page one says hello."], ["Page two says goodbye."], ["Page three ends."]])


def partial() -> bytes:
    """Three pages, the second without a text layer: partial coverage."""
    return document([["First page text."], None, ["Third page text."]])


def no_text() -> bytes:
    return document([None, None])


def encrypted() -> bytes:
    return document([["Secret text."]], encrypt_with="fixture-password")


def truncated() -> bytes:
    return text()[:40]


def over_limit(pages: int = 301) -> bytes:
    return document([[f"Page {n}."] for n in range(1, pages + 1)])


def near_limit() -> bytes:
    """300 pages of plain text, just under the 16 MiB source limit: the address-space limit is set so that this
    extracts."""
    line = "The quick brown fox jumps over the lazy dog, 0123456789. " * 2
    return document([[line] * 450 for _ in range(300)])


FIXTURES = {"text": text, "multi_page": multi_page, "partial": partial, "no_text": no_text,
            "encrypted": encrypted, "truncated": truncated, "over_limit": over_limit, "near_limit": near_limit}
