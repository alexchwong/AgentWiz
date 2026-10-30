# AgentWiz attachment interface

Version 3 (draft), dated 30 October 2026. Supersedes version 2. The interface it defines is still `interface_version` 1.
Status: **draft**. It may change while the owner uses basic attachments in AgentWiz 0.1.2. It is frozen as `interface_version` 1 when docSpell's build starts, and versioned after that (D35, R3-8).
Written with: *AgentWiz 0.1.2 — Technical Specification*, version 3 (R3-2).
Kept in: the project, and as `docs/attachment_interface.md` in the AgentWiz repository. It is copied into docSpell's repository as the first part of docSpell's specification.

**Changes from version 2** (from *AgentWiz 0.1.2 — Technical specification review v2*, item V2-4). One correction, in Section 5: only a non-paginated document whose single block is a `text` block renders as that text unchanged. Version 2 applied the exception to any single block, which contradicted the heading and figure rules: a lone heading could render as `## Results` or `Results`, and a lone figure would lose `[figure: …]`. The basic reader always returns one `text` block, so its output is unchanged.

**Changes from version 1** (from *review v1*, item V1-7). Three corrections, none changing the shape of a valid result:

- Section 8: the basic reader's blocks carry `page_start` and `page_end`, both `null`, as Section 3 requires. Version 1's "no page fields" contradicted Section 3.
- Section 5: when no block is rendered, `included_pages` is `null` even for a paginated document, `text` is empty, and `notice` says so. Version 1 left that case without a valid value.
- Section 4: a version mismatch is reported as its own case, `unsupported_interface_version`, naming both versions, instead of as a malformed result.

---

## 1. Purpose and parties

This document defines how a **reader** turns an attached file into text that AgentWiz can insert into a request. There are two readers and one consumer:

| Party | Role | From |
| --- | --- | --- |
| AgentWiz's basic reader (`agentwiz/attachments.py`) | Reader for `.txt`, `.md`, `.csv` and `.json`. Implements a subset (Section 8). | 0.1.2 |
| docSpell | Reader for PDF, Word and other formats. Implements all of it. | 0.1.4 (separate repository) |
| AgentWiz | Consumer: uploads, checks each result, renders, previews and inserts. | 0.1.2 |

**Why it is written before docSpell exists.** The interface is shaped by the PDF case, because a text reader never meets pages, tables, or blocks joined across pages. An interface derived from the basic reader would lack exactly what docSpell needs, and docSpell's arrival would then be a breaking change (plan 9.3).

**A structural contract, not shared classes.** Readers return plain JSON-compatible data (dicts, lists, strings, integers, booleans, `None`) and raise exceptions identified by a `code` attribute. Neither package imports the other's types, so AgentWiz's basic reader works without docSpell installed, and docSpell needs nothing from AgentWiz.

## 2. A reader module

A reader is a Python module exposing:

```python
INTERFACE_VERSION: int = 1
FORMATS: tuple[str, ...]          # formats the reader handles, as lowercase extensions without the dot

def extract(source: bytes | str | os.PathLike, *, filename: str | None = None) -> dict: ...
def render(document: dict, pages: tuple[int, int] | None = None) -> dict: ...

class AttachmentError(Exception):
    code: str                       # Section 6
```

**`extract`** reads one file and returns its **document result** (Section 3).

- `source` is the file's bytes, or a path to it.
- With bytes, `filename` is required; it decides the format and is never opened.
- With a path, `filename` defaults to the path's last component.
- The reader never writes outside a temporary directory of its own, and removes what it writes.

**`render`** turns a document result from the **same reader** into the text to insert, for the whole document or a page range (Section 5). A consumer never splits, joins or re-orders blocks itself; that is why rendering belongs to the reader.

