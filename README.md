# glacier-thaw

S3 pipeline to restore objects from Glacier: copies
`s3://<SRC_BUCKET>/<PREFIX>/` to `s3://<DST_BUCKET>/<PREFIX>/`
in `STANDARD` and unlocks the client download.

Each run is scoped to a bucket+prefix pair. The legacy `<SERIAL>/`
format (`1234-5678-90/`) still works, but any prefix is valid: paste
`s3://bucket/prefix/` and the label is inferred from the last segment
(`--serial`/`--label` are only required when the prefix is the bucket root).

> **Why direct S3 instead of DataSync?** The operational credentials have full
> S3 access and zero DataSync access. `aws s3 cp` with `--storage-class
> STANDARD` performs the same copy without depending on DataSync roles.

## Stack

| Layer    | Technology                                     |
| -------- | ---------------------------------------------- |
| Language | Python 3.11+ via `uv` (venv `.venv` + `uv.lock`) |
| AWS      | boto3 (S3: list/head/restore/copy/download)    |
| CLI      | argparse stdlib (`uv run python -m s3_glacier_restore`) |
| UI       | local HTTP server (loopback) + HTML/CSS/JS embedded in `ui/index.html` |
| Exe      | PyInstaller onefile (`s3-download-client.exe`), built once |
| Logs     | auditable JSONL (`logs/<run_id>.jsonl`)        |
| Tests    | mocked pytest (`uv run pytest`)                |
| Lint     | ruff, zero tolerance (`uv run ruff check`)     |

## Architecture

```
src/s3_glacier_restore/
├── cli.py         → subcommands (perms/plan/restore/status/copy/verify/download/auto/batch)
├── auto.py        → auto orchestration (checkpoint, wait, status gate, batch)
├── config.py      → RestoreConfig (no secrets; profile/region/buckets)
├── aws_factory.py → boto3 session per --profile, retries, identity (sts)
├── s3ops.py       → census, head, restore, copy, download (pure boto3)
├── audit.py       → AuditLogger: JSONL + console, run_id, no secrets
├── report.py      → per-run .md report (evidence for the supervisor)
└── ui/            → entry/server/runner/child/auth: admin and client panels in the exe
```

## Quickstart

Buckets are required: use the `--src-bucket`/`--dst-bucket` flags or set them
once in your shell (there is no built-in default):

```powershell
$env:S3_RESTORE_SRC_BUCKET = "<SRC_BUCKET>"
$env:S3_RESTORE_DST_BUCKET = "<DST_BUCKET>"
```

```bash
uv sync   # creates .venv and installs everything (runtime + dev)

# 0. Preflight — REQUIRED before anything (read-only)
uv run python -m s3_glacier_restore plan --serial 1234-5678-90 --profile verificar

# Generic form (any prefix; label inferred from the last segment):
uv run python -m s3_glacier_restore plan \
  --src s3://origin-bkt/1234-5678-90/ --dst s3://dest-bkt/1234-5678-90/ \
  --profile verificar

# 1. If plan blocks → restore (Deep Archive: ~12h Standard / ~48h Bulk)
uv run python -m s3_glacier_restore restore --serial 1234-5678-90 --profile verificar --dry-run
uv run python -m s3_glacier_restore restore --serial 1234-5678-90 --profile verificar
uv run python -m s3_glacier_restore status --serial 1234-5678-90 --profile verificar

# 2. Copy to the public bucket in STANDARD
uv run python -m s3_glacier_restore copy --serial 1234-5678-90 --profile verificar --dry-run
uv run python -m s3_glacier_restore copy --serial 1234-5678-90 --profile verificar

# 3. Verify before delivering (refuses if any object is archived or the destination is empty)
uv run python -m s3_glacier_restore verify --serial 1234-5678-90 --profile verificar --max-list 700000

# 3b. Check source vs destination parity (missing, extra, divergent + ages)
uv run python -m s3_glacier_restore parity --serial 1234-5678-90 --profile verificar --max-list 700000

# 4. Download locally (everything or specific keys)
#    (if you used `auto`, the ready-made command is in handoff/<SERIAL>-download.txt)
uv run python -m s3_glacier_restore download --serial 1234-5678-90 --profile verificar --dest ./restored-1234-5678-90 --max-list 700000
uv run python -m s3_glacier_restore download --serial 1234-5678-90 --profile verificar --dest ./case --key 1234-5678-90/Images/photo.png --key 1234-5678-90/Database/dump.sql

# Large prefix: --max-list is mandatory. The isolated default is 20000;
# plan, verify and parity refuse truncated results (INCONCLUSIVE) —
# without --max-list 700000 you only see the first 20k keys.

# 5. Audit the credential's permissions
uv run python -m s3_glacier_restore perms --serial 1234-5678-90 --profile verificar
```

