# aws-cli — Agent Guide

S3 Glacier restore pipeline (`<SRC_BUCKET>` → `<DST_BUCKET>`).
Python 3.11+ + boto3, stdlib only at runtime. No secrets in repo.

## Map (read only what the task needs)

| I need...              | Read                          |
| ---------------------- | ----------------------------- |
| Product vision + usage | `README.md`                   |
| Commands, gates, gotchas | this file (below)           |
| Restore runbook        | `docs/RUNBOOK.md`             |
| Audit log standard     | `README.md` → Audit trail     |
| Code layout            | `src/s3_glacier_restore/`          |

## Commands

Buckets are mandatory: export `S3_RESTORE_SRC_BUCKET` / `S3_RESTORE_DST_BUCKET`, or pass
`--src-bucket` / `--dst-bucket`. No bucket defaults exist; omission exits 2.

```bash
uv sync                                     # install (venv local .venv + uv.lock)
uv run python -m s3_glacier_restore --help       # CLI entry

uv run python -m s3_glacier_restore plan --serial 1234-5678-90 --profile verificar
uv run python -m s3_glacier_restore perms --serial 1234-5678-90 --profile verificar
uv run python -m s3_glacier_restore restore --serial 1234-5678-90 --profile verificar --dry-run
uv run python -m s3_glacier_restore status --serial 1234-5678-90 --profile verificar
uv run python -m s3_glacier_restore copy --serial 1234-5678-90 --profile verificar --dry-run
uv run python -m s3_glacier_restore verify --serial 1234-5678-90 --profile verificar
uv run python -m s3_glacier_restore download --serial 1234-5678-90 --profile verificar --dest ./restored-1234-5678-90

uv run python -m s3_glacier_restore auto --serial 1234-5678-90 --profile verificar   # full chain, no download
uv run python -m s3_glacier_restore batch 1234-5678-90 1234-5678-91 --profile verificar --max-parallel 3

uv run pytest                 # unit suite (no AWS calls, mocked)
uv run ruff check src tests   # lint (zero warnings)
uv run ruff format --check src tests    # format check
```

## Gates (real evidence before commit)

`uv run ruff check` + `uv run ruff format --check` + `uv run pytest` green. Conventional commits
(`feat:`, `fix:`...), explicit paths, never `git add -A`. No secret may appear
in code, logs, reports or commit — grep for `AKIA|Secret|password` before commit.

## Gotchas (learned the hard way — respect them)

- **PowerShell strips embedded double quotes** when calling native binaries.
  Never pass JSON inline to `aws` CLI from PS. This project uses boto3 directly,
  so the bug class is gone — do not reintroduce subprocess `aws` calls.
- **AWS CLI auto-pagination nests `--query` results** per page (`[[...],[...]]`).
  We paginate manually with `ContinuationToken` and no `--query`-style
  projection. Do not add JMESPath projections over paginated listings.
- **`list-objects-v2` renders null `StorageClass` as missing.** 0-byte folder
  markers have no `StorageClass` — treat as `FOLDER_MARKER`, exclude from
  class census and from copy/restore loops.
- **`HeadObject` on Deep Archive returns 200.** Only `GET` validates the class
  (`InvalidObjectState` / `Storage class does not support synchronous
  retrieval`). A 200 on head does NOT mean the object is readable.
- **Restore does not change `StorageClass`.** The object stays `DEEP_ARCHIVE`;
  only the `Restore` header flips to `ongoing-request="false"`. Never skip a
  copy just because the class is archived — HEAD it first. `auto` enforces a
  full `status` gate before `copy` for this reason.
- **`auto`/`batch` defaults differ from standalone steps on purpose.** The
  orchestrator uses `--max-list 700000` and `--workers 20` (`_auto_common`);
  standalone `copy`/`restore`/`status` use `--max-list 20000` and `--workers 8`,
  while standalone `download` uses `--max-list 20000` and is sequential (no
  `--workers`). Keep both aligned with `AutoOptions`/`_auto_common` when adding
  flags — a silent 20k cap on a 700k-object serial would hand off an incomplete
  bucket. `write_handoff` must carry `--max-list` into the download command for
  the same reason.
- **DataSync ARN validation runs before IAM.** A probe with a malformed ARN
  returns `InvalidRequestException` (regex) even without permission. To test
  IAM, use a well-formed but nonexistent ARN and read `AccessDenied` vs
  `... is not found`.
- **Explicit Deny beats Allow.** `pipeline-restore` has an explicit deny on
  `s3:RestoreObject`; adding Allows does not help until the Deny is removed.
- **`auto` checkpoint belongs to the orchestrator.** Isolated `restore`/`copy`
  commands do not advance it: to skip an already completed restore, continue
  with the isolated steps or set `phase: wait` in
  `state/<SERIAL>-<profile>.json` before calling `auto`.
- **PowerShell wrappers:** a function that writes to the output stream and then
  `return $code` returns ALL accumulated output, not just the exit code —
  `$rc -ne 0` then fires a false abort after a successful step. Run the native
  command directly and read `$LASTEXITCODE` immediately after it; log via the
  pipeline (`ForEach-Object`/`Add-Content`), never from an output-returning
  helper.
- **Destination lifecycle expires in 30 days** (`Delete-after-30d` on
  `<DST_BUCKET>`). Tell the customer; the copy is time-boxed.
- **Deep Archive has 180-day minimum retention.** Deleting source early bills
  the difference. Default to copy-then-verify, never move-then-delete.

## Conventions

- Code, comments, commits in English. User-facing console output in pt-BR.
- Strict typing (`from __future__ import annotations`, no `Any` in public
  signatures). stdlib + boto3 only at runtime.
- Every mutating run writes `logs/<run_id>.jsonl` (audit trail) and
  `reports/<serial>-<run_id>.md` (evidence). Read-only runs (`plan`,
  `status`, `verify`, `perms`) also log — audit covers reads too.
- Never log secret material. Actor = IAM ARN, never key material.
