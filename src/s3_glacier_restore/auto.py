"""Unattended pipeline: restore -> wait/status -> copy -> verify -> handoff.

Each phase is checkpointed so a multi-day run can resume after interruption.
`batch` runs independent serials concurrently; every one keeps its own state,
audit trail and handoff file.
"""

from __future__ import annotations

import json
import sys
import time
from argparse import Namespace
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from .audit import AuditLogger
from .aws_factory import build_session, s3_client
from .cli import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_OK,
    build_parser,
    cmd_copy,
    cmd_restore,
    cmd_verify,
)
from .config import DEFAULT_REGION, DEFAULT_STORAGE_CLASS, RestoreConfig, make_run_id
from .locations import LABEL_RE, infer_label, parse_s3_location
from .s3ops import StatusCounts, collect_status, run_census

PHASES = ("restore", "wait", "copy", "verify", "handoff")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class AutoOptions:
    serial: str
    profile: str = "default"
    region: str = DEFAULT_REGION
    src_bucket: str = ""
    src_prefix: str = ""
    dst_bucket: str = ""
    dst_prefix: str = ""
    storage_class: str = DEFAULT_STORAGE_CLASS
    tier: str = "Standard"
    days: int = 7
    max_list: int = 700000
    workers: int = 20
    wait_seconds: int = 14400
    poll_seconds: int = 3600
    max_polls: int = 24
    state_dir: Path = field(default_factory=lambda: Path("state"))
    handoff_dir: Path = field(default_factory=lambda: Path("handoff"))
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    report_dir: Path = field(default_factory=lambda: Path("reports"))
    force: bool = False
    force_copy: bool = False
    dry_run: bool = False


@dataclass
class AutoState:
    serial: str
    profile: str = ""
    phase: str = "restore"
    status: str = "running"  # running | done | failed
    started_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    attempts: dict[str, int] = field(default_factory=dict)
    run_ids: dict[str, str] = field(default_factory=dict)
    last_status: str = ""
    error: str = ""
    handoff_file: str = ""

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = _now()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, serial: str, profile: str) -> AutoState:
        if not path.exists():
            return cls(serial=serial, profile=profile)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["serial"] = serial
        data["profile"] = profile
        return cls(**data)


def _cfg(opts: AutoOptions, serial: str) -> RestoreConfig:
    return RestoreConfig(
        serial=serial,
        profile=opts.profile,
        region=opts.region,
        src_bucket=opts.src_bucket,
        src_prefix=opts.src_prefix,
        dst_bucket=opts.dst_bucket,
        dst_prefix=opts.dst_prefix,
        dest_storage_class=opts.storage_class,
        restore_days=opts.days,
        restore_tier=opts.tier,
        max_list=opts.max_list,
        dry_run=opts.dry_run,
        log_dir=opts.log_dir,
        report_dir=opts.report_dir,
    )


def _uri(bucket: str, prefix: str) -> str:
    return f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"


def _sub_args(command: str, opts: AutoOptions, serial: str) -> Namespace:
    argv: list[str] = [
        command,
        "--src",
        _uri(opts.src_bucket, opts.src_prefix or f"{serial}/"),
        "--dst",
        _uri(opts.dst_bucket, opts.dst_prefix or f"{serial}/"),
        "--serial",
        serial,
        "--profile",
        opts.profile,
        "--region",
        opts.region,
        "--max-list",
        str(opts.max_list),
        "--log-dir",
        str(opts.log_dir),
        "--report-dir",
        str(opts.report_dir),
    ]
    if command == "restore":
        argv += ["--workers", str(opts.workers), "--tier", opts.tier, "--days", str(opts.days)]
        if opts.dry_run:
            argv.append("--dry-run")
    elif command == "copy":
        argv += ["--workers", str(opts.workers), "--storage-class", opts.storage_class]
        if opts.dry_run:
            argv.append("--dry-run")
    return build_parser().parse_args(argv)