## Delivery to the client (single exe)

`s3-download-client.exe` is built **once** and serves every client. Source,
destination (`s3://bucket/prefix/` URIs) and label are entered in the UI: there
is no per-client manifest and no rebuild per prefix.

### Client prerequisites

- `s3:ListBucket` and `s3:GetObject` on the destination bucket; the client restores and copies nothing.
- Enough free disk space for the `1234-5678-90` serial (the UI checks with a 10% margin).
- The destination bucket deletes objects after 30 days — download and keep the local copy.

### Build (technical, once)

```bash
# default build password: eajdias
uv run --group build python scripts/build_client.py

# custom password:
uv run --group build python scripts/build_client.py --admin-password <ADMIN_PASSWORD>
```

Without `--admin-password`/`S3_RESTORE_ADMIN_PASSWORD`, the build uses the
default password `eajdias`. The password becomes `ADMIN_SALT` +
`ADMIN_PASSWORD_SHA256` in `src/s3_glacier_restore/ui/_build_config.py`
(gitignored); the plaintext value is never written. After logging in, the admin
panel allows changing the password; the new digest is stored in
`%LOCALAPPDATA%\s3-glacier-restore\admin-auth.json` (this machine only; delete
the file to fall back to the build password). `S3_RESTORE_SIGNTOOL` +
`S3_RESTORE_CERT_SUBJ` enable signing; without it, SmartScreen may block the
first launch.

### Admin panel (technical)

Open the exe → **Administrador** → password (default `eajdias` or the one set at
build) → source and destination (`s3://bucket/prefix/`, optional label) → run
`auto` (or the isolated steps). The panel uses the machine's default AWS CLI
profile (`default`), never asks for client credentials, and **never downloads**.
`auto` and `batch` already come with `--max-list 700000`.

### Client panel

1. **Cliente** → paste the block (values below are placeholders, not real credentials):

   ```text
   AWS_ACCESS_KEY_ID=<ACCESS_KEY_ID>
   AWS_SECRET_ACCESS_KEY=<SECRET_ACCESS_KEY>
   AWS_DEFAULT_REGION=sa-east-1
   AWS_DEFAULT_OUTPUT=json
   AWS_SESSION_TOKEN=<SESSION_TOKEN>
   ```

   `AWS_SESSION_TOKEN` is optional. The block only lives in the child process
   `env` and the field is cleared when the download starts; if the key is
   rotated, paste the new block — no rebuild needed.

2. Enter the destination (`s3://bucket/prefix/`) and the label, if the prefix is
   generic → **Validar** (lists folders live).
3. Select everything or specific folders → **Continuar** (sampled HEAD: 5 per folder).
4. Choose the local folder → **Verificar espaço** → **Iniciar download**. Same-name folders
   are merged, same-name files are overwritten, and nothing is deleted.

The download trail and report live in
`%LOCALAPPDATA%\s3-glacier-restore\logs\` and `reports\`; the UI port in
`%LOCALAPPDATA%\s3-glacier-restore\ui.port`. Admin commands write to
`logs\`/`reports\` relative to the exe's working directory, unless
`--log-dir`/`--report-dir` are given in the extra arguments field.

## Automatic mode (`auto` / `batch`)

A single command runs the whole chain and **stops before the download**
(which is manual):

```bash
uv run python -m s3_glacier_restore auto --serial <SERIAL> --profile verificar

# Generic (label inferred from `2024/`):
uv run python -m s3_glacier_restore auto \
  --src s3://origin-bkt/backups/2024/ --dst s3://dest-bkt/delivery/2024/ \
  --profile verificar