Both functions are pure with respect to process state: no printing, no global state, one call per operation (plan 13.3, following modelSpell's library rules).

## 3. The document result

```json
{
  "interface_version": 1,
  "format": "pdf",
  "page_count": 12,
  "blocks": [
    {"type": "heading", "text": "Results", "page_start": 3, "page_end": 3, "level": 2},
    {"type": "paragraph", "text": "…", "page_start": 3, "page_end": 3},
    {"type": "table", "text": "| Gene | Count |\n| --- | --- |\n| TP53 | 4 |", "page_start": 3, "page_end": 5,
     "rows": [["Gene", "Count"], ["TP53", "4"]], "header_rows": 1, "joined": true, "source_pages": [3, 4, 5]},
    {"type": "figure", "text": "Figure 2: survival curves", "page_start": 6, "page_end": 6}
  ],
  "warnings": [
    {"code": "no_text_layer", "message": "Page 9 has no text layer; its content is missing.", "page": 9}
  ]
}
```

| Field | Type | Rule |
| --- | --- | --- |
| `interface_version` | integer | The version the reader implements. |
| `format` | string | One of the reader's `FORMATS`. |
| `page_count` | integer ≥ 1, or `null` | `null` for formats without fixed pages (the four basic formats; Word, unless docSpell decides to render it, plan 13.3). |
| `blocks` | list | The document's content in reading order. May be empty only when the document has no content; then a warning says so. |
| `warnings` | list | Anything the reader could not do fully. Never silently omitted (P7). |

**Blocks.** Every block has `type`, `text`, `page_start` and `page_end`:

| `type` | `text` holds | Extra fields |
| --- | --- | --- |
| `text` | Text with no further structure (the basic reader's only type) | — |
| `heading` | The heading text | `level`: 1–6 |
| `paragraph` | One paragraph | — |
| `list` | The items, one per line, each prefixed `- ` or `1. ` as in Markdown | — |
| `table` | The reader's Markdown rendering of the table | `rows`: list of rows, each a list of cell strings; `header_rows`: integer ≥ 0; `joined`: true when the reader joined parts from consecutive pages; `source_pages`: the pages the parts came from (present when `joined`) |
| `figure` | A placeholder: caption or alternative text, or `""` | — |

`page_start` and `page_end` are the first and last pages the block's content comes from: integers with `1 ≤ page_start ≤ page_end ≤ page_count`, or both `null` exactly when `page_count` is `null`.

**Warnings.** Each has `code`, `message` and `page` (an integer, or `null` for the whole document). Codes defined so far:

| Code | Meaning |
| --- | --- |
| `empty_file` | The file has no content. |
| `no_text_layer` | A page has no extractable text (for example a scanned page without OCR). |
| `page_skipped` | A page could not be read and is missing from the blocks. |
| `table_join_uncertain` | A table might continue on the next page but was not joined (Appendix A of the plan lists the signals). |
| `content_limited` | The reader stopped early because of a stated limit; the message names the limit and where it stopped. |

A reader may add codes. A consumer shows any warning's `message`, and does not need to know its code.

## 4. Consumer checks

Before using a result, AgentWiz checks it (`attachments.check_document`), in this order:

1. **Version.** If `interface_version` is an integer that AgentWiz does not accept, the result is refused as `unsupported_interface_version`, with a message naming the reader's version and the versions AgentWiz accepts. Nothing else is checked, because a result for another version may rightly have another shape.
2. **Shape.** The result is refused as `extraction_failed` ("The reader returned a malformed result.") if:
   - a field in Section 3 is missing or has the wrong type, including an `interface_version` that is not an integer;
   - a block type or its extra fields break Section 3;
   - page fields disagree with `page_count`.

`unsupported_interface_version` is AgentWiz's own code, used only by this check; readers never raise it (Section 6). In AgentWiz 0.1.2 the accepted set is `{1}`.

## 5. Rendering

`render(document, pages=None)` returns:

```json
{"text": "…", "pages": [3, 5], "included_pages": [2, 6],
 "notice": "Included content from pages 2 and 6, outside the requested pages 3–5, because blocks there continue into the range."}
```

| Field | Rule |
| --- | --- |
| `text` | Markdown, as below. `""` when no block is rendered. |
| `pages` | The requested range as `[first, last]`, or `null` for the whole document. |
| `included_pages` | The first and last pages the rendered content actually comes from. `null` when `page_count` is `null`, and `null` when no block is rendered. |
| `notice` | `null`, or one sentence about what the text covers compared with what was asked: either the pages outside the requested range that included content comes from, or, when no block is rendered, that no content was extracted from the requested pages (or from the document). |

**Markdown.**

- Blocks are rendered in order, separated by one blank line:
  - `heading`: `#` repeated `level` times, a space, then the text;
  - `paragraph`, `list` and `text`: the text;
  - `table`: its `text`;
  - `figure`: `[figure: <text>]`, or `[figure]` when the text is empty.
- When `page_count` is not `null`, a marker line `--- page N ---` comes before the first block that starts on page N, for each page that has content. A block starting before the range carries its own start page's marker.
- When `page_count` is `null` there are no markers. A document whose only block is a `text` block renders as that block's text, unchanged. Any other block type, alone or not, follows the rules above.

**Page ranges (R3-4).**

- With `pages = (first, last)`, every block that **overlaps** the range is included **whole**: `page_start ≤ last` and `page_end ≥ first`. Nothing is cut at a page boundary.
- If any included block extends outside the range, `notice` names those outside pages and `included_pages` shows the real extent.
- If no block overlaps a valid range (for example a scanned page without a text layer), the result is not an error: `text` is `""`, `included_pages` is `null`, and `notice` says no content was extracted from pages `first`–`last`. The document's warnings already say why. The same holds for the whole document when `blocks` is empty.
- An invalid range raises `AttachmentError("invalid_page_range")`: `first < 1`, `last < first`, `last > page_count`, or any range when `page_count` is `null`.

AgentWiz adds the provenance line (`[file "report.pdf" pages 3–5]` from 0.1.4) and shows `notice` in the preview. It never edits `text`.

Before either side builds page ranges, a fixture document with a table spanning three pages is agreed and used in both repositories to check this policy (plan 13.2). It also includes a page with no extractable content, so the empty-range case above is checked too. AgentWiz uses the fixture's document result as data, so it needs no PDF engine.

## 6. Errors

A reader raises `AttachmentError` (its own class) with `code` set to one of:

| Code | When |
| --- | --- |
| `unsupported_format` | The reader does not handle this file's format. |
| `undecodable_text` | The text cannot be decoded (for example not UTF-8). |
| `damaged_file` | The file is damaged, truncated or encrypted. |
| `extraction_failed` | Any other failure to produce a result. |
| `invalid_page_range` | `render` was given a range that breaks Section 5. |

AgentWiz's own check adds one code, `unsupported_interface_version` (Section 4); a reader never raises it.

The exception's message is the reader's own text, suitable to show the user. It never contains file content beyond the file's name. A reader never returns an empty successful result in place of an error.

AgentWiz identifies a reader's errors by their `code` attribute, so it needs no reader's exception classes. Any other exception from a reader is reported as `extraction_failed` with its class name only.

## 7. Versioning and change control (D35)

- **While this document is a draft:** the owner's use of basic attachments may change it. Each change updates this document, `docs/attachment_interface.md` and `agentwiz/attachments.py` together, and `interface_version` stays 1.
- **When docSpell's build starts:** this document is frozen as `interface_version` 1, and docSpell is specified and built to it.
- **After that:** any change increases the version. AgentWiz states which versions it accepts, and refuses a result with any other version, naming both.

## 8. The basic reader's subset (AgentWiz 0.1.2)

| Aspect | Basic reader |
| --- | --- |
| `FORMATS` | `("txt", "md", "csv", "json")`, chosen by extension, in any letter case |
| `extract` | Decodes UTF-8 (with or without a byte-order mark) or UTF-16 with a byte-order mark; normalises line endings to `\n`; returns one `text` block holding the whole file, verbatim otherwise |
| `page_count` | Always `null`; each block's `page_start` and `page_end` are present and `null`; no markers |
| `warnings` | `empty_file` for an empty file; nothing else |
| `render` | Returns the block's text unchanged; any `pages` argument raises `invalid_page_range` |
| Errors | `unsupported_format`, `undecodable_text` (including UTF-32) |

AgentWiz keeps reading these four formats itself after docSpell arrives, so they never depend on the optional extra (R3-5).

## 9. Questions left to docSpell's specification

From plan 13.3. None of these changes this interface's shape:

- whether Word documents are rendered to get pages, or reported with `page_count` `null`;
- install weight, and so the engine;
- OCR for scanned PDFs, or only the `no_text_layer` warning;
- size and time limits, reported with `content_limited`;
- behaviour on encrypted or damaged files (`damaged_file`);
- the first list of formats.
