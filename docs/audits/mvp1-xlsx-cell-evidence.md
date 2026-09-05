# MVP1 D14: bounded XLSX cell evidence extraction

Date: 2026-09-05. Base: `aebd4e687d646961c9608f1a5906fba300b94aed`.
Branch: `codex/mvp1-xlsx-cell-evidence`.

## Outcome and scope

Implemented and locally tested a bounded XLSX parser, with a minimal call from
`extract_text_result`. This is an **extraction-layer slice**, not complete D14,
production acceptance, persisted Evidence, or a live spreadsheet integration.
No migrations, UI, provider adapters, jobs, OCR benchmark root, or external APIs
were changed. All workbook inputs in the new tests are synthetic ZIPs in memory.

Code: [parser](../../backend/app/ocr_quality/xlsx_cells.py),
[ExtractionResult and compatibility facade](../../backend/app/organizer_engine/content.py),
[regression tests](../../backend/tests/test_mvp1_xlsx_cell_evidence.py).

## Data contract

`ExtractionResult.metadata()` adds `spreadsheet_sheets` and `spreadsheet_cells`.
Sheet identity comes from `xl/workbook.xml` and its relationships, not guessed
filenames. Workbook order, sheet ID/name, actual worksheet part, exact A1 address,
row and column are retained. Empty sheets retain their identity in the sheet list.
Cell values remain strings; no numeric/date/currency formatting is invented.

Each cell separates original formula text (without adding `=`), formula presence,
type/shared index/range, raw `<v>` cache, decoded cache/string value, and
`cache_state` (`present`, `empty`, `missing`). Shared/inline rich text concatenates
actual text runs without inventing spaces. Zero is distinct from a missing cache.
`formula_recalculated` is always false and `cache_freshness` is always
`not_verified`: a supplied cache is not evidence of current calculation accuracy.
Extraction confidence describes parser/review status, not formula correctness.

Missing/empty formula caches, unavailable shared-formula follower expressions,
non-normal formula types, cell errors, external links, macro presence, incomplete
identity and truncated TSV produce review flags. Shared/array/data-table formulas
are preserved, not expanded or evaluated. Formula and cached-value locator
descriptors are separate and validate against the existing
[SheetCellLocator](../../backend/app/source_evidence/fragment_reader.py) schema.
No `displayed_value` locator is claimed; styles and rendered values are not read.

Worksheet-only legacy inputs remain readable as TSV, but lack verified workbook
identity and get no locators. Missing explicit cell addresses remain null in
`cell_ref`, `row`, and `column`; internal positional traversal used to preserve
legacy text is not exposed as source coordinates. Canonical cell locators require
explicit row/cell addresses and workbook identity. Duplicate, conflicting,
out-of-order or out-of-bounds coordinates fail closed.

The `.text`/`extract_text()` facade remains TSV for
[structured import](../../backend/app/structured_import.py). Dense rows keep their
existing layout, sparse columns retain tab gaps, and sheets concatenate in workbook
order. Blank source rows are not materialized: this compatibility projection is
not an authoritative sheet grid. Metadata is the exact-cell representation.

## Resource and security boundaries

| Bound | Enforced value |
| --- | --- |
| Compressed archive | 10 MiB |
| ZIP entries | 1,000 |
| Individual uncompressed entry / selected XML | 4 MiB |
| Aggregate declared uncompressed entries | 20 MiB |
| Per-entry compression ratio | 200:1 |
| Sheets / cells / shared strings | 100 / 20,000 / 20,000 |
| Individual formula or value | 32,767 characters |
| Aggregate shared strings plus formulas/decoded cell values | 1,000,000 characters |
| XML nodes / nesting per selected XML part | 100,000 / 128 |
| TSV projection | 50,000 characters |
| Coordinates | A1 through XFD1048576 |

Archive/entry/XML/value budgets fail the whole parse, rather than returning
partial evidence. TSV alone may truncate at a row boundary with an explicit
review warning, while all cell metadata within the other bounds is retained.
Memory is bounded by those limits, not constant-memory streaming: selected XML
parts become bounded trees and the result retains bounded cell metadata.

ZIP filenames (including original names before Windows normalization), duplicates,
symlinks, encryption, unsupported compression, absolute filesystem paths,
traversal, and ambiguous workbook relationships are rejected. Internal OPC targets
resolve only inside the package; `/xl/...` package-absolute targets are supported.
URL-like, percent-encoded and traversal targets cannot become file/network reads.
No archive entries are extracted to disk. Only selected XML parts are opened;
macro binaries and external relationships are never executed or fetched.

