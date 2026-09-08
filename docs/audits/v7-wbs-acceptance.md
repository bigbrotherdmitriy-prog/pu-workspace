# v7 WBS executable acceptance contract

Date: 2026-09-08. Base: `73178e0db3be8f4f2aadde339c17725b605e88be`.

## Outcome

This branch adds an executable, product-independent acceptance contract for the
mandatory MVP4 WBS gap. It does **not** claim that WBS is implemented. The validator
can consume a safe JSON projection from the future API or PostgreSQL harness and
does not import application code that does not exist yet.

The canonical hierarchy is:

`project → phase → work → subwork → milestone`.

Additional leaf work/subwork nodes are permitted. A node with children is a summary;
summary nodes cannot carry duration, dependencies or financial links. A milestone is
a leaf with duration zero and equal start/finish dates.

## Fixed acceptance semantics

- IDs are opaque and unique within one projection; all nodes have the same project
  and baseline scope.
- There is exactly one project root. Parent kinds are checked, cycles and disconnected
  nodes are rejected.
- Sibling order is unique and contiguous from zero. API output is deterministic
  preorder, ordered by that sibling order.
- Summary dates are the minimum start and maximum finish of descendant leaves.
- Summary progress is duration-weighted over leaves, rounded half-up to two decimals.
  A milestone has weight one. This is scheduling progress, not financial aggregation.
- Financial links are permitted only on leaves. Clone remaps every node identity and
  does not copy financial links.
- A parent with surviving children cannot be deleted. Root, financially linked,
  actualised or evidence-backed nodes cannot be deleted.
- The proposed additive migration is exactly `a54f001c0a20 → a54f001c0a21`, with
  `wbs_parent_id`, `wbs_order`, and `is_summary`; existing migrations are immutable.
  Existing flat rows must be preserved as deterministically ordered root children.

## Commands and evidence

Run the self-contained fixture:

```powershell
python scripts/ci/v7_wbs_acceptance.py
```

Validate an API/harness projection:

```powershell
python scripts/ci/v7_wbs_acceptance.py path/to/safe-wbs-projection.json
```

Run contract tests:

```powershell
python -m pytest -q scripts/ci/test_v7_wbs_acceptance.py
```

The JSON projection must not contain document bodies, provider tokens, DSNs or
personal data. Synthetic values in the embedded fixture use `.test`-free labels and
numeric local scopes only.

## PostgreSQL conditional protocol

These cases are mandatory on a clean isolated PostgreSQL database after implementation:

1. upgrade from `a54f001c0a20` to `a54f001c0a21`;
2. preservation of flat rows as ordered root children;
3. concurrent reparent guarded by graph-revision CAS;
4. concurrent sibling reorder with exactly one winner;
5. transactionally isolated clone/original;
6. finance-link race with summary conversion fails closed;
7. parent-delete race with child insert fails closed;
8. restart preserves hierarchy and derived rollups.

For each case record the command, baseline ID, graph revisions before/after, expected
and actual safe status. Do not record titles, provider identifiers, SQL, DSN or source
content. Until these execute, PostgreSQL status is **CONDITIONAL**, not PASS.

## Integration handoff

The future implementation should serialize its read model into this validator, then
replace/add application tests without weakening any error condition. If implementation
chooses different column names, it must update the proposed manifest and this contract
in the same reviewed change. Product code, migrations, workflows, production and the
original dirty worktree were not changed here.

## Local verification result

- WBS contract suite: **23 passed**.
- Embedded fixture CLI: **PASS** with 6 nodes and 2 leaves.
- Whole `scripts/ci` collection: **353 passed, 7 failed** in the local Windows
  environment. Six failures require a working WSL Bash which is unavailable; one
  unrelated temporary-repository commit exceeded its existing ten-second timeout.
  These failures do not import or execute the WBS files and are not represented as
  a full-suite PASS.
- PostgreSQL execution: **CONDITIONAL**; no isolated PostgreSQL DSN was used.
