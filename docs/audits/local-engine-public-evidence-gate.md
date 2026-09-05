# Local engine acceptance: public evidence, not a private substitute

Date: 2026-09-05. Base: `098df263df3b41f40005cdd82b55b90f5e87614d`.

## Reproduced defects

The OCR benchmark computed field precision/recall and coordinates from its
private preprocessed TSV pass, while the public `extract_text_result` result
could return no fields or invalid coordinates and still receive PASS. Failed
pages also disappeared from the false-negative denominator.

Independent review additionally reproduced a 20-page false PASS with two malformed
public field results. Page success is now credited only after the whole public
result is validated; the regression requires 18/20 technical successes and FAIL.

Six regression cases failed before the fix: missing public evidence; wrong page;
negative coordinates; coordinates outside the page; NaN coordinates; missing
fields on a failed extraction. A valid-public-evidence control passed.

The benchmark now uses public fields and their actual page dimensions, requires
finite in-bounds coordinates, counts expected fields from failed pages as false
negatives, and checks public OCR and recognition success. Before/after private
preprocessing metrics remain comparison metrics only. Each corpus page is sent
as a standalone image, so the public page index is 1; this does not establish
multi-page PDF remapping. No extraction algorithm or production flag changed.

Historical metrics in `v54-ocr-benchmark-metrics.json` predate this correction;
they are not fresh acceptance evidence for the corrected benchmark.

## Mandatory Linux execution

The existing branch-scoped v54 runtime workflow now has an independent
`local-engines` job on GitHub-hosted Ubuntu 24.04. It installs local Tesseract
with Russian/English/OSD and DejaVu, then runs the exact real benchmark and two
POSIX symlink rejection tests. It uses no database or provider credentials.

`scripts/ci/local_engine_acceptance.py` requires exactly three passing tests,
zero skips/xfail/deselection, and exit 0. Only a fixed JSON summary is uploaded;
raw subprocess output, exceptions, paths and source documents are not emitted.
An owned temporary directory is used for pytest and removed after the command.
Missing artifacts fail the job. Failure/timeout cannot become PASS.

## Local checks

- OCR regression plus existing benchmark/commercial/batch tests: **27 passed,
  1 skipped** (Tesseract unavailable).
- Local-engine harness and workflow contracts: **14 passed**.
- Real local helper execution: **FAIL, 0 passed, 3 skipped**. This is expected
  missing Tesseract/Windows symlink capability, not a Linux runtime proof.
- Runtime authority URL regression: **1 failed, 5 passed** before fix;
  **6 passed** after fix. The `postgres` hostname is accepted only under
  `GITHUB_ACTIONS=true`; database prefix and no-query guards remain required.

Status: **CONDITIONAL**. Corrected actual OCR quality, Linux symlink evidence and
PostgreSQL authority still require isolated CI execution on the resulting SHA.
No packages installed locally, provider calls, push, merge or deploy occurred.
