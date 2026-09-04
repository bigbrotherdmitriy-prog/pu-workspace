# v5.4 mailbox rollout controls

## Scope

This change adds an operator-only control surface for the existing
`v54_mailbox_cutover_flags` rows. It adds no table, column, migration, provider
call, production activation, secret, or real mailbox data.

## Control contract

`PATCH /integrations/mailbox-rollout` changes exactly one flag. The request must
pin the organization, mail connection UUID, current credential generation,
binding epoch, and mailbox authority version. `If-Match: "<record_version>"`
pins the flags row. The only accepted approval value is `CONFIRM`; there is no
AUTO mode and no batch mutation.

Promotion follows the fail-closed lattice:

1. `shadow_write` and `shadow_read_compare` (in either order)
2. `pilot_write`
3. `primary_read`
4. `actions`

Rollback disables one flag per request and is accepted only when the resulting
state remains in that lattice. Consequently operators must disable `actions`,
then `primary_read`, then `pilot_write`, before either shadow flag.

The service locks and verifies the exact active Gmail connection, verified
identity, current credential generation and binding epoch. A revoked identity,
revoked connection, rotated (non-current) generation, stale CAS version, stale
authority version, expired authority, or service principal fails closed.
Global administrator status grants no bypass: the authenticated user still
needs an active mailbox-local `rollout` authority row.

Every successful one-flag transition increments `record_version` with a SQL CAS
and appends one audit record in the same transaction. The audit contains only
the flag, boolean direction, before/after versions, and internal actor user ID;
it contains no email, Google subject, provider locator, provider identifier,
credential row, access token, refresh token, or request payload.

Runtime resolution independently rejects flag combinations outside the same
lattice. Credential rotation creates a default-false row for the new generation
and makes the prior generation ineligible because it is no longer the exact
identity generation. Existing mailbox-cohort detection therefore remains
fail-closed and cannot fall back to the legacy project token.

## Acceptance evidence

The regression suite covers strict DTO/header validation, rejection of AUTO,
the full promotion and rollback order, one audit per approval, no-op and
prerequisite rejection, stale and cross-scope pins, global-admin and service
principal denial, expired/revoked/stale authority, rotation/revocation closure,
audit data minimization, and runtime lattice validation.

Live provider and production execution are intentionally out of scope.

Local acceptance on Windows:

- rollout control regression/security tests: `14 passed`;
- rollout, mailbox identity, integrations, and Gmail regression subset:
  `91 passed`;
- full backend with a worktree-local pytest base directory:
  `980 passed, 11 skipped`;
- the first full run used the host-global pytest temp directory and produced
  only `WinError 5` fixture setup errors; the identical suite passed after
  selecting the writable worktree-local base directory;
- Python compilation, Alembic single-head/offline coverage, and
  `git diff --check` are part of the final integration checks.
