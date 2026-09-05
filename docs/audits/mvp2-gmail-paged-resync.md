# MVP2 Gmail paged resync

Date: 2026-09-05

Base: `3ff8b767383598226abe7fb3d6710e2de3bf734c`

Branch: `codex/mvp2-gmail-paged-resync`

## Result and limits

The existing durable Gmail history job now ingests a resync page before fetching
the next page. An inbox above 100 messages can complete: the offline worker test
ingests 101 distinct messages across three pages, including an entirely repeated
100-message page, with 101 processed and 100 skipped.

The resync scope remains `is:inbox newer_than:30d`. This is not an all-mail or
unlimited historical import. The explicit admission budgets are:

| Bound | Value |
| --- | --- |
| Maximum references requested/accepted per page | 100 |
| Maximum listing pages per run, including empty pages | 100 |
| Maximum raw references per run, including duplicates | 10,000 |
| Maximum continuation token length | 2,000 characters |
| Maximum message ID length | 500 characters |

The existing provider retry helper allows three attempts per read; the exact
worker attempt and unexpired lease remain mandatory. There is no additional
wall-clock timeout or lease extension in this change. A provider request already
in flight is not cancelled when the lease expires; the post-read guard prevents
its result from being ingested under an expired claim.

Only page-sized reference buffers, page-local deduplication IDs, bounded safe
counters and at most 100 continuation tokens are retained. No accumulated
mailbox-sized reference list or cross-page message-ID set is built. This bounds
application buffering of valid responses; it does not constrain an oversized
HTTP response before the provider client has parsed it. Oversized parsed pages
are rejected before ingestion.

## Cursor, failure and recovery

The profile history ID is fetched before the first listing. Only after the final
page and all successful ingestion does the existing checkpoint CAS publish that
pre-scan ID. Messages arriving during a successful scan remain eligible for the
next incremental history query. Empty pages with a valid continuation are
followed. Repeated/malformed tokens, malformed IDs and oversized pages fail
closed; an empty final page is a valid completion.

Successful message ingestion already commits through the existing mailbox-scoped
ingestion path. If a later message/page fails, prior successful messages remain
available, but the cursor does not advance. The next attempt pins a fresh
profile ID and starts listing from the first page; the existing mailbox message
identity deduplicates committed rows. A recovered worker uses the same job with
its newly validated worker attempt/lease. No page continuation or partial
history pin is persisted; no migration, second queue or new job kind is needed.

The numeric budgets are hard run limits, not resumable partitions. At exhaustion,
already accepted pages may be committed but completion is rejected and the
checkpoint remains unadvanced. A stable inbox above 10,000 raw references or 100
pages cannot be completed simply by retrying at the same limits. An inbox whose
listing/ingestion cannot finish within a valid worker lease may also require
operator intervention. A future partitioned/resumable recovery design or an
explicitly reviewed budget/lease policy change is needed for those cases. This
change makes no claim that replay alone resolves such a persistent limit.

Cross-page and restart deduplication reuse the existing Message/origin/draft
behavior. This does not introduce an exactly-once guarantee for external effects
or repair unrelated pre-existing ingestion failure windows.

## Scope and concurrency guards

The production callbacks are bound to `run_history_sync`'s exact claimed epoch
guard, including the epoch update on an expired incremental cursor's transition
to resync. Every provider attempt and every page ingestion revalidates the
checkpoint, organization, connection identity, mail connection, credential
generation, binding epoch, actor authority and worker claim. Message reads are
also checked again after the provider returns and before ingestion/backfill.
New message ingestion rechecks immediately before origin creation and ingest.
Failure handling rechecks authority rather than swallowing a stale claim as an
ordinary failed message and continuing the page.

The callback guard expires authority cache entries. Pending accepted backfill
changes are flushed before that refresh so they are not silently discarded;
guard rejection still rolls the transaction back. Completion remains fenced by
the existing checkpoint CAS. These boundary checks do not claim atomicity with
provider calls or eliminate every interleaving inside downstream ingestion.

No changes were made to models, schema revision `a54f001c0a18`, worker handlers,
provider adapters, CI or frontend. No real provider/API/mail operation,
credential, outgoing message, external AI, push, merge or deployment was used.

## Verification

Baseline was run before editing: **33 passed in 12.13 s**.

Final targeted verification: **60 passed in 17.57 s**:

```text
tests/test_mvp2_gmail_paged_resync.py
tests/test_mvp2_gmail_history_cursor.py
tests/test_mvp2_gmail_history_acceptance.py
tests/test_mvp2_gmail_provider_fault_acceptance.py
--basetemp=.pytest-paged-resync-final3
```

Adjacent Gmail regression verification: **65 passed, 1 skipped in 11.01 s**:

```text
tests/test_gmail_adapter.py
tests/test_gmail_automation.py
tests/test_gmail_project_validation.py
tests/test_mvp2_gmail_history_cursor_postgres.py
tests/test_v54_gmail_attachment_staging.py
tests/test_v54_gmail_a05_wiring.py
--basetemp=.pytest-paged-resync-regression
```

Both were run from `backend` using
`C:/Users/dpush/OneDrive/Документы/ChatGPT/Workspace/.venv-pu-workspace-tests/Scripts/python.exe`
with `-X utf8 -m pytest -q`. `git diff --check` passed. The existing PostgreSQL
cursor test is skipped without its explicitly isolated PostgreSQL test database;
no new skip was introduced. The first new-test run exposed fixture counting
assumptions because the common fixture already seeds a Message; assertions were
corrected to count the exact synthetic provider IDs.

Coverage includes 201-reference streaming, a real offline worker ingesting 101
distinct messages and a repeated page, empty continuation/final pages, malformed
and repeated tokens, malformed/oversized pages, page/message budget exhaustion,
partial-page failure with committed-row deduplication on replay, terminal replay,
mid-scan new arrivals, post-list/post-get credential/binding/epoch/lease changes,
recovered lease after a committed first page and 404-to-resync epoch rebinding.

Status: **OFFLINE/SQLITE targeted checks pass; live Gmail and PostgreSQL remain
conditional**. No production PASS is claimed. The parent integration stream must
run the full backend suite after applying the commit.
