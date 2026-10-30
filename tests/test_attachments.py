"""Attachments, request limits and insertion previews: techspec 0.1.2 tests #38-#40."""
import codecs
import hashlib
import json

import pytest

from agentwiz import attachments, insertion
from agentwiz.ui import server as ui_server
from conftest import headers_only, upload

# 38 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["a.TXT", "b.Md", "c.csv", "d.JSON"])
def test_basic_formats_extensions(ui, name):
    status, body = upload(ui, b'{"x": 1}\n', name)
    assert status == 200 and body["format"] == name.rsplit(".", 1)[1].lower()
    assert body["insertion"]["text"] == f'[file "{name}"]\n{{"x": 1}}\n'


@pytest.mark.parametrize("data, text", [
    ("héllo".encode("utf-8"), "héllo"),
    (codecs.BOM_UTF8 + "héllo".encode("utf-8"), "héllo"),
    (codecs.BOM_UTF16_LE + "héllo".encode("utf-16-le"), "héllo"),
    (codecs.BOM_UTF16_BE + "héllo".encode("utf-16-be"), "héllo"),
    (b"a\r\nb\rc\n", "a\nb\nc\n"),
])
def test_basic_formats_encodings(ui, data, text):
    status, body = upload(ui, data, "f.txt")
    assert status == 200 and body["insertion"]["text"] == '[file "f.txt"]\n' + text


@pytest.mark.parametrize("data, name, code", [
    ("héllo".encode("latin-1"), "f.txt", "undecodable_text"),
    (codecs.BOM_UTF32_LE + "hi".encode("utf-32-le"), "f.txt", "undecodable_text"),
    (b"%PDF-1.7", "f.pdf", "unsupported_format"),
    (b"x", "noextension", "unsupported_format"),
])
def test_basic_formats_refused(ui, data, name, code):
    status, body = upload(ui, data, name)
    assert status == 400 and body["code"] == code


def test_basic_formats_interface():
    status_empty = attachments.extract(b"", filename="e.md")
    assert status_empty["warnings"] == [{"code": "empty_file", "message": "The file is empty.", "page": None}]
    doc = attachments.extract(b"text\r\n", filename="r.md")
    assert doc == {"interface_version": 1, "format": "md", "page_count": None,
                   "blocks": [{"type": "text", "text": "text\n", "page_start": None, "page_end": None}],
                   "warnings": []}
    assert attachments.render(doc) == {"text": "text\n", "pages": None, "included_pages": None, "notice": None}
    with pytest.raises(attachments.AttachmentError) as exc:
        attachments.render(doc, (1, 1))
    assert exc.value.code == "invalid_page_range"
    attachments.check_document(doc)
    for broken in ({k: v for k, v in doc.items() if k != "warnings"},
                   {**doc, "blocks": [{**doc["blocks"][0], "type": "poem"}]},
                   {**doc, "interface_version": "1"}):
        with pytest.raises(attachments.AttachmentError) as exc:
            attachments.check_document(broken)
        assert exc.value.code == "extraction_failed"
    with pytest.raises(attachments.AttachmentError) as exc:
        attachments.check_document({**doc, "interface_version": 2})
    assert exc.value.code == "unsupported_interface_version"


# 39 --------------------------------------------------------------------------------------------
def test_limits_and_filenames(ui, tmp_path, monkeypatch):
    for path, length in (("/api/attachments/extract", 50 * 2**20 + 1), ("/api/complete", 32 * 2**20 + 1),
                         ("/api/mcp/call", 32 * 2**20 + 1)):
        status, body = headers_only(ui, path, length, {"X-Filename": "a.md"})
        assert status == 413 and body["code"] == "too_large" and body["size"] == length
    status, body = upload(ui, b"abc", "a.md", length=None)
    assert status == 411 and body["code"] == "length_required"

    monkeypatch.setattr(insertion, "REQUEST_LIMIT", 1024)
    monkeypatch.setattr(ui_server, "UPLOAD_LIMIT", 2048)
    status, body = upload(ui, b"x" * 1536, "big.txt")
    assert status == 200 and body["insertion"]["too_large_to_send"] is True
    status, body = upload(ui, b"x" * 2049, "bigger.txt")
    assert status == 413

    tricky = '"\\\x01\x1f'
    status, body = upload(ui, tricky.encode() * 10, "q.txt")
    text = body["insertion"]["text"]
    assert body["insertion"]["request_bytes"] == len(json.dumps(text, ensure_ascii=False).encode()) - 2
    assert body["insertion"]["request_bytes"] > len(text.encode())

    for name in ("résumé 年報.md", "100% done.md", "emoji 🙂.md"):
        status, body = upload(ui, b"x", name)
        assert status == 200 and body["filename"] == name
        assert body["insertion"]["provenance"] == "[file " + json.dumps(name, ensure_ascii=False) + "]"
    for encoded in ("bad%zz.md", "bad%C3.md", "ctl%0A.md", "%41" * 400):
        status, body = upload(ui, b"x", None, encoded=encoded)
        assert status == 400 and body["code"] == "bad_filename"
    before = sorted(p for p in tmp_path.rglob("*"))
    status, body = upload(ui, b"x", "../../x.md")
    assert status == 200 and body["filename"] == "../../x.md"
    assert sorted(p for p in tmp_path.rglob("*")) == before


# 40 --------------------------------------------------------------------------------------------
def test_insertion_preview(ui):
    data = b"# Report\nline\n"
    status, body = upload(ui, data, "report.md")
    assert body["source_sha256"] == hashlib.sha256(data).hexdigest() and body["source_bytes"] == len(data)
    ins = body["insertion"]
    assert ins["sha256"] == hashlib.sha256(ins["text"].encode()).hexdigest() != body["source_sha256"]
    assert ins["text"].startswith('[file "report.md"]\n')

    line = insertion.tool_provenance("web", "search_web", '{"query":"lighthouse"}', False)
    assert line == '[tool web.search_web arguments={"query":"lighthouse"}]'
    assert insertion.tool_provenance("s", "a b", "{}", True) == '[tool s."a b" arguments={}] (tool error)'
    long_args = '{"q":"' + "x" * 300 + '"}'
    line = insertion.tool_provenance("s", "t", long_args, False)
    assert long_args[:200] in line and f"({len(long_args) - 200} more characters)" in line
    assert long_args not in line
