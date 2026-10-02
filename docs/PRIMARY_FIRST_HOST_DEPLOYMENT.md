# First-host primary deployment

`scripts/deploy-primary-first-host.sh` prepares a future primary application on
a new host without changing DNS, TLS, Caddy, the old production server, or the
existing staging Compose project. The candidate is reachable only at a loopback
port until a separate, reviewed cutover changes the reverse proxy.

## Safety contract

- The root is configurable and must be an existing canonical directory owned by
  the deploy user. `/opt/pu-workspace-primary` and
  `/srv/pu-workspace-primary` are suitable examples.
- A private host marker is mandatory. This prevents accidentally running the
  script on the legacy production layout.
- The Compose project, container prefix, database volume and loopback port are
  dedicated to this stack. Production (`app`, port `3000`) and staging
  (`puw-staging`, port `3010`) identifiers are refused.
- The candidate has no public URL and the script performs only a read-only smoke
  against `127.0.0.1`. It never changes or contacts a production hostname.
- Workers and the scheduler are placed behind the `cutover` Compose profile and
  remain stopped during candidate validation, so restored queued jobs and timed
  integrations cannot run in parallel with the old production server.
- Secrets are read from `shared/.env.primary`, copied to a per-release runtime
  file with mode `0600`, and never printed. Keep the same `APP_SECRET_KEY` and
  `TOKEN_ENCRYPTION_KEY` when importing an existing production database.
- Later updates refuse implicit rotation of the database password, application
  signing key, or token-encryption key. Rotate those only with a separate,
  reviewed data migration procedure.
- An initial PostgreSQL custom-format dump may be restored only when the
  dedicated volume does not yet exist. Existing data is backed up and test
  restored before later application updates.
- `current` is replaced atomically. A failed smoke restores the previous
  application only when its migrations prove compatible with the live schema.
  Database rollback is never guessed or performed automatically.

## One-time host preparation

Create the root as an administrator, then give it to the unprivileged deploy
account. The following values are examples and must match the later invocation:

```sh
install -d -m 700 -o pu-primary -g pu-primary \
  /opt/pu-workspace-primary \
  /opt/pu-workspace-primary/shared \
  /opt/pu-workspace-primary/releases

install -m 600 -o pu-primary -g pu-primary /dev/stdin \
  /opt/pu-workspace-primary/shared/.pu-primary-host <<'EOF'
PU_WORKSPACE_NEW_PRIMARY=1
PRIMARY_PROJECT=puw-primary-next
PRIMARY_PORT=3020
PRIMARY_VOLUME=puw-primary-next_primary_data
EOF
```

Transfer the production environment through an encrypted administrative
channel into `/opt/pu-workspace-primary/shared/.env.primary` with owner
`pu-primary` and mode `0600`. Do not paste or echo its contents into CI logs.
The source release must already exist under
`/opt/pu-workspace-primary/releases/<full-commit-sha>`, and its image must
already be loaded or built locally. Pin both artifacts explicitly:

```sh
printf '%s\n' '<full-commit-sha>' > \
  /opt/pu-workspace-primary/releases/<full-commit-sha>/.pu-primary-release
chmod 400 /opt/pu-workspace-primary/releases/<full-commit-sha>/.pu-primary-release
cd /opt/pu-workspace-primary/releases/<full-commit-sha>
docker build -f backend/Dockerfile --target runtime \
  --build-arg PU_RELEASE_REVISION=<full-commit-sha> \
  --build-arg PU_BUILD_MODE=production \
  -t app-backend:<full-commit-sha> .
```

## Candidate activation

Run as the owner of the root. Supply a verified PostgreSQL `pg_dump -Fc` only on
the first activation:

Always start from the exact release directory, **not `/root`**. Switching user
with `runuser` does not necessarily change the working directory; an inaccessible
inherited `/root` caused a permission failure during the V6-00a deployment.
Do not patch the deploy script or leave a temporary wrapper in the release.
When invoking from a root shell, first `cd` to the release, then use the deploy
account that owns the primary root (currently `pu-staging` on the approved host).

