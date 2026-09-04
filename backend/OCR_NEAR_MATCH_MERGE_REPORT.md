# OCR near-match merge review, 2026-09-03

Base release: `3fdd4eda9a24b8e3bad39c0d59a865dc267a4933`.

## Cause and correction

The adaptive OCR pass already protects tables by replacing only a damaged numbered
prose section. Within that section, however, the complete `PSM=6` text replaced the
complete `PSM=1` text. This could introduce a new one-letter spelling change in a
word which the primary pass had already read correctly.

The merge now aligns alphabetic tokens from both OCR variants. Within changed
regions it preserves a primary token only when there is one unambiguous fallback
token of the same length with exactly one substituted letter. Structural repairs
and larger word changes still come from the fallback, so split fragments can become
complete words. The policy also applies to an accepted whole-page fallback.

This is deliberately provenance-preserving rather than spell correction. Neither
OCR variant establishes which of two near-identical spellings is correct. The code
therefore avoids letting a structural fallback silently alter already-readable
primary evidence. It does not contain a dictionary or a phrase-specific rewrite.

## Verification

- 30 focused content/adaptive-OCR tests passed.
- Full backend suite: **457 passed, 1 skipped**, with two existing dependency
  deprecation warnings. PostgreSQL remains the skipped local integration check.
- Synthetic regression models a fragmented clause where the primary says `через`
  and structural fallback says `чераз`; the merged clause retains `через` while
  accepting the larger structural repair.
- A numbered-clause/table regression confirms that table labels, row ordering,
  quantities and amounts remain from the primary result.
- No customer PDF or production data is included in tests.

## Boundaries

This does not decide correct spelling and cannot repair a one-letter mistake already
present in the primary OCR. If the primary variant is wrong and the fallback is
right, the same conservative policy retains the primary spelling. Ambiguous token
matches are ignored. The existing fragmentation and manual-review gates remain
necessary, and exact production acceptance must be repeated with the original PDF.

No API, database, frontend, dependency, deployment or existing task record is
changed by this patch.
