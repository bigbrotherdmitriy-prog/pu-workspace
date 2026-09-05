# Document filename egress guard

Date: 2026-09-05. Base: `aebd4e687d646961c9608f1a5906fba300b94aed`.
Branch: `codex/ai-egress-filename-guard`, separate clean worktree.

## Proven defect

The Telegram document webhook prepared the document body using ProjectAIPolicy
but passed the original filename to `AIProviderAdapter.analyze_document` and used
the same original name as cache context. Gemini includes that filename in its
prompt. Consequently, `redacted` removed EMAIL/PHONE/INN from the body while those
same values remained in the filename sent to external AI. `metadata_only` also
sent the original name and created cache entries keyed by that name.

Four regression cases failed before the fix: filename EMAIL, PHONE, INN and
metadata-only filename/cache isolation. Three control cases already passed:
local-only, explicit external-allowed and the existing missing-policy default.
The fixture calls the actual webhook and real text extraction, document/version
indexing, local task/draft/governance processing, policy helper and AI cache.
SessionLocal uses an isolated in-memory SQLite database. Only Telegram download,
notification delivery and AI adapter are doubled; no external request is made.

## Minimal correction

`prepare_external_ai_document` applies the existing project policy to the complete
document prompt input and returns prepared text, prepared filename and mode.
The helper resolves the existing text policy once. In `redacted`, filename and
body use the same existing stable EMAIL/PHONE/INN substitutions. In `metadata_only`,
the context is the fixed non-sensitive label `document`. `local_only` still raises
before cache access or adapter analysis. Existing explicit `external_allowed`
and absent-policy behavior are preserved.

The Telegram document boundary now passes the same prepared filename to the
provider and cache. The original name remains in local Document/Message records,
local summaries and notifications; DocumentVersion content is unchanged. Existing
raw-filename cache entries do not match the prepared context for the corrected
restricted-mode cases. No cache deletion or schema migration is required.

## Verification

- Before fix: **4 failed, 3 passed**, demonstrating the actual boundary defects.
- Initial adjacent policy/cache/Telegram suite: **26 passed**.
- Final AI policy/cache/secretary, Telegram and OCR suites: **73 passed, 1 skipped**
  in 3.62 seconds. The skipped test requires actual local Tesseract runtime.
- Ten new boundary tests cover exact prepared payload/cache keys, all three
  supported sensitive classes, metadata-only safe cache reuse, avoidance of old
  raw-context cache entries, unchanged local source data, zero local-only AI
  calls, safe local fallback on provider error and default compatibility.
- `git diff --check`: PASS. No provider, real Telegram configuration, production
  data, package installation, push, merge or deployment was involved.

Tests use the shared workspace virtualenv Python with `-X utf8`,
`-p no:cacheprovider` and unique `--basetemp` values. Final command from `backend`:

```text
python -X utf8 -m pytest tests/test_ai_egress_filename_guard.py
tests/test_ai_policy.py tests/test_ai_cache.py tests/test_ai_secretary_api.py
tests/test_ai_secretary_automation.py tests/test_telegram_webhook.py
tests/test_telegram_files.py tests/test_telegram_task_commands.py
tests/test_telegram_transport.py tests/test_telegram_relay.py
tests/test_telegram_relay_health.py tests/test_v54_ocr_benchmark.py
tests/test_ocr_benchmark_public_evidence.py tests/test_ocr_batch.py
tests/test_ocr_commercial_hardening.py -q --tb=short -p no:cacheprovider
--basetemp=.pytest-filename-egress-adjacent-20260905
```

## Remaining P04/P05 boundaries

This is a document-filename bypass fix, not organization-wide DLP completion.
There is still no organization policy in this path; missing ProjectAIPolicy still
means `external_allowed`. Organization inheritance/default migration and owner
decisions are unchanged. `dlp_enabled` remains a stored setting without a complete
data-class enforcement engine. Supported redaction classes remain EMAIL/PHONE/INN;
bank/name/address/amount/contract DLP is not claimed. The Telegram message-context
branch and other future ingress points require their own review. Full backend,
real external provider and actual OCR runtime acceptance were not performed here.