def _default_status(opts: AutoOptions, _s3: object | None) -> StatusCounts:
    session = build_session(opts.profile, opts.region)
    s3 = s3_client(session, opts.region)
    cfg = _cfg(opts, opts.serial)
    census = run_census(s3, cfg.src_bucket, cfg.src_prefix, cfg.max_list)
    if not census.ok:
        return StatusCounts(failed=1, total=1)
    return collect_status(s3, cfg.src_bucket, census.archived, opts.workers)


@dataclass
class AutoCommands:
    """Injectable step functions (keeps orchestration testable without AWS)."""

    restore: Callable[[Namespace], int] = cmd_restore
    status_fn: Callable[[AutoOptions, object | None], StatusCounts] = _default_status
    copy: Callable[[Namespace], int] = cmd_copy
    verify: Callable[[Namespace], int] = cmd_verify


def _run_step(
    fn: Callable[[Namespace], int], command: str, opts: AutoOptions, serial: str
) -> tuple[int, str]:
    """Run one CLI step; return (exit_code, new_run_id)."""
    before = set(opts.log_dir.glob("*.jsonl"))
    code = fn(_sub_args(command, opts, serial))
    added = set(opts.log_dir.glob("*.jsonl")) - before
    run_id = sorted(added)[0].stem if added else ""
    return code, run_id


