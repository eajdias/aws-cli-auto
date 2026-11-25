"""CLI: perms | plan | restore | status | copy | verify | download."""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from uuid import uuid4

from .audit import AuditLogger
from .aws_factory import build_session, s3_client, split_aws_error, sts_client
from .config import DEFAULT_REGION, DEFAULT_STORAGE_CLASS, RestoreConfig
from .locations import infer_label, parse_s3_location
from .parity import compare_listings, list_scope
from .report import write_report
from .s3ops import (
    S3ObjectRef,
    collect_status,
    dst_key_for,
    head_restore_state,
    is_archived,
    is_downloadable,
    run_census,
    timed,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2


def _common(
    sp: argparse.ArgumentParser, serial_required: bool = True, max_list_default: int = 20000
) -> None:
    _ = serial_required  # legacy compat: a resolucao (resolve_locations) valida
    sp.add_argument(
        "--serial",
        default=None,
        help="Etiqueta do run (ex-serial). Inferida do prefixo quando --src/--dst sao usados.",
    )
    sp.add_argument(
        "--label",
        dest="serial",
        default=None,
        help="Alias de --serial.",
    )
    sp.add_argument(
        "--src",
        default="",
        help="Origem: s3://bucket/prefixo/ (ou bucket/prefixo). Exige --dst.",
    )
    sp.add_argument(
        "--dst",
        default="",
        help="Destino: s3://bucket/prefixo/ (ou bucket/prefixo). Exige --src.",
    )
    sp.add_argument("--profile", default="default", help="Perfil AWS (~/.aws/credentials)")
    sp.add_argument("--region", default=DEFAULT_REGION)
    sp.add_argument(
        "--src-bucket",
        default=os.environ.get("S3_RESTORE_SRC_BUCKET", ""),
        help="Bucket de origem (ou S3_RESTORE_SRC_BUCKET)",
    )
    sp.add_argument(
        "--dst-bucket",
        default=os.environ.get("S3_RESTORE_DST_BUCKET", ""),
        help="Bucket de destino (ou S3_RESTORE_DST_BUCKET)",
    )
    sp.add_argument("--max-list", type=int, default=max_list_default, help="Objetos no censo")
    sp.add_argument("--log-dir", default="logs")
    sp.add_argument("--report-dir", default="reports")


def _auto_common(sp: argparse.ArgumentParser, serial_required: bool = True) -> None:
    """Options shared by `auto` and `batch`."""
    _common(sp, serial_required=serial_required, max_list_default=700000)
    sp.add_argument("--workers", type=int, default=20)
    sp.add_argument("--tier", default="Standard", choices=["Standard", "Bulk"])
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument(
        "--storage-class",
        default=DEFAULT_STORAGE_CLASS,
        choices=["STANDARD", "STANDARD_IA", "INTELLIGENT_TIERING", "GLACIER_IR"],
    )
    sp.add_argument(
        "--wait-seconds",
        type=int,
        default=14400,
        help="Grace before the first status check (default 4h)",
    )
    sp.add_argument(
        "--poll-seconds", type=int, default=3600, help="Interval between status checks (default 1h)"
    )
    sp.add_argument("--max-polls", type=int, default=24)
    sp.add_argument("--state-dir", default="state")
    sp.add_argument("--handoff-dir", default="handoff")
    sp.add_argument("--force", action="store_true", help="Restart even if done/failed")
    sp.add_argument(
        "--force-copy", action="store_true", help="Copy even if status never confirmed all restores"
    )
    sp.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="s3-glacier-restore", description="S3 Glacier restore pipeline"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("perms", help="Matriz de permissoes S3 (leitura + probe de escrita)")
    _common(c)

    c = sub.add_parser("plan", help="Preflight: censo e bloqueios (somente leitura)")
    _common(c)

    c = sub.add_parser("restore", help="Solicita restore dos arquivados")
    _common(c)
    c.add_argument("--tier", default="Standard", choices=["Standard", "Bulk"])
    c.add_argument("--days", type=int, default=7)
    c.add_argument("--workers", type=int, default=8, help="Chamadas S3 em paralelo (default 8)")
    c.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("status", help="Progresso dos restores (somente leitura)")
    _common(c)
    c.add_argument("--workers", type=int, default=8, help="Heads S3 em paralelo (default 8)")

    c = sub.add_parser("copy", help="Copia legiveis para o destino em STANDARD")
    _common(c)
    c.add_argument(
        "--storage-class",
        default="STANDARD",
        choices=["STANDARD", "STANDARD_IA", "INTELLIGENT_TIERING", "GLACIER_IR"],
    )
    c.add_argument("--workers", type=int, default=8, help="Copias S3 em paralelo (default 8)")
    c.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("verify", help="Gate do download: recusa se houver arquivado no destino")
    _common(c)

    c = sub.add_parser(
        "parity", help="Compara origem x destino: ausentes, extras e divergentes (leitura)"
    )
    _common(c)

    c = sub.add_parser(
        "download",
        help="Baixa do bucket publico para disco local (tudo ou chaves especificas)",
    )
    _common(c)
    c.add_argument("--dest", default="", help="Diretorio local (default ./restored-<SERIAL>)")
    c.add_argument(
        "--key",
        action="append",
        default=[],
        help="Chave especifica no bucket destino; repita para varias. Default: tudo sob o prefixo",
    )
    c.add_argument(
        "--prefix",
        action="append",
        default=[],
        help="Prefixo relativo ao serial para baixar; repita para varios (ex: Images/)",
    )
    c.add_argument(
        "--skip-head",
        action="store_true",
        help="Pula HEAD por objeto (use so apos o HEAD amostral ter passado)",
    )
    c.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("auto", help="Orquestra restore -> status -> copy -> verify (retomavel)")
    _auto_common(c)

    c = sub.add_parser("batch", help="Varios alvos em paralelo, cada um com checkpoint proprio")
    _auto_common(c, serial_required=False)
    c.add_argument(
        "serials",
        nargs="+",
        help="Etiquetas (ex: 1234-5678-90) ou pares SRC=DST (ex: s3://bkt-a/p1=s3://bkt-b/p1)",
    )
    c.add_argument("--max-parallel", type=int, default=2)
    return p


def resolve_locations(
    args: argparse.Namespace,
) -> tuple[str, str, str, str, str]:
    """Resolve (label, src_bucket, src_prefix, dst_bucket, dst_prefix) from CLI args.

    URI mode (``--src s3://bucket/prefixo/ --dst ...``) wins and infers the
    label from the last prefix segment; legacy mode derives ``<serial>/``
    prefixes from ``--serial``. Raises ``ValueError`` on incomplete input.
    """
    src_raw = (getattr(args, "src", "") or "").strip()
    dst_raw = (getattr(args, "dst", "") or "").strip()
    serial_raw = (getattr(args, "serial", "") or "").strip()
    if getattr(args, "cmd", "") == "download" and dst_raw and not src_raw:
        src_raw = dst_raw
    if src_raw or dst_raw:
        if not src_raw or not dst_raw:
            raise ValueError(
                "informe --src e --dst (ex: --src s3://bucket/origem/ --dst s3://bucket/destino/)"
            )
        src = parse_s3_location(src_raw)
        dst = parse_s3_location(dst_raw)
        label = infer_label(serial_raw, src.prefix, dst.prefix)
        return label, src.bucket, src.prefix, dst.bucket, dst.prefix
    if not serial_raw:
        raise ValueError("informe --serial (etiqueta) ou --src/--dst com URIs S3")
    label = infer_label(serial_raw)
    src_bucket = (getattr(args, "src_bucket", "") or "").strip()
    dst_bucket = (getattr(args, "dst_bucket", "") or "").strip()
    return label, src_bucket, "", dst_bucket, ""


def _setup(args: argparse.Namespace) -> tuple[RestoreConfig, AuditLogger, object]:
    try:
        label, src_bucket, src_prefix, dst_bucket, dst_prefix = resolve_locations(args)
        cfg = RestoreConfig(
            serial=label,
            profile=args.profile,
            region=args.region,
            src_bucket=src_bucket,
            src_prefix=src_prefix,
            dst_bucket=dst_bucket,
            dst_prefix=dst_prefix,
            dest_storage_class=getattr(args, "storage_class", "STANDARD"),
            restore_days=getattr(args, "days", 7),
            restore_tier=getattr(args, "tier", "Standard"),
            max_list=args.max_list,
            dry_run=bool(getattr(args, "dry_run", False)),
            log_dir=Path(args.log_dir),
            report_dir=Path(args.report_dir),
            run_id=getattr(args, "run_id", ""),
        )
    except ValueError as exc:
        print(f"ERRO: configuracao invalida: {exc}", file=sys.stderr)
        sys.exit(EXIT_BLOCKED)
    logger = AuditLogger(logger_log_dir(cfg), cfg.run_id, cfg.serial, cfg.profile)
    session = build_session(cfg.profile, cfg.region)
    try:
        sts = sts_client(session, cfg.region)
        ident, _, _ = timed(sts.get_caller_identity)
        account = str(ident.get("Account", "")) if ident else ""
        actor = str(ident.get("Arn", "")) if ident else ""
    except Exception as exc:
        code, msg = split_aws_error(exc)
        logger.log(
            "ERROR", "run", "sts:GetCallerIdentity", f"profile:{cfg.profile}", "fail", code, msg
        )
        print(f"ERRO: identidade AWS indisponivel ({code}). Confira --profile.", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    logger.set_identity(account, actor)
    logger.log(
        "INFO",
        "run",
        "sts:GetCallerIdentity",
        actor or cfg.profile,
        "ok",
        extra={"account": account},
    )
    print(f"Ator: {actor}")
    s3 = s3_client(session, cfg.region)
    return cfg, logger, s3


def logger_log_dir(cfg: RestoreConfig) -> Path:
    return cfg.log_dir


def cmd_perms(args: argparse.Namespace) -> int:
    cfg, logger, s3 = _setup(args)
    rows: list[tuple[str, str, str]] = []  # (action, resource, result)

    def check(step_action: str, resource: str, fn: object, *a: object, **k: object) -> bool:
        _result, duration, err = timed(fn, *a, **k)  # type: ignore[arg-type]
        if err is None:
            logger.log("INFO", "perms", step_action, resource, "ok", duration_ms=duration)
            rows.append((step_action, resource, "PASS"))
            print(f"  [PASS] {step_action:16} {resource}")
            return True
        code, msg = err
        logger.log("ERROR", "perms", step_action, resource, "fail", code, msg, duration)
        rows.append((step_action, resource, "FAIL"))
        print(f"  [FAIL] {step_action:16} {resource}\n         {code}: {msg[:220]}")
        return False

    print(f"Origem : s3://{cfg.src_bucket}/{cfg.src_prefix}")
    print(f"Destino: s3://{cfg.dst_bucket}/{cfg.dst_prefix}")
    ok = True
    ok &= check(
        "s3:ListBucket",
        f"arn:aws:s3:::{cfg.src_bucket}",
        s3.list_objects_v2,
        Bucket=cfg.src_bucket,
        Prefix=cfg.src_prefix,
        MaxKeys=1,
    )
    ok &= check(
        "s3:ListBucket",
        f"arn:aws:s3:::{cfg.dst_bucket}",
        s3.list_objects_v2,
        Bucket=cfg.dst_bucket,
        Prefix=cfg.dst_prefix,
        MaxKeys=1,
    )

    # Sample key from source for GetObject / RestoreObject probes.
    sample_key = ""
    try:
        page = s3.list_objects_v2(Bucket=cfg.src_bucket, Prefix=cfg.src_prefix, MaxKeys=5)
        for obj in page.get("Contents", []) or []:
            if obj and obj.get("Key") and not str(obj["Key"]).endswith("/"):
                sample_key = str(obj["Key"])
                break
    except Exception as exc:
        code, msg = split_aws_error(exc)
        logger.log("ERROR", "perms", "s3:ListObjectsV2", cfg.src_uri(), "fail", code, msg)
    if sample_key:
        print(f"Amostra: {sample_key}")
        ok &= check(
            "s3:GetObject",
            f"arn:aws:s3:::{cfg.src_bucket}/{sample_key}",
            s3.head_object,
            Bucket=cfg.src_bucket,
            Key=sample_key,
        )
        # RestoreObject probe on a readable object: InvalidObjectState means
        # the permission exists (object simply is not archived).
        _, duration, err = timed(
            s3.restore_object,
            Bucket=cfg.src_bucket,
            Key=sample_key,
            RestoreRequest={
                "Days": cfg.restore_days,
                "GlacierJobParameters": {"Tier": cfg.restore_tier},
            },
        )
        if err is None:
            logger.log(
                "INFO",
                "perms",
                "s3:RestoreObject",
                f"arn:aws:s3:::{cfg.src_bucket}/{sample_key}",
                "ok",
                duration_ms=duration,
            )
            rows.append(("s3:RestoreObject", sample_key, "PASS"))
            print("  [PASS] s3:RestoreObject  (restore solicitado na amostra)")
        else:
            code, msg = err
            if code == "InvalidObjectState":
                logger.log(
                    "INFO",
                    "perms",
                    "s3:RestoreObject",
                    f"arn:aws:s3:::{cfg.src_bucket}/{sample_key}",
                    "ok",
                    duration_ms=duration,
                    extra={"note": "permission exists; object not archived"},
                )
                rows.append(("s3:RestoreObject", sample_key, "PASS"))
                print("  [PASS] s3:RestoreObject  (permissao existe; objeto nao arquivado)")
            else:
                logger.log(
                    "ERROR",
                    "perms",
                    "s3:RestoreObject",
                    f"arn:aws:s3:::{cfg.src_bucket}/{sample_key}",
                    "fail",
                    code,
                    msg,
                    duration,
                )
                rows.append(("s3:RestoreObject", sample_key, "FAIL"))
                print(f"  [FAIL] s3:RestoreObject\n         {code}: {msg[:220]}")
                ok = False
    else:
        print("  (sem chave de amostra - ListBucket da origem falhou?)")

    # PutObject probe + cleanup.
    probe_key = f"{cfg.dst_prefix}.probe-{uuid4().hex[:8]}.txt"
    _, duration, err = timed(
        s3.put_object,
        Bucket=cfg.dst_bucket,
        Key=probe_key,
        Body=b"permission probe",
        ContentType="text/plain",
    )
    if err is None:
        logger.log(
            "INFO",
            "perms",
            "s3:PutObject",
            f"arn:aws:s3:::{cfg.dst_bucket}/{probe_key}",
            "ok",
            duration_ms=duration,
        )
        rows.append(("s3:PutObject", probe_key, "PASS"))
        print("  [PASS] s3:PutObject")
        _, duration2, err2 = timed(s3.delete_object, Bucket=cfg.dst_bucket, Key=probe_key)
        if err2 is None:
            logger.log(
                "INFO",
                "perms",
                "s3:DeleteObject",
                f"arn:aws:s3:::{cfg.dst_bucket}/{probe_key}",
                "ok",
                duration_ms=duration2,
            )
            print("  [PASS] s3:DeleteObject (probe removido)")
        else:
            code2, msg2 = err2
            logger.log(
                "ERROR",
                "perms",
                "s3:DeleteObject",
                f"arn:aws:s3:::{cfg.dst_bucket}/{probe_key}",
                "fail",
                code2,
                msg2,
                duration2,
            )
            print(f"  [FAIL] s3:DeleteObject\n         {code2}: {msg2[:220]}")
            ok = False
    else:
        code, msg = err
        logger.log(
            "ERROR",
            "perms",
            "s3:PutObject",
            f"arn:aws:s3:::{cfg.dst_bucket}/{probe_key}",
            "fail",
            code,
            msg,
            duration,
        )
        rows.append(("s3:PutObject", probe_key, "FAIL"))
        print(f"  [FAIL] s3:PutObject\n         {code}: {msg[:220]}")
        ok = False

    body = "\n".join(f"- [{r}] `{a}` `{res}`" for a, res, r in rows)
    path = write_report(
        cfg.report_dir,
        cfg,
        logger,
        f"Permissoes - {cfg.serial}",
        [("Matriz (API real)", body)],
        verdict="OK - credencial completa para o fluxo."
        if ok
        else "BLOQUEADO - faltam permissoes (ver erros acima).",
    )
    print(f"\nRelatorio: {path}\nTrilha: logs/{cfg.run_id}.jsonl")
    return EXIT_OK if ok else EXIT_ERROR


def cmd_plan(args: argparse.Namespace) -> int:
    cfg, logger, s3 = _setup(args)
    print(f"Origem: s3://{cfg.src_bucket}/{cfg.src_prefix} (amostra ate {cfg.max_list})")
    census = run_census(s3, cfg.src_bucket, cfg.src_prefix, cfg.max_list)
    logger.log(
        "INFO" if census.ok else "ERROR",
        "plan",
        "s3:ListObjectsV2",
        cfg.src_uri(),
        "ok" if census.ok else "fail",
        census.error_code,
        census.error_msg,
        extra={"scanned": census.scanned, "by_class": census.by_class},
    )
    for cls in sorted(census.by_class):
        print(f"  {cls:14} {census.by_class[cls]:8}")
    if census.truncated:
        print(
            f"\n!! Censo cortado em --max-list {cfg.max_list} - numeros parciais; "
            "rode com --max-list maior."
        )
    print(f"\nArquivados (precisam restore): {len(census.archived)}")
    if not census.ok:
        path = write_report(
            cfg.report_dir,
            cfg,
            logger,
            f"Plan - {cfg.serial}",
            [("Censo", "FALHOU - ver trilha.")],
            [(f"Origem ({census.scanned})", census)],
            verdict="BLOQUEADO - censo falhou.",
        )
        print(f"\nRelatorio: {path}")
        return EXIT_BLOCKED
    if census.scanned == 0:
        write_report(
            cfg.report_dir,
            cfg,
            logger,
            f"Plan - {cfg.serial}",
            [("Censo", "Nenhum objeto no prefixo. Confira o serial.")],
            [("Origem (0)", census)],
            verdict="BLOQUEADO - prefixo vazio.",
        )
        return EXIT_BLOCKED
    if census.archived:
        path = write_report(
            cfg.report_dir,
            cfg,
            logger,
            f"Plan - {cfg.serial}",
            [
                (
                    "Bloqueio",
                    f"{len(census.archived)} objeto(s) em Glacier/Deep Archive. "
                    "Uma copia direta FALHARA com 'Storage class does not support "
                    "synchronous retrieval'. Rode `restore` e aguarde antes de `copy`.",
                )
            ],
            [(f"Origem ({census.scanned})", census)],
            verdict="BLOQUEADO - restore necessario.",
        )
        print("\n!! BLOQUEADO: rode `restore` antes de `copy`.")
        print(f"Relatorio: {path}")
        return EXIT_BLOCKED
    if census.truncated:
        verdict = (
            f"INCONCLUSIVO - censo cortado em --max-list {cfg.max_list}; "
            "rode de novo com --max-list maior antes de `copy`."
        )
        path = write_report(
            cfg.report_dir,
            cfg,
            logger,
            f"Plan - {cfg.serial}",
            [("Censo", verdict)],
            [(f"Origem ({census.scanned})", census)],
            verdict=verdict,
        )
        print("\n!! " + verdict)
        print(f"Relatorio: {path}")
        return EXIT_BLOCKED
    path = write_report(
        cfg.report_dir,
        cfg,
        logger,
        f"Plan - {cfg.serial}",
        [("Censo", "Nada arquivado - seguro rodar `copy`.")],
        [(f"Origem ({census.scanned})", census)],
        verdict="OK - pode copiar.",
    )
    print("\nOK - pode copiar.")
    print(f"Relatorio: {path}")
    return EXIT_OK


def _restore_one(
    s3: object, cfg: RestoreConfig, obj: object
) -> tuple[object, str, str | None, str | None, int]:
    """Restore one object. Returns (obj, outcome, code, msg, duration_ms)."""
    _, duration, err = timed(
        s3.restore_object,  # type: ignore[attr-defined]
        Bucket=cfg.src_bucket,
        Key=obj.key,  # type: ignore[attr-defined]
        RestoreRequest={
            "Days": cfg.restore_days,
            "GlacierJobParameters": {"Tier": cfg.restore_tier},
        },
    )
    if err is None:
        return (obj, "ok", None, None, duration)
    code, msg = err
    if code in (
        "RestoreAlreadyInProgress",
        "ObjectAlreadyInActiveTierError",
        "RestoreAlreadyInProgressError",
    ):
        return (obj, "skip", code, msg, duration)
    return (obj, "fail", code, msg, duration)


def _head_one(
    s3: object, cfg: RestoreConfig, obj: object
) -> tuple[object, str, str | None, str | None]:
    """Head one object. Returns (obj, state, code, msg)."""
    state, _, err = head_restore_state(s3, cfg.src_bucket, obj.key)  # type: ignore[arg-type,attr-defined]
    if err is not None:
        code, msg = err
        return (obj, "error", code, msg)
    return (obj, state, None, None)


def _record_restore(
    logger: AuditLogger,
    cfg: RestoreConfig,
    obj: object,
    outcome: str,
    code: str | None,
    msg: str | None,
    duration: int,
    ok: int,
    skip: int,
    fail: int,
) -> tuple[int, int, int]:
    res = f"s3://{cfg.src_bucket}/{obj.key}"  # type: ignore[attr-defined]
    if outcome == "ok":
        logger.log(
            "INFO",
            "restore",
            "s3:RestoreObject",
            res,
            "ok",
            duration_ms=duration,
            extra={"storage_class": obj.storage_class},  # type: ignore[attr-defined]
        )
        return (ok + 1, skip, fail)
    if outcome == "skip":
        logger.log("INFO", "restore", "s3:RestoreObject", res, "skip", code, msg, duration)
        return (ok, skip + 1, fail)
    logger.log("ERROR", "restore", "s3:RestoreObject", res, "fail", code, msg, duration)
    return (ok, skip, fail + 1)


def cmd_restore(args: argparse.Namespace) -> int:
    cfg, logger, s3 = _setup(args)
    census = run_census(s3, cfg.src_bucket, cfg.src_prefix, cfg.max_list)
    if not census.ok:
        print("ERRO: censo falhou - ver trilha.")
        return EXIT_ERROR
    if not census.archived:
        print("Nada arquivado - nada a restaurar.")
        return EXIT_OK
    workers = max(1, int(getattr(args, "workers", 1) or 1))
    print(
        f"Arquivados: {len(census.archived)} (tier={cfg.restore_tier}, days={cfg.restore_days}, "
        f"workers={workers})" + (" [DRY-RUN]" if cfg.dry_run else "")
    )
    ok = skip = fail = 0
    if cfg.dry_run:
        for obj in census.archived:
            res = f"s3://{cfg.src_bucket}/{obj.key}"
            logger.log(
                "INFO",
                "restore",
                "s3:RestoreObject",
                res,
                "dry_run",
                extra={"storage_class": obj.storage_class},
            )
        ok = len(census.archived)
    elif workers == 1:
        for i, obj in enumerate(census.archived, 1):
            _, outcome, code, msg, duration = _restore_one(s3, cfg, obj)
            ok, skip, fail = _record_restore(
                logger, cfg, obj, outcome, code, msg, duration, ok, skip, fail
            )
            if i % 50 == 0:
                print(f"  ... {i}/{len(census.archived)} (ok={ok} skip={skip} fail={fail})")
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="restore") as ex:
            futs = [ex.submit(_restore_one, s3, cfg, o) for o in census.archived]
            for done_n, fut in enumerate(as_completed(futs), 1):
                obj, outcome, code, msg, duration = fut.result()
                ok, skip, fail = _record_restore(
                    logger, cfg, obj, outcome, code, msg, duration, ok, skip, fail
                )
                if done_n % 50 == 0:
                    print(
                        f"  ... {done_n}/{len(census.archived)} (ok={ok} skip={skip} fail={fail})"
                    )
    print(f"\nRESTORE: solicitados={ok} ja_em_andamento={skip} falhas={fail}")
    if cfg.dry_run:
        verdict = f"DRY-RUN - nada foi solicitado; {ok} seriam restaurados. Rode sem --dry-run."
    elif fail:
        verdict = "Falhas - ver trilha."
    else:
        verdict = "Aguarde ~12h (Standard) / ~48h (Bulk) e rode `status`."
    path = write_report(
        cfg.report_dir,
        cfg,
        logger,
        f"Restore - {cfg.serial}",
        [
            (
                "Resultado",
                f"solicitados={ok} ja_em_andamento={skip} falhas={fail} de {len(census.archived)}.",
            )
        ],
        [(f"Origem ({census.scanned})", census)],
        verdict=verdict,
    )
    print(f"Relatorio: {path}\nTrilha: logs/{cfg.run_id}.jsonl")
    return EXIT_ERROR if fail else EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    cfg, logger, s3 = _setup(args)
    census = run_census(s3, cfg.src_bucket, cfg.src_prefix, cfg.max_list)
    if not census.ok:
        print("ERRO: censo falhou - ver trilha.")
        return EXIT_ERROR
    workers = max(1, int(getattr(args, "workers", 1) or 1))
    print(f"Verificando {len(census.archived)} objeto(s) (workers={workers})...")
    progress = 0

    def record(obj: S3ObjectRef, state: str, code: str | None, msg: str | None) -> None:
        nonlocal progress
        res = f"s3://{cfg.src_bucket}/{obj.key}"
        if state == "error":
            logger.log("ERROR", "status", "s3:HeadObject", res, "fail", code, msg)
        else:
            logger.log("INFO", "status", "s3:HeadObject", res, "ok", extra={"restore_state": state})
        progress += 1
        if progress % 200 == 0:
            print(f"  ... {progress}/{len(census.archived)}")

    counts = collect_status(s3, cfg.src_bucket, census.archived, workers, on_result=record)
    print(f"\nSTATUS: {counts.summary()}")
    verdict = (
        "Tudo restaurado - pode rodar `copy`."
        if counts.complete
        else "Aguarde e rode `status` de novo."
    )
    path = write_report(
        cfg.report_dir,
        cfg,
        logger,
        f"Status - {cfg.serial}",
        [("Restore", verdict)],
        [(f"Origem ({census.scanned})", census)],
        verdict=verdict,
    )
    print(f"Relatorio: {path}")
    return EXIT_OK


def _copy_one(
    s3: object, cfg: RestoreConfig, key: str, storage_class: str
) -> tuple[str, str | None, str | None, int, str]:
    """Copy one key. Returns (outcome, code, msg, duration_ms, reason).

    A restored Deep Archive object keeps its storage class; only the Restore
    header says whether GET/CopyObject is allowed. HEAD before copying.
    """
    if is_archived(storage_class):
        state, _, err = head_restore_state(s3, cfg.src_bucket, key)  # type: ignore[arg-type]
        if err is not None or not is_downloadable(storage_class, state):
            code, msg = err if err is not None else (None, f"restore_state={state}")
            return ("skip", code, msg, 0, "restore not complete")
    dst_key = dst_key_for(key, cfg.src_prefix, cfg.dst_prefix)
    _, duration, err = timed(
        s3.copy_object,  # type: ignore[attr-defined]
        Bucket=cfg.dst_bucket,
        Key=dst_key,
        CopySource={"Bucket": cfg.src_bucket, "Key": key},
        StorageClass=cfg.dest_storage_class,
        MetadataDirective="COPY",
        TaggingDirective="COPY",
    )
    if err is None:
        return ("ok", None, None, duration, "copied")
    code, msg = err
    return ("fail", code, msg, duration, "copy failed")


def _record_copy(
    logger: AuditLogger,
    cfg: RestoreConfig,
    key: str,
    outcome: str,
    code: str | None,
    msg: str | None,
    duration: int,
    reason: str,
    ok: int,
    fail: int,
    skip: int,
) -> tuple[int, int, int]:
    res = f"s3://{cfg.src_bucket}/{key}"
    if outcome == "ok":
        dst_key = dst_key_for(key, cfg.src_prefix, cfg.dst_prefix)
        logger.log(
            "INFO",
            "copy",
            "s3:CopyObject",
            res,
            "ok",
            duration_ms=duration,
            extra={
                "dst": f"s3://{cfg.dst_bucket}/{dst_key}",
                "storage_class": cfg.dest_storage_class,
            },
        )
        return (ok + 1, fail, skip)
    if outcome == "skip":
        logger.log(
            "INFO",
            "copy",
            "s3:CopyObject",
            res,
            "skip",
            code,
            msg,
            duration,
            extra={"reason": reason},
        )
        return (ok, fail, skip + 1)
    logger.log("ERROR", "copy", "s3:CopyObject", res, "fail", code, msg, duration)
    return (ok, fail + 1, skip)


def cmd_copy(args: argparse.Namespace) -> int:
    cfg, logger, s3 = _setup(args)
    census = run_census(s3, cfg.src_bucket, cfg.src_prefix, cfg.max_list)
    if not census.ok:
        print("ERRO: censo falhou - ver trilha.")
        return EXIT_ERROR
    workers = max(1, int(getattr(args, "workers", 1) or 1))
    print(
        f"Origem -> destino ({cfg.dest_storage_class}, workers={workers})"
        + (" [DRY-RUN]" if cfg.dry_run else "")
    )
    ok = skip = fail = 0
    # Re-list with keys to copy (census only kept archived keys; list again).
    items: list[tuple[str, str]] = []
    token: str | None = None
    while True:
        params: dict[str, object] = {
            "Bucket": cfg.src_bucket,
            "Prefix": cfg.src_prefix,
            "MaxKeys": 1000,
        }
        if token:
            params["ContinuationToken"] = token
        page = s3.list_objects_v2(**params)  # type: ignore[arg-type]
        for obj in page.get("Contents", []) or []:
            if obj is None:
                continue
            key = str(obj.get("Key", ""))
            cls = str(obj.get("StorageClass") or "FOLDER_MARKER")
            if key.endswith("/") or cls == "FOLDER_MARKER":
                logger.log(
                    "INFO",
                    "copy",
                    "s3:CopyObject",
                    f"s3://{cfg.src_bucket}/{key}",
                    "skip",
                    extra={"reason": "folder marker"},
                )
                skip += 1
                continue
            items.append((key, cls))
            if len(items) >= cfg.max_list:
                break
        if len(items) >= cfg.max_list:
            break
        token = page.get("NextContinuationToken")
        if not token:
            break

    def record(
        key: str, outcome: str, code: str | None, msg: str | None, duration: int, reason: str
    ) -> None:
        nonlocal ok, skip, fail
        ok, fail, skip = _record_copy(
            logger, cfg, key, outcome, code, msg, duration, reason, ok, fail, skip
        )

    if cfg.dry_run:
        for key, cls in items:
            logger.log(
                "INFO",
                "copy",
                "s3:CopyObject",
                f"s3://{cfg.src_bucket}/{key}",
                "dry_run",
                extra={"storage_class": cfg.dest_storage_class, "src_class": cls},
            )
        ok = len(items)
    elif workers == 1:
        for done_n, (key, cls) in enumerate(items, 1):
            outcome, code, msg, duration, reason = _copy_one(s3, cfg, key, cls)
            record(key, outcome, code, msg, duration, reason)
            if done_n % 50 == 0:
                print(f"  ... copiados={ok} pulados={skip} falhas={fail}")
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="copy") as ex:
            fut_to_key = {ex.submit(_copy_one, s3, cfg, key, cls): key for key, cls in items}
            for done_n, fut in enumerate(as_completed(fut_to_key), 1):
                outcome, code, msg, duration, reason = fut.result()
                record(fut_to_key[fut], outcome, code, msg, duration, reason)
                if done_n % 50 == 0:
                    print(f"  ... copiados={ok} pulados={skip} falhas={fail}")
    print(f"\nCOPY: copiados={ok} pulados={skip} falhas={fail}")
    path = write_report(
        cfg.report_dir,
        cfg,
        logger,
        f"Copy - {cfg.serial}",
        [
            (
                "Resultado",
                f"copiados={ok} pulados={skip} falhas={fail}. "
                "Pulados = arquivados sem restore concluido ou "
                "marcadores de pasta.",
            )
        ],
        verdict="Falhas - ver trilha." if fail else "OK.",
    )
    print(f"Relatorio: {path}\nTrilha: logs/{cfg.run_id}.jsonl")
    return EXIT_ERROR if fail else EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    cfg, logger, s3 = _setup(args)
    dst = run_census(s3, cfg.dst_bucket, cfg.dst_prefix, cfg.max_list)
    if not dst.ok:
        print("ERRO: censo do destino falhou - ver trilha.")
        return EXIT_ERROR
    if dst.truncated:
        verdict = (
            f"INCONCLUSIVO - censo cortado em --max-list {cfg.max_list}; "
            "rode de novo com --max-list maior."
        )
        print("\n!! " + verdict)
        write_report(
            cfg.report_dir,
            cfg,
            logger,
            f"Verify - {cfg.serial}",
            [("Destino", verdict)],
            [(f"Destino ({dst.scanned})", dst)],
            verdict=verdict,
        )
        return EXIT_BLOCKED
    for cls in sorted(dst.by_class):
        print(f"  {cls:14} {dst.by_class[cls]:8}")
    archived = [k for k, v in dst.by_class.items() if k in ("DEEP_ARCHIVE", "GLACIER")]
    n_arch = sum(dst.by_class.get(k, 0) for k in archived)
    if dst.scanned == 0:
        verdict = "BLOQUEADO - destino vazio. Nada para entregar."
        print("\n!! " + verdict)
        write_report(
            cfg.report_dir,
            cfg,
            logger,
            f"Verify - {cfg.serial}",
            [("Destino", verdict)],
            [(f"Destino ({dst.scanned})", dst)],
            verdict=verdict,
        )
        return EXIT_BLOCKED
    if n_arch:
        verdict = (
            f"NAO ENVIAR - {n_arch} objeto(s) ainda arquivados no destino. "
            "O download do cliente FALHARA com InvalidObjectState. "
            "Copie de novo como STANDARD."
        )
        print("\n!! " + verdict)
        write_report(
            cfg.report_dir,
            cfg,
            logger,
            f"Verify - {cfg.serial}",
            [("Destino", verdict)],
            [(f"Destino ({dst.scanned})", dst)],
            verdict=verdict,
        )
        return EXIT_BLOCKED
    verdict = "OK - destino liberado para o cliente."
    print("\nOK - destino liberado.")
    print(
        f"\n  aws s3 sync s3://{cfg.dst_bucket}/{cfg.dst_prefix} "
        f"./restored-{cfg.serial}/ --region {cfg.region}"
    )
    print("\n  NOTA: o bucket destino apaga objetos em 30 dias (Delete-after-30d).")
    path = write_report(
        cfg.report_dir,
        cfg,
        logger,
        f"Verify - {cfg.serial}",
        [("Destino", verdict)],
        [(f"Destino ({dst.scanned})", dst)],
        verdict=verdict,
    )
    print(f"Relatorio: {path}")
    return EXIT_OK


