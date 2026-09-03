# Adaptive OCR fallback review

## Source evidence

The user supplied the original one-page image-only PDF `951 ДИСИАЙ ГОРОДЕЦ.pdf`.
It is not stored in the repository. The source scan visibly reads in clause 7:

> Покупатель обязан принять оплаченный товар лично или через уполномоченного представителя.

At 300 DPI JPEG, matching the production renderer, Tesseract's configured PSM 1
split that clause into fragments equivalent to the damaged production task.
PSM 6 recognized the clause continuously. On the complete page the candidate:

- reduced distinct isolated-ending fragments from 3 to 0;
- retained 119.8% of the alphanumeric volume;
- retained 95.2% similarity of the ordered digit stream;
- added no replacement, control, or mixed-script corruption signals.

The complete PSM 6 page is nevertheless rejected because individual table
digits differ from PSM 1. Only clause 7 satisfies the strict numbered-prose
merge checks; the table and all surrounding financial data stay byte-for-byte
from the primary OCR result.

This is evidence for this scan, not a general OCR accuracy claim. The stricter
policy below was subsequently verified with repository-only synthetic fixtures;
the customer PDF was not added to tests or committed.

## Implementation

PSM 1 remains the primary configured mode. A bounded PSM 6 retry is eligible when
the primary result contains repeated isolated endings and enough prose, or when
the primary output is empty/very short. Primary failures and timeouts produce an
empty page result without discarding pages already recognized in the batch.

Whole-page replacement is forbidden for detected tabular layouts. On such pages,
only a damaged numbered prose clause may be replaced, and only when that clause
has no remaining fragmentation/corruption, keeps its numeric tokens exactly, and
retains comparable text volume. The surrounding header, table, amounts and bank
details always remain from PSM 1. A non-tabular page may be replaced as a whole
only under the same strict corruption, volume and exact numeric-token checks.

Fallback is limited to the first two OCR-eligible pages per document and shares
the existing deadline. A timeout keeps the primary output. Administrators can
disable it with `OCR_ADAPTIVE_FALLBACK=false` or adjust
`OCR_FALLBACK_MAX_PAGES` and `OCR_FALLBACK_PSM`.

## Limits

- Numbered-clause merging does not repair unnumbered damaged prose on a page that
  also contains a table; it keeps the safer primary text instead.
- Numeric-token equality does not prove semantic correctness, so generated
  proposals still require review against the original.
- Existing extracted text and task records are not rewritten automatically.
- Production acceptance requires deployment followed by explicit re-recognition
  of this document; generated proposals must remain unconfirmed until reviewed.
