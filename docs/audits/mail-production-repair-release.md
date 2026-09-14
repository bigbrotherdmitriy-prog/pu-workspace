# Mail production repair candidate — 2026-09-08

Base: main `c59add470f1af6ff32850ada0933d88f6e50505c`.
Observed production backend: `7e1a1d4901931c708d184f805ebee4a845df7dd3`.

Integrates source fixes from 7e90ccf (Gmail modify consent, safe denied moves)
and bac5cdf (bounded selected-folder refresh, including sent). Preserves
ComfortControls, reading-comfort.css and theme initialization from the live
backend source, with the current main v6/v9 import order. Rebuilds react_dist;
no compiled bundle is copied from a different release.

Checks: 193 frontend tests; 26 Chromium synthetic E2E including light/dark,
mobile layout, sent-folder HTTP request/rendering and provider outage;
61 targeted backend mail/OAuth/browser-safety tests. TypeScript and build PASS.
No model, migration, jobs, production credential or email content changes.
Backend checks use synthetic SQLite; PostgreSQL/full regression is a CI gate.

User-visible after release: reconnect the same Google account/project and grant
gmail.modify; then Sent → Get new imports up to 25 sent messages regardless of
the recent-seven-days inbox window. This is not a full history import.

Deployment gates: exact SHA green CI, current-release CAS, exclusive deployment
lock, backup/recovery verification, health/readiness and rollback. Do not replace
the live reading preferences or weaken staging/production separation checks.
Live mailbox mutation is excluded from acceptance: no real send/trash test.