```sh
cd /opt/pu-workspace-primary/releases/<full-commit-sha>
scripts/deploy-primary-first-host.sh \
  /opt/pu-workspace-primary \
  /opt/pu-workspace-primary/releases/<full-commit-sha> \
  app-backend:<full-commit-sha> \
  puw-primary-next \
  3020 \
  /opt/pu-workspace-primary/import/production.dump
```

For a fresh empty database, omit the final argument. For a later release update,
omit it as well; the script detects the existing dedicated volume, creates and
test-restores a backup, then performs the application switch.

Success means the database and backend are healthy and the loopback smoke
reports the expected full commit SHA. The candidate manifest and **all** static
files have also been verified against the immutable image ID and the loopback
HTTP server. A new image without a valid manifest fails before database start.
Workers and the scheduler intentionally
remain stopped. It does **not** mean the public cutover is complete.

## Verification and cutover boundary

Before public cutover, verify from the host:

```sh
readlink -f /opt/pu-workspace-primary/current
docker compose --env-file \
  /opt/pu-workspace-primary/runtime/<full-commit-sha>/.env.primary \
  -f /opt/pu-workspace-primary/current/infra/primary/docker-compose.yml \
  -p puw-primary-next ps
curl --noproxy '*' http://127.0.0.1:3020/api/readiness
```

DNS TTL reduction, landing-page migration, Caddy configuration, certificate
staging, final database freeze/delta, OAuth verification and the 24–48 hour old
host rollback window are separate cutover steps. Do not point DNS at this host
until those steps and a browser acceptance run have succeeded.

Only inside the approved cutover window, after the old background services have
been stopped and the final database transfer has completed, activate the new
background services with the same pinned runtime environment:

```sh
docker compose --profile cutover --env-file \
  /opt/pu-workspace-primary/runtime/<full-commit-sha>/.env.primary \
  -f /opt/pu-workspace-primary/current/infra/primary/docker-compose.yml \
  -p puw-primary-next up -d --no-build --wait worker scheduler
```

## Stage 1: image-built frontend and public integrity gate

See [approved ADR](architecture/ADR-REACT-DIST-IMAGE-BUILD-RU.md).
`backend/app/react_dist` is still tracked during stage 1, but both build
contexts exclude it. The Node stage builds only frontend sources with the frozen
lockfile. Generated output is copied after backend sources, and the complete
manifest is stored at `/app/frontend-build-manifest.json`, outside `/new/`.

Record the source archive SHA-256 and image ID before activation; save the image
artifact and the previous image ID before any switch. Do not replace a previously
accepted image/receipt with another build of the same SHA. Preserve at least the
current and two previous accepted image/receipt/archive/runtime sets. Database
backup and test restoration remain mandatory and are performed by the first-host
script on updates. No schema change is introduced by this packaging change.

After approved cutover/start of both workers and the scheduler, run the following
as the deploy account **from the candidate release directory**. `image_id` must
be the already recorded candidate image ID, not a tag resolved after cutover:

```sh
verification_stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
python3 scripts/verify_image_static.py \
  --image "$image_id" --expected-image-id "$image_id" \
  --revision "$release_sha" --archive-sha256 "$archive_sha256" \
  --base-url https://puworkspace.ru --compose-project puw-primary-next \
  --readiness-attempts 25 --readiness-interval 5 --readiness-timeout 180 \
  --receipt "/opt/pu-workspace-primary/static-receipts/${release_sha}-${verification_stamp}-https.json"
```

This gate checks exactly one backend, two durable workers and one scheduler on
the same immutable image and SHA; `GMAIL_AUTO_SYNC_ENABLED=true` on each; public
`/api/status`; `readiness ready:true` and every required check; HTTP 200 without
redirects, valid TLS, identity encoding, MIME type, size and SHA-256 for **every**
manifest file (including videos, lazy chunks and service worker). For `index.html`
the requested URL is `/new/`. The reference is extracted from the exact image
using a disposable container that is **never started**, not from Git or a local
frontend build. Receipts are private, created exclusively, and never overwritten.

