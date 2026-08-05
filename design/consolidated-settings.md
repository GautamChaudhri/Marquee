# Consolidated Settings Architecture

Marquee exposes one `/settings` workspace with eight URL-addressable horizontal tabs:

| Tab | Standard purpose | Advanced purpose |
| --- | --- | --- |
| General | Application identity and browser-local appearance | None |
| Connections | TMDB, Radarr, Sonarr, and normal sync cadence | Diagnostics and synchronization policy |
| Media | Logical roots and Arr path mappings | Application-managed paths |
| Posters | Naming, restore order, and healing | Poster storage and healing policy |
| Pipeline | Normal run defaults | Gates, scoring, OCR, models, stacks, and artifacts |
| Taste | Profile and residual thresholds | Calibration, maps, artifacts, axes, and rate limits |
| System | Logs, backups, metrics, retention, and schedules | Database, workers, leases, evidence, resources, and shutdown |
| Access | API/authentication and transport posture | Lockout and defensive request controls |

The rail is keyboard navigable, sticky, horizontally scrollable, and shareable through
`?tab=<tab>&level=<standard|advanced>`. A dirty draft survives tab changes, while a sticky save bar
creates one immutable revision. Browser appearance preferences remain browser-local and apply
immediately.

Poster Text Profiles remain on the Pipeline dashboard because they are part of review. The old
Poster Filename, Restoration, and Heal Scan forms moved to Settings → Posters. Backup, heal,
orphan-cache cleanup, poster reset, and debug-capture cleanup remain contextual operations on the
Pipeline dashboard. Taste artifact generation and management remain on Taste, which links to
Settings → Taste.

Connections is a single full-width service registry. It exposes one named Radarr and one named
Sonarr slot, with `RADARR_INSTANCE_NAME` and `SONARR_INSTANCE_NAME` stored as public revision
metadata. TMDB remains visibly distinct: a Marquee-supplied credential is marked as built-in, while
the same edit flow permits an explicit encrypted custom-token replacement.

## Complete catalog

`CONFIGURATION_CATALOG` is generated from every modeled `Settings` and `PipelineSettings` field.
Coverage tests require exact equality, so adding a Python setting without classifying it fails CI.
The implementation currently classifies 244 fields (131 application plus 113 pipeline):

- 205 revision-managed
- 3 encrypted integration credentials
- 28 deployment/bootstrap-owned
- 8 hidden Text Profile compatibility invariants

Every entry independently records:

- `storage`: `revision`, `secret_store`, `deployment`, or `internal`
- `sensitivity`: `public`, `private`, or `secret`
- `apply_mode`: `hot`, `next_job`, `restart`, or `deployment`
- owner, scope, tab, card section, Standard/Advanced level, control metadata, and visibility

Restart-bound public settings remain valid revision values. The UI labels them clearly; Marquee
does not edit Compose files, mount host paths, use the Docker socket, or restart itself.

## API and revision semantics

- `GET /api/settings` returns the current version/etag, catalog, effective public values, defaults,
  value sources, presence-only secret statuses, and sanitized deployment facts.
- `PUT /api/settings/config` accepts revision-owned fields plus `expected_version`.
- `POST /api/settings/integrations/{provider}/test` tests stored or candidate details without a
  write.
- `PUT /api/settings/integrations/{provider}` tests first, then atomically updates the public URL
  and instance name plus an optional credential replacement.
- `DELETE /api/settings/integrations/{provider}/credential` writes an explicit tombstone so a
  cleared managed credential cannot fall back to a legacy environment value.

The old Pipeline configuration routes and poster/heal Settings payload remain compatibility
adapters for one release. They use the same revision authority, checksum validation, PostgreSQL
notification, cache-repair behavior, no-op detection, and optimistic conflict handling.

On conflict, the frontend reloads the current revision, retains the local draft, and identifies
values changed by the other writer. It never performs a last-write-wins overwrite.

## Credential security

Only `TMDB_READ_ACCESS_TOKEN`, `RADARR_API_KEY`, and `SONARR_API_KEY` are UI-managed secrets. Each
write uses AES-256-GCM with a random 96-bit nonce. Authenticated associated data binds the secret
name, schema version, and monotonically increasing generation. PostgreSQL stores only the name,
ciphertext, nonce, key ID, configured/tombstone flag, generation, and timestamps.

The value-free audit table records secret name, action, actor, generation, and timestamp. Settings
responses expose only `configured`, `source`, `updated_at`, and `generation`; they never expose a
default, value, ciphertext, nonce, key ID, or fingerprint.