def write_handoff(opts: AutoOptions, state: AutoState) -> Path:
    """Leave the download command ready for the operator."""
    opts.handoff_dir.mkdir(parents=True, exist_ok=True)
    opts.state_dir.mkdir(parents=True, exist_ok=True)
    src_prefix = opts.src_prefix or f"{opts.serial}/"
    dst_prefix = opts.dst_prefix or f"{opts.serial}/"
    command = (
        f"uv run python -m s3_glacier_restore download --dst {_uri(opts.dst_bucket, dst_prefix)} "
        f"--serial {opts.serial} "
        f"--profile {opts.profile} --dest ./restored-{opts.serial} "
        f"--max-list {opts.max_list}"
    )
    path = opts.handoff_dir / f"{opts.serial}-download.txt"
    path.write_text(
        "\n".join(
            [
                "RESTORE + COPY CONCLUIDOS",
                "",
                f"Alvo:    {opts.serial}",
                f"Origem:  {_uri(opts.src_bucket, src_prefix)}",
                f"Destino: {_uri(opts.dst_bucket, dst_prefix)} ({opts.storage_class})",
                f"Status:  {state.last_status or 'concluido'}",
                "",
                "Download manual (NAO executado automaticamente):",
                "",
                f"  cd {Path.cwd()}",
                f"  {command}",
                "",
                "LEMBRETE: bucket destino apaga objetos em 30 dias.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (opts.state_dir / f"{opts.serial}.done").write_text(
        f"OK {_now()} | {state.last_status}\n", encoding="utf-8"
    )
    state.handoff_file = str(path)
    return path


def run_auto(
    opts: AutoOptions,
    commands: AutoCommands | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Run the whole pipeline for one serial, resuming from its checkpoint."""
    commands = commands or AutoCommands()
    if not LABEL_RE.match(opts.serial or ""):
        raise ValueError(f"invalid label: {opts.serial}")
    for label, bucket, env_name in (
        ("origem", opts.src_bucket, "S3_RESTORE_SRC_BUCKET"),
        ("destino", opts.dst_bucket, "S3_RESTORE_DST_BUCKET"),
    ):
        if not bucket.strip():
            print(
                f"ERRO: bucket de {label} ausente: informe a flag ou {env_name}.",
                file=sys.stderr,
            )
            return EXIT_BLOCKED
    state_path = opts.state_dir / f"{opts.serial}-{opts.profile}.json"
    state = AutoState.load(state_path, opts.serial, opts.profile)
    if state.status == "done" and not opts.force:
        print(f"[{opts.serial}] ja concluido - nada a fazer.")
        return EXIT_OK
    if state.status == "failed" and not opts.force:
        print(f"[{opts.serial}] estado failed - rode com --force para retomar.")
        return EXIT_ERROR
    if opts.force:
        state = AutoState(serial=opts.serial, profile=opts.profile)
    state.status = "running"
    state.error = ""

    logger = AuditLogger(
        opts.log_dir,
        make_run_id(opts.serial),
        opts.serial,
        opts.profile,
    )
    logger.log(
        "INFO",
        "auto",
        "auto:run",
        f"serial:{opts.serial}",
        "ok",
        extra={"phase": state.phase, "resume": state.attempts},
    )

    phases = PHASES[PHASES.index(state.phase) :] if state.phase in PHASES else PHASES
    for phase in phases:
        state.phase = phase
        state.attempts[phase] = state.attempts.get(phase, 0) + 1
        state.save(state_path)
        logger.log(
            "INFO",
            "auto",
            "auto:phase:start",
            f"serial:{opts.serial}",
            "ok",
            extra={"phase": phase, "attempt": state.attempts[phase]},
        )

        if phase == "restore":
            code, run_id = _run_step(commands.restore, "restore", opts, opts.serial)
            state.run_ids["restore"] = run_id
            if code != EXIT_OK:
                state.status = "failed"
                state.error = f"restore exit={code}"
                state.save(state_path)
                return EXIT_ERROR

        elif phase == "wait":
            if opts.wait_seconds > 0:
                sleep_fn(opts.wait_seconds)
            counts = StatusCounts()
            for attempt in range(1, max(1, opts.max_polls) + 1):
                counts = commands.status_fn(opts, None)
                state.last_status = counts.summary()
                state.save(state_path)
                print(f"[{opts.serial}] status {attempt}/{opts.max_polls}: {counts.summary()}")
                if counts.complete:
                    break
                if attempt < opts.max_polls:
                    sleep_fn(opts.poll_seconds)
            if not counts.complete and not opts.force_copy:
                state.status = "failed"
                state.error = f"restore incompleto: {counts.summary()}"
                state.save(state_path)
                logger.log(
                    "ERROR",
                    "auto",
                    "auto:phase:block",
                    f"serial:{opts.serial}",
                    "blocked",
                    extra={"phase": "wait", "status": counts.summary()},
                )
                return EXIT_ERROR
            state.last_status = counts.summary() + (" [FORCE]" if opts.force_copy else "")

        elif phase == "copy":
            code, run_id = _run_step(commands.copy, "copy", opts, opts.serial)
            state.run_ids["copy"] = run_id
            if code != EXIT_OK:
                state.status = "failed"
                state.error = f"copy exit={code}"
                state.save(state_path)
                return EXIT_ERROR

        elif phase == "verify":
            code, run_id = _run_step(commands.verify, "verify", opts, opts.serial)
            state.run_ids["verify"] = run_id
            if code != EXIT_OK:
                state.status = "failed"
                state.error = f"verify exit={code} (destino nao liberado)"
                state.save(state_path)
                return EXIT_ERROR

        elif phase == "handoff":
            write_handoff(opts, state)

        state.save(state_path)
        logger.log(
            "INFO", "auto", "auto:phase:done", f"serial:{opts.serial}", "ok", extra={"phase": phase}
        )

    state.status = "done"
    state.phase = "done"
    state.save(state_path)
    logger.log(
        "INFO",
        "auto",
        "auto:run",
        f"serial:{opts.serial}",
        "ok",
        extra={"result": "done", "handoff": state.handoff_file},
    )
    print(f"[{opts.serial}] CONCLUIDO - download em: {state.handoff_file}")
    return EXIT_OK


def _resolve_target(item: str, base: AutoOptions) -> AutoOptions:
    """Resolve one batch item: plain label or ``SRC=DST`` URI pair.

    Plain labels scope to ``<base prefix><label>/`` under the base buckets
    (legacy: ``<label>/``). URI pairs carry their own buckets, prefixes and
    inferred label.
    """
    if "=" in item:
        src_raw, _, dst_raw = item.partition("=")
        src = parse_s3_location(src_raw)
        dst = parse_s3_location(dst_raw)
        return replace(
            base,
            serial=infer_label("", src.prefix, dst.prefix),
            src_bucket=src.bucket,
            src_prefix=src.prefix,
            dst_bucket=dst.bucket,
            dst_prefix=dst.prefix,
        )
    label = infer_label(item)
    return replace(
        base,
        serial=label,
        src_prefix=f"{base.src_prefix}{label}/",
        dst_prefix=f"{base.dst_prefix}{label}/",
    )


def _options_from_args(args: Namespace, serial: str | None = None) -> AutoOptions:
    src_raw = (getattr(args, "src", "") or "").strip()
    dst_raw = (getattr(args, "dst", "") or "").strip()
    if src_raw or dst_raw:
        if not src_raw or not dst_raw:
            raise ValueError("informe --src e --dst com URIs S3")
        src = parse_s3_location(src_raw)
        dst = parse_s3_location(dst_raw)
        label = infer_label((serial or args.serial or ""), src.prefix, dst.prefix)
        return AutoOptions(
            serial=label,
            profile=args.profile,
            region=args.region,
            src_bucket=src.bucket,
            src_prefix=src.prefix,
            dst_bucket=dst.bucket,
            dst_prefix=dst.prefix,
            storage_class=args.storage_class,
            tier=args.tier,
            days=args.days,
            max_list=args.max_list,
            workers=args.workers,
            wait_seconds=args.wait_seconds,
            poll_seconds=args.poll_seconds,
            max_polls=args.max_polls,
            state_dir=Path(args.state_dir),
            handoff_dir=Path(args.handoff_dir),
            log_dir=Path(args.log_dir),
            report_dir=Path(args.report_dir),
            force=args.force,
            force_copy=args.force_copy,
            dry_run=args.dry_run,
        )
    return AutoOptions(
        serial=serial or args.serial,
        profile=args.profile,
        region=args.region,
        src_bucket=args.src_bucket,
        dst_bucket=args.dst_bucket,
        storage_class=args.storage_class,
        tier=args.tier,
        days=args.days,
        max_list=args.max_list,
        workers=args.workers,
        wait_seconds=args.wait_seconds,
        poll_seconds=args.poll_seconds,
        max_polls=args.max_polls,
        state_dir=Path(args.state_dir),
        handoff_dir=Path(args.handoff_dir),
        log_dir=Path(args.log_dir),
        report_dir=Path(args.report_dir),
        force=args.force,
        force_copy=args.force_copy,
        dry_run=args.dry_run,
    )


def cmd_auto(args: Namespace) -> int:
    return run_auto(_options_from_args(args))


def cmd_batch(args: Namespace) -> int:
    base = _options_from_args(args)
    targets = [_resolve_target(item, base) for item in list(args.serials)]
    return run_batch(targets, base, max_parallel=args.max_parallel)


def run_batch(
    serials: list[str] | list[AutoOptions],
    base_opts: AutoOptions,
    max_parallel: int = 2,
    runner: Callable[[AutoOptions], int] | None = None,
) -> int:
    """Run independent targets concurrently, each with its own checkpoint.

    ``serials`` accepts plain labels or ``SRC=DST`` URI pairs (resolved
    against ``base_opts``). Pre-resolved ``AutoOptions`` lists pass through.
    """
    if serials and isinstance(serials[0], AutoOptions):
        targets = list(serials)
    else:
        targets = [_resolve_target(str(item), base_opts) for item in serials]
    runner = runner or run_auto
    results: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_parallel), thread_name_prefix="batch") as ex:
        futures = {ex.submit(runner, target): target.serial for target in targets}
        for fut in as_completed(futures):
            serial = futures[fut]
            results[serial] = fut.result()
            print(f"[batch] {serial}: exit={results[serial]}")
    return EXIT_ERROR if any(code != EXIT_OK for code in results.values()) else EXIT_OK
