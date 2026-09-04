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
docker build \
  --label com.pu-workspace.primary.revision=<full-commit-sha> \
  -t app-backend:<full-commit-sha> \
  /opt/pu-workspace-primary/releases/<full-commit-sha>/backend
```

## Candidate activation

Run as the owner of the root. Supply a verified PostgreSQL `pg_dump -Fc` only on
the first activation:

```sh
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
reports the expected full commit SHA. Workers and the scheduler intentionally
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