Compose `--wait` is not a durable heartbeat gate. The verifier waits only when
`ready:false` has a nonempty failed-required set consisting exclusively of
`durable_workers` and/or `durable_scheduler`. Defaults are at most 25 attempts,
5-second pauses (at most 120 seconds of pauses), and a 180-second monotonic wall
deadline. Each API request still has a maximum 30-second timeout, shortened to
the remaining deadline. Every attempt checks public HTTP 200 and the exact
backend revision again. Late readiness, exhausted limits, schema/database/config
failures, wrong revision, malformed responses, auth, TLS, redirects or network
failures stop the gate; they are not retried. All four exact-image components
and Gmail flags are checked again after the wait, before static acceptance.

The private receipt is reserved before verification and records **success and
failure**. Inspect exit status and `verified:true`, not mere file existence.
Failure evidence includes stage/reason, bounded API status/body projections,
request times and elapsed durations, readiness attempts and failed required
checks. Static evidence distinguishes `not_started`, partial verification and
complete success, with expected/observed sizes and SHA-256 where bytes were read.
Arbitrary response extras, secret values, cookies and raw HTML are not recorded;
body hashes and truncation flags identify bounded error responses instead.
An existing receipt is never overwritten, and inability to save diagnostics
does not permit acceptance. Use a new stamp for every invocation. Retain the
deploy log together with receipts: `PRIMARY_CURRENT_SELECTED` records UTC bounds
around atomic symlink replacement so future cutover-to-request timing can be
measured. SIGKILL/power loss cannot guarantee a completed final receipt.

No static request is retried or accepted partially: the original full-file
inventory, TLS, no-redirect, identity-encoding, MIME, size and SHA-256 criteria
remain mandatory. The [01.10 failure investigation](audits/pr109-readiness-failure-2026-10-02.md)
documents the startup heartbeat race and the unmeasured historical candidate
public bytes; current rollback acceptance does not prove past candidate integrity.

Any readiness/image/static failure stops the rollout. Restore the previous full
artifact, not just the symlink or frontend. Do not retry by bypassing checks:

```sh
sh scripts/rollback-primary-release.sh \
  /opt/pu-workspace-primary "$previous_release" "$previous_image_id" \
  puw-primary-next 3020 https://puworkspace.ru
```

The rollback script refuses an unproven schema combination, restores all four
applications on the saved exact image and old private runtime environment,
and rechecks public readiness and all hashes. It never performs DB downgrade or
changes Gmail/AUTO/access-control flags. For a pre-stage-1 image without a manifest,
only the explicit legacy rollback mode extracts its whole static tree and makes
an external inventory; it never falls back to `react_dist` in Git. Keep the new
verifier release directory available even after the symlink is restored.
The retained runtime `PRIMARY_IMAGE` alone is atomically pinned to the accepted
image ID, so later Compose invocations cannot switch back to an old mutable tag.
No secret or execution/access-control flag is modified by this pin.

If rollback fails or the schema/image is unavailable, report the failure; do not
rebuild an old commit and call it the old accepted artifact. The known consequence
of a separately authorized authority migration downgrade (revoked scoped rights
and increased authority version) is unaffected; this script does not do it.

Browser/service-worker cache acceptance remains a separate visual check after
the byte-integrity gate. Stage 2 (untracking generated output) is not authorized
until an actual stage-1 rollout and legacy rollback acceptance are recorded.

## Local development and CI

Local Compose builds the same recipe from the repository root with explicit
`development/local-dev` identity by default. Such images cannot pass production
verification. Alternatively run Vite dev separately, or build locally before
serving the Python `/new/` app; during stage 1 do not commit generated changes in
the tracked `react_dist`. Production requires `PU_BUILD_MODE=production` and a
full lowercase 40-character revision.

CI uses the canonical `backend/Dockerfile` testing target, sharing runtime tools,
fresh static files and manifest with production; it adds test fixtures only.
Docker smoke extracts that image and verifies every asset through its isolated
loopback gateway. The retained `Dockerfile.ci` mirrors the canonical recipe for
compatibility; a contract test prevents divergence. Incremental/hotfix recipes
that inherit `app-backend:latest` are not standard source-built releases.