def local_path_for(key: str, dst_prefix: str, dest_root: Path) -> Path:
    """Map a bucket key to its local download path, mirroring the prefix."""
    rel = key[len(dst_prefix) :] if key.startswith(dst_prefix) else key
    return dest_root / rel


def _parity_sample(items: dict[str, tuple[int, object]], limit: int = 50) -> str:
    names = sorted(items)
    lines = [f"- `{name}` ({items[name][0]} B)" for name in names[:limit]]
    if len(names) > limit:
        lines.append(f"- ... +{len(names) - limit} omitidos")
    return "\n".join(lines) if lines else "(nenhum)"


def cmd_parity(args: argparse.Namespace) -> int:
    """Compare source vs destination key sets (read-only)."""
    cfg, logger, s3 = _setup(args)
    print(f"Origem : {cfg.src_uri()} (ate {cfg.max_list})")
    src_map, src_cut = list_scope(s3, cfg.src_bucket, cfg.src_prefix, cfg.max_list)
    logger.log(
        "INFO",
        "parity",
        "s3:ListObjectsV2",
        cfg.src_uri(),
        "ok",
        extra={"listed": len(src_map), "truncated": src_cut},
    )
    print(f"Destino: {cfg.dst_uri()} (ate {cfg.max_list})")
    dst_map, dst_cut = list_scope(s3, cfg.dst_bucket, cfg.dst_prefix, cfg.max_list)
    logger.log(
        "INFO",
        "parity",
        "s3:ListObjectsV2",
        cfg.dst_uri(),
        "ok",
        extra={"listed": len(dst_map), "truncated": dst_cut},
    )
    result = compare_listings(src_map, dst_map)
    result.src_truncated = src_cut
    result.dst_truncated = dst_cut

    states = {"nunca solicitados": 0, "em restauracao": 0, "restaurados": 0, "falhas": 0}
    for rel in sorted(result.missing)[:20]:
        resource = f"s3://{cfg.src_bucket}/{cfg.src_prefix}{rel}"
        state, _, err = head_restore_state(s3, cfg.src_bucket, cfg.src_prefix + rel)
        if err is not None:
            code, msg = err
            logger.log("ERROR", "parity", "s3:HeadObject", resource, "fail", code, msg)
            states["falhas"] += 1
        else:
            logger.log("INFO", "parity", "s3:HeadObject", resource, "ok", extra={"state": state})
            if state == "restoring":
                states["em restauracao"] += 1
            elif state == "restored":
                states["restaurados"] += 1
            else:
                states["nunca solicitados"] += 1

    print(f"\nPARITY: {result.summary()}")
    print(f"  origem: {result.src_objects} objetos ({result.src_bytes / 1e9:.2f} GB)")
    print(f"  destino: {result.dst_objects} objetos ({result.dst_bytes / 1e9:.2f} GB)")
    if result.missing:
        print(f"  ausente mais antigo: {result.missing_oldest}")
        print(f"  ausente mais novo: {result.missing_newest}")
    print("  amostra HEAD (20): " + ", ".join(f"{key}={value}" for key, value in states.items()))

    sections = [
        (
            "Resumo",
            f"origem={result.src_objects} destino={result.dst_objects} "
            f"{result.summary()} "
            f"amostra={states} cortado={src_cut or dst_cut}",
        ),
        (f"Ausentes no destino ({len(result.missing)})", _parity_sample(result.missing)),
        (
            f"Tamanhos divergentes ({len(result.diverged)})",
            "\n".join(
                f"- `{name}` origem={result.diverged[name][0][0]} B "
                f"destino={result.diverged[name][1][0]} B"
                for name in sorted(result.diverged)[:50]
            )
            or "(nenhum)",
        ),
        (f"Extras no destino ({len(result.extra)})", _parity_sample(result.extra)),
    ]
    if src_cut or dst_cut:
        verdict = (
            f"INCONCLUSIVO - lista cortada em --max-list {cfg.max_list}; "
            "rode de novo com --max-list maior."
        )
        code_out = EXIT_BLOCKED
    elif not result.ok:
        verdict = (
            f"DIVERGENTE - {result.summary()}. Rode restore/status/copy de novo e repita o parity."
        )
        code_out = EXIT_BLOCKED
    else:
        verdict = (
            f"OK - destino espelha a origem "
            f"({result.dst_objects} objetos, {result.dst_bytes / 1e9:.2f} GB)."
        )
        code_out = EXIT_OK
    print(("\n!! " if code_out else "\n") + verdict)
    path = write_report(
        cfg.report_dir, cfg, logger, f"Parity - {cfg.serial}", sections, verdict=verdict
    )
    print(f"Relatorio: {path}\nTrilha: logs/{cfg.run_id}.jsonl")
    return code_out