The JSON keyring lives outside PostgreSQL and the repository. Compose mounts it only into the API
and worker. The API key and PostgreSQL password are separate bootstrap secrets mounted into the
smallest required service set. The same-origin SvelteKit proxy reads the API key server-side and
rejects cross-origin mutation requests.

Credential forms use blank `autocomplete="new-password"` inputs; blank means unchanged. Client
state is erased after a write and is never persisted in browser storage. Remote credential
operations require HTTPS; loopback HTTP remains available for development. Integration URLs reject
userinfo, queries, fragments, link-local/reserved destinations, and redirects. Provider failures
are replaced with bounded messages that contain neither credentials nor upstream response bodies.

The central job log redactor resolves managed credentials and the effective database URL at use
time. Query-string API authentication is rejected outside the reserved webhook compatibility path.

This design follows [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html),
[OWASP Cryptographic Storage](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html),
[OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), and
[Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/).

## Fresh Compose installation

1. Copy `.env.example` to `.env` and replace only the bootstrap values.
2. Generate the external keyring without printing its material:

   ```bash
   python -m marquee.maintenance generate-settings-keyring \
     --output ~/.config/marquee/settings-keyring.json
   ```

3. Set `MARQUEE_SETTINGS_KEYRING_PATH` in `.env` to that absolute path.
4. Start the stack with the managed-secret override:

   ```bash
   docker compose \
     -f docker/docker-compose.yml \
     -f docker/compose.managed-secrets.yml \
     up -d
   ```

5. Open Connections and enter the provider credentials. Use a real DNS name in
   `MARQUEE_SITE_ADDRESS` for automatic Caddy HTTPS before allowing remote credential changes.

Compose publishes Caddy on loopback unless `MARQUEE_BIND_ADDRESS` is deliberately changed. Set
`MEDIA_PATH_CEILINGS` to the JSON list of container paths actually mounted for media; editable
logical roots and Arr mappings can only narrow that immutable authority. `DATA_PATH_CEILING`
defaults to `/app/data`. Contained compute runs as `JOB_RUNNER_UID`/`JOB_RUNNER_GID` (65532 by
default), cannot read the root-owned mode-`0400` bootstrap/keyring files, and receives only the
credential required by its specific operation over bounded stdin.

## Existing `.env` migration

Do not delete the old file until the migration and a restart have been verified.

1. Restrict it immediately:

   ```bash
   chmod 600 .env
   ```

2. Create/mount the settings keyring as above.
3. Run one migration container with the legacy environment visible:

   ```bash
   docker compose \
     -f docker/docker-compose.yml \
     -f docker/compose.managed-secrets.yml \
     -f docker/compose.legacy-env-import.yml \
     run --rm marquee-api python -m marquee.maintenance migrate-settings
   ```

   The command atomically seeds explicit revision-managed values and imports only the three managed
   credentials. Its JSON output contains counts and the new configuration version, never values.

4. Restart without `compose.legacy-env-import.yml`, verify Connections and the active setting
   sources, then reduce `.env` to `.env.example`'s bootstrap fields.
5. Remove retired variables rather than migrating them: `TVDB_API_KEY`, `LETTERBOX_DOVI_TOOL`,
   `SUBGEN_URL`, `SUBGEN_CALLBACK_TOKEN`, and `SUBTITLE_BACKUP_MODE`.

During this transition release, precedence is managed revision/secret → legacy environment → code
default. The import is idempotent and never overwrites an existing revision value or managed-secret
row.

## Key rotation

1. Back up the database and current keyring.
2. Add a new random 32-byte key under a new key ID, retain all old keys, and set the new ID active.
3. Atomically replace the external keyring file with mode `0600` and restart authorized services.
4. Run:

   ```bash
   python -m marquee.maintenance rotate-settings-keyring
   ```

5. Verify the reported row count and test all configured integrations.
6. Only then remove the retired key from the keyring and restart again.

Authenticated decryption fails closed for missing keys, wrong keys, tampering, nonce changes, name
changes, or generation changes. Rotation preserves cleared tombstones and writes value-free audit
events.

## Runtime convergence

API, worker, and scheduler initialize the database and configuration provider before constructing
services. They resolve the same immutable revision; PostgreSQL notifications refresh each process.
Tested integration URL and credential replacements apply to the next job: the API swaps its client
after commit and workers refresh the encrypted provider at job boundaries. A worker creates one
operation-scoped secret envelope for a contained runner; the runner cannot open PostgreSQL or the
keyring, removes the envelope after reading it, and never embeds credentials in job documents. Job
documents retain their sealed pipeline revision. Logical path translation resolves revision values
on each use, while Settings reports only container-side path health—not host mount sources.
