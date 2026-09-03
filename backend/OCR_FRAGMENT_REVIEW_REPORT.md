# Review of fragmented text, 2026-09-03

Base: `f978c72541d1c368c7df425f5f3f0647afe45c6b`.
Branch: `codex/ocr-fragment-review`.

## Finding and scoped change

The previous review rules detect replacement characters, repeated mixed-script
substitutions and other explicit noise. They do not detect intact Cyrillic letters
split into word fragments. Therefore the fragment shown during acceptance could
keep the compatibility score of 0.82 despite its visibly damaged wording.

The additional rule requires at least five Cyrillic prose words of three or more
letters and at least two **different**, isolated, lowercase Russian ending-like
fragments. It uses a bounded set of endings, not a spelling dictionary. Ordinary
short words such as `у`, `при`, `их`, `им`, `ей` do not participate. Uppercase
abbreviations, fragments within identifiers, hyphen/slash notation, quoted endings
and dot/colon abbreviations are excluded. One ambiguous token is insufficient.

The reported generic phrase `Поку обязан при ий товар лично или через у го пред:
теля.` now retains its original wording but receives a score capped at 0.45 and
an explanation suggesting possible word breaks. The normal complete counterpart
keeps the compatibility score of 0.82. Neither score is a calibrated probability.

No evidence is corrected, no candidate is dropped, no task is confirmed, and no
new assignee behavior is introduced. The existing creation path stores reasons in
description. There are no UI/API/schema/dependency changes in this commit.

## Tests

- New synthetic regression tests: 25 passed.
- Combined new and existing task-quality tests: 69 passed.
- Full backend suite: **434 passed, 1 skipped**, 2 existing deprecation warnings.
- PostgreSQL integration remains skipped locally (SQLite in-memory test database).
- Initial full runs hit an existing Windows permission error on the shared pytest
  temp directory; the successful full run used a fresh workspace `--basetemp`.
- `git diff --check` passed.

Tests include the generic reported phrase, several different artificial fragments,
clean counterpart, dates, normal short words, technical codes, quoted suffixes,
uppercase abbreviations, repeated single ambiguous tokens, per-candidate isolation
and stubbed OCR output propagation without rewriting. No customer PDF is embedded.

## Limits and remaining work

This is a **manual-review heuristic, not an OCR correction**. The original PDF was
unavailable and no real OCR benchmark was run. The PDF/Tesseract pipeline is
unchanged. Native text length or an alphanumeric ratio does not prove semantic
correctness; choosing another OCR output purely by length would not resolve that.

Standalone suffixes can be intentional in linguistic material or unfamiliar
technical notation; false positives remain possible, including unquoted examples
with two different ending-like tokens. The warning describes a possibility and
must not be read as proof of corruption. The exclusions intentionally favor
precision and can miss damage. Incorrect whole words, valid-looking fragments,
single/repeated identical fragments or very short sentences can remain unflagged.
Tests explicitly document that unflagged nonsense is still possible.

Existing database tasks are not rescored. Reimport uses the existing evidence hash
and does not migrate existing records. Any future reassessment must be a separate,
auditable operation; none was performed here.

No production data, runtime configuration or provider connection was changed.
No merge, push or deploy was performed by this worktree.