```

Sequence: `restore` → wait 4h → `status` repeat until 100% → `copy` in
`STANDARD` → `verify` → writes `handoff/<SERIAL>-download.txt` and
`state/<SERIAL>.done`. `download` is **never** triggered.

The `status` gate is mandatory: restore does **not** change the
`StorageClass`, so copying without confirming the `Restore` header would
produce an empty destination. To bypass the gate (exploratory use),
`--force-copy`.

Checkpoint in `state/<SERIAL>-<profile>.json`: interrupted? Run the same
command again and it resumes from the recorded `phase`
(`restore`/`wait`/`copy`/`verify`/`handoff`). There is no per-phase
`status=done`: `status` is for the whole run (`done` ends it; `failed`
requires `--force`). `--dry-run` simulates `restore`/`copy` (the `status` gate
keeps reading via HEAD; `verify` and the handoff remain real). Timing knobs:
`--wait-seconds`, `--poll-seconds`, `--max-polls`.

Resume is per-`auto`, not per-project: if you ran `restore`/`copy` through the
isolated commands, it does not inherit that progress and starts at `restore`.
To continue without repeating the restore, keep going with the isolated steps
or set `phase` to `wait` in the checkpoint before calling `auto`.

`auto`/`batch` use `--workers 20` and `--max-list 700000` by default (aligned
with `AutoOptions`); isolated `copy`, `restore` and `status` use 8 and 20000.

Multiple clients at the same time (each with its own state, log and handoff):

```bash
uv run python -m s3_glacier_restore batch 1234-5678-90 1234-5678-91 1234-5678-92 \
  --profile verificar --max-parallel 3

# Generic pairs (each item carries its own URIs):
uv run python -m s3_glacier_restore batch \
  "s3://origin-bkt/backups/2024=s3://dest-bkt/delivery/2024" \
  --profile verificar
```

Manual equivalent (after `verify` releases):

```bash
aws s3 sync s3://<DST_BUCKET>/<SERIAL>/ ./restored-<SERIAL>/ --region sa-east-1
```

## Audit trail (traceability)

Every execution generates a `run_id` (`<serial>-<utc>-<8hex>`) and two artifacts:

- `logs/<run_id>.jsonl` — one JSON event per line (action, resource, result,
  error, duration). The auditable source of truth.
- `reports/<serial>-<run_id>.md` — human-readable summary with the same
  information, ready to forward.

In `auto`/`batch`, the orchestration also writes its own log (`auto:run`,
`auto:phase:*`) with its own `run_id`, in addition to the `restore`/`copy`/`verify` logs.

### Event pattern

```json
{"ts":"2026-11-24T15:00:00.123Z","level":"INFO","run_id":"...","serial":"...","profile":"verificar","account":"<ACCOUNT_ID>","actor":"arn:aws:iam::...:user/...","step":"copy","action":"s3:CopyObject","resource":"s3://bucket/key","result":"ok","error_code":null,"error_msg":null,"duration_ms":123,"extra":{"storage_class":"STANDARD"}}
```

Fields and rules:

| Field | Rule |
|---|---|
| `ts` | ISO-8601 UTC, milliseconds, `Z` suffix |
| `level` | `INFO` ok/skip, `WARN` recoverable, `ERROR` failure |
| `result` | `ok` \| `fail` \| `skip` \| `blocked` \| `dry_run` |
| `error_code` | AWS code (`AccessDenied`, `InvalidObjectState`...) or `null` |
| `error_msg` | exact AWS message, no truncating the essentials |
| `resource` | `s3://bucket/key` for object operations; run/auto/perms events use `serial:<SERIAL>`, an ARN or a profile |
| secrets | **forbidden**: never access key, secret, token or password |

The console is pt-BR and summarized; the JSONL is structured English and
complete. `--dry-run` records `result: dry_run` without performing the mutation.

## Configuration

| Item | Value |
|---|---|
| Region | `sa-east-1` (default, overridable with `--region`) |
| Source | `<SRC_BUCKET>` — `--src-bucket` or `S3_RESTORE_SRC_BUCKET` |
| Destination | `<DST_BUCKET>` — `--dst-bucket` or `S3_RESTORE_DST_BUCKET` |
| Destination class | `STANDARD` (the only one the client downloads) |
| Source lifecycle | `30d STANDARD_IA` → `90d GLACIER_IR` → `180d DEEP_ARCHIVE` |
| Destination lifecycle | `Delete-after-30d` (deletes after 30 days) |

Credentials live in `%USERPROFILE%\.aws\credentials` via `aws configure`.
The project only receives `--profile`; never a key or secret.

## Documentation

- Operational runbook: [`docs/RUNBOOK.md`](docs/RUNBOOK.md)
- Agent guide: [`AGENTS.md`](AGENTS.md)