XML accepts UTF-8 only and rejects NUL, DTD and entity declarations before parsing.
There is no formula engine, XML external resolver, shell, network or macro call.
Failures expose fixed codes, not workbook text, paths or exception details.
This is a supported-subset parser, not a full OOXML schema validator: encrypted
packages, other encodings, chartsheets and unsupported structures may fail closed.

## Regression evidence

Regression-first baseline, before implementation:
`test_structured_import.py test_document_engine.py test_ocr_commercial_hardening.py`
— **10 passed**. Four new tests then failed against the old parser: sparse-column
position loss, missing formula/cache metadata and OCR fallback on missing caches.
An additional hostile backslash ZIP regression reproduced a Windows `ZipInfo`
normalization bypass before original-name validation was added. A legacy identity
regression also failed before inferred row/column metadata was removed.

Final targeted command (from `backend`, shared test venv Python 3.12.14):

```text
python -X utf8 -m pytest tests/test_mvp1_xlsx_cell_evidence.py tests/test_content.py tests/test_structured_import.py tests/test_document_engine.py tests/test_ocr_commercial_hardening.py tests/test_ocr_batch.py tests/test_v54_fragment_reader.py --basetemp=.pytest-xlsx-cell-verified
```

The test set covers 53 new synthetic cases: formulas/caches, missing and empty
caches, shared/inline strings, sparse cells, worksheet ordering and relationship
targets, empty sheets, legacy identity, shared-formula followers, error/zero values,
malformed coordinates, duplicate ZIP/sheet/relationship identity, unavailable
parts, external/macro isolation, path traversal, DTDs/entities, every declared
resource-limit class and explicit TSV truncation. Adjacent tests cover content,
structured import, document processing, OCR metadata and strict fragment locators.
Final result: **165 passed in 20.20s**, including all 53 new XLSX cases; no
provider/live claim follows. `git diff --check` also passed.

## Remaining bounded work / gates

1. Extraction metadata is **not durably wired into XLSX ingestion** in this slice.
   [Local upload](../../backend/app/local_upload_staging.py),
   [Drive ingestion](../../backend/app/organizer_engine/drive.py),
   [Yandex ingestion](../../backend/app/integrations/yandex_disk.py),
   [Telegram](../../backend/app/api/telegram.py), and
   [workspace ingestion](../../backend/app/api/workspace.py) use the text facade.
   [OCR batch](../../backend/app/ocr_batch.py) persists metadata only for supported
   PDF/image inputs and does not admit XLSX. Next bounded action: one ingestion
   path carrying these cells into existing source-version/evidence storage with
   retry, integrity and access regression tests. No such persistence is claimed.
2. `sheet_cell` descriptors identify content in the supplied workbook bytes, not
   SourceVersion/access/integrity authority. A caller must pin the original and
   validated source version before durable evidence creation or fragment reads.
   No resolver, DB authority or access check was bypassed or fabricated here.
3. XLS binary workbooks and native Google Sheets are separate slices. Their
   formula/cache semantics and provider/version authority are unchanged. Shared
   formula expansion, styles/display formatting, merged-grid semantics, unsupported
   encodings and large workbooks need separately bounded implementation decisions.
4. Owner-approved representative files and runtime acceptance remain separate;
   synthetic tests do not demonstrate real-provider, deployment or production
   readiness. Full-backend integration regression belongs to the parent task.

## TSV cap CPU regression — 2026-09-05

Independent review found that sparse rows still expanded up to 16,384 empty-column
slots after TSV had reached its 50,000-character cap. A 1,229-byte, 100-row fixture
produced only three text rows but expanded 1,638,400 slots. The permitted 20,000
cells could therefore cause 327,680,000 projection lookups despite the cap.

The new regression failed first: after truncation it observed a third dense-row
expansion (`[1, 16384, 16384]` instead of `[1, 16384]`). The minimal fix moves the
truncation guard before line construction. Cell/formula/cache/locator metadata
continues to be parsed and retained for subsequent rows; only discarded TSV work
stops. Existing limit warnings and output text are unchanged.

Validation: targeted parser/content/structured-import/document/OCR/fragment-reader
suite **166 passed in 21.81 seconds**, including 54 XLSX cases. `git diff --check`
passed. No full suite,
provider calls, production access, push, merge or deployment was performed here.
