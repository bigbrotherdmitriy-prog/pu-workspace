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

This is evidence for this scan, not a general OCR accuracy claim.

## Implementation

PSM 1 remains the primary configured mode. A bounded PSM 6 retry is eligible only
when the primary result contains multiple distinct isolated endings and enough
prose. The retry is accepted only when fragmentation falls, text volume remains
within 75-135%, at least 90% ordered digit-stream similarity is retained, no new
non-fragment corruption appears, and prose volume remains comparable.

Fallback is limited to the first two OCR-eligible pages per document and shares
the existing deadline. A timeout keeps the primary output. Administrators can
disable it with `OCR_ADAPTIVE_FALLBACK=false` or adjust
`OCR_FALLBACK_MAX_PAGES` and `OCR_FALLBACK_PSM`.

## Limits

- PSM 6 can still mix columns; conservative acceptance criteria reduce but do not
  eliminate that risk.
- Ordered digits detect many losses/reorderings but do not prove every amount is
  attached to the correct row.
- Existing extracted text and task records are not rewritten automatically.
- Production acceptance requires deployment followed by explicit re-recognition
  of this document; generated proposals must remain unconfirmed until reviewed.