def cmd_download(args: argparse.Namespace) -> int:
    cfg, logger, s3 = _setup(args)
    dest_root = Path(args.dest) if args.dest else Path(f"./restored-{cfg.serial}")
    specific: list[str] = list(args.key or [])
    prefix_filters: list[str] = []
    for raw in getattr(args, "prefix", None) or []:
        rel = raw.strip("/")
        for base in (cfg.dst_prefix.rstrip("/"), cfg.serial.strip("/")):
            if base and (rel == base or rel.startswith(f"{base}/")):
                rel = rel[len(base) :].strip("/")
                break
        if rel:
            prefix_filters.append(f"{cfg.dst_prefix}{rel}")
    skip_head = bool(getattr(args, "skip_head", False))
    head_extra: dict[str, bool] = {"head_skipped": True} if skip_head else {}

    targets: list[str] = []
    if specific:
        targets = specific
        print(f"Chaves especificas: {len(targets)}")
    else:
        print(f"Listando s3://{cfg.dst_bucket}/{cfg.dst_prefix} ...")
        token = None
        while True:
            params: dict[str, object] = {
                "Bucket": cfg.dst_bucket,
                "Prefix": cfg.dst_prefix,
                "MaxKeys": 1000,
            }
            if token:
                params["ContinuationToken"] = token
            page = s3.list_objects_v2(**params)  # type: ignore[arg-type]
            for obj in page.get("Contents", []) or []:
                if obj is None:
                    continue
                key = str(obj.get("Key", ""))
                if key.endswith("/"):
                    continue
                targets.append(key)
                if len(targets) >= cfg.max_list:
                    break
            if len(targets) >= cfg.max_list:
                print(f"  (limite --max-list={cfg.max_list} atingido; use --max-list maior)")
                break
            token = page.get("NextContinuationToken")
            if not token:
                break
    if prefix_filters:
        targets = [key for key in targets if any(key.startswith(p) for p in prefix_filters)]
        print(f"Filtro de prefixo: {len(prefix_filters)} -> {len(targets)} alvo(s)")
    print(f"Alvos: {len(targets)}")
    if not targets:
        print("Nada a baixar (nenhuma chave encontrada).")
        return EXIT_BLOCKED

    if not cfg.dry_run:
        dest_root.mkdir(parents=True, exist_ok=True)
    ok = skip = fail = 0
    total_bytes = 0
    for key in targets:
        res = f"s3://{cfg.dst_bucket}/{key}"
        if skip_head:
            state, head = "none", {"StorageClass": "STANDARD"}
            herr = None
        else:
            state, head, herr = head_restore_state(s3, cfg.dst_bucket, key)
        if state == "error" and herr:
            code, msg = herr
            logger.log("ERROR", "download", "s3:HeadObject", res, "fail", code, msg)
            fail += 1
            continue
        storage_class = str((head or {}).get("StorageClass") or "STANDARD")
        if not is_downloadable(storage_class, state):
            # HEAD on Deep Archive returns 200 even when unreadable; only a
            # completed restore (Restore: ongoing-request="false") allows GET.
            logger.log(
                "INFO",
                "download",
                "s3:GetObject",
                res,
                "skip",
                extra={
                    **head_extra,
                    "reason": "archived, cannot download",
                    "storage_class": storage_class,
                    "restore_state": state,
                },
            )
            skip += 1
            continue
        local = local_path_for(key, cfg.dst_prefix, dest_root)
        if cfg.dry_run:
            logger.log(
                "INFO",
                "download",
                "s3:GetObject",
                res,
                "dry_run",
                extra={**head_extra, "local_path": str(local)},
            )
            ok += 1
        else:
            local.parent.mkdir(parents=True, exist_ok=True)
            _, duration, derr = timed(s3.download_file, cfg.dst_bucket, key, str(local))
            if derr is not None:
                code, msg = derr
                logger.log(
                    "ERROR",
                    "download",
                    "s3:GetObject",
                    res,
                    "fail",
                    code,
                    msg,
                    duration,
                    extra={**head_extra, "local_path": str(local)},
                )
                fail += 1
                continue
            try:
                size = local.stat().st_size
            except OSError as exc:
                logger.log(
                    "ERROR",
                    "download",
                    "s3:GetObject",
                    res,
                    "fail",
                    "LocalReadError",
                    str(exc),
                    duration,
                    extra={**head_extra, "local_path": str(local)},
                )
                fail += 1
                continue
            logger.log(
                "INFO",
                "download",
                "s3:GetObject",
                res,
                "ok",
                duration_ms=duration,
                extra={**head_extra, "local_path": str(local), "bytes": size},
            )
            ok += 1
            total_bytes += size
        if (ok + skip + fail) % 50 == 0:
            print(f"  ... baixados={ok} pulados={skip} falhas={fail}")
    print(
        f"\nDOWNLOAD: baixados={ok} pulados={skip} falhas={fail} "
        f"({total_bytes / 1e9:.2f} GB) -> {dest_root}"
    )
    if fail:
        verdict = f"Falhas - ver trilha. Destino parcial em {dest_root}."
        code_out = EXIT_ERROR
    elif ok == 0:
        verdict = "Nada baixado - tudo arquivado ou inexistente. Rode `verify` e `copy` antes."
        code_out = EXIT_BLOCKED
    else:
        verdict = f"OK - {ok} arquivo(s) em {dest_root}."
        code_out = EXIT_OK
    path = write_report(
        cfg.report_dir,
        cfg,
        logger,
        f"Download - {cfg.serial}",
        [("Resultado", verdict)],
        verdict=verdict,
    )
    print(f"Relatorio: {path}\nTrilha: logs/{cfg.run_id}.jsonl")
    return code_out


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "__child":
        # Internal command: never exposed in the public parser or --help.
        from .ui.child import main as child_main

        return child_main(raw[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd in ("auto", "batch"):
        # Local import avoids a cycle: auto.py reuses these CLI step functions.
        from .auto import cmd_auto, cmd_batch

        return cmd_auto(args) if args.cmd == "auto" else cmd_batch(args)
    handlers = {
        "perms": cmd_perms,
        "plan": cmd_plan,
        "restore": cmd_restore,
        "status": cmd_status,
        "copy": cmd_copy,
        "verify": cmd_verify,
        "parity": cmd_parity,
        "download": cmd_download,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
