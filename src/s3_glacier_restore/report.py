"""Human-readable Markdown report generated from the audit trail."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .audit import AuditLogger
from .config import RestoreConfig
from .s3ops import Census


def _table(by_class: dict[str, int], scanned: int) -> str:
    lines = ["| Classe | Objetos | % | Leitura |", "| --- | ---: | ---: | --- |"]
    for cls in sorted(by_class):
        n = by_class[cls]
        pct = round(n / scanned * 100, 1) if scanned else 0.0
        readable = {
            "DEEP_ARCHIVE": "NAO - precisa restore",
            "GLACIER": "NAO - precisa restore",
            "GLACIER_IR": "SIM - cobra retrieval",
            "FOLDER_MARKER": "n/a",
        }.get(cls, "SIM")
        lines.append(f"| {cls} | {n} | {pct} | {readable} |")
    return "\n".join(lines)


def write_report(
    report_dir: Path,
    cfg: RestoreConfig,
    logger: AuditLogger,
    title: str,
    sections: list[tuple[str, str]],
    censuses: list[tuple[str, Census]] | None = None,
    verdict: str = "",
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{cfg.serial}-{cfg.run_id}.md"
    now = datetime.now(UTC).isoformat(timespec="seconds")
    out: list[str] = [
        f"# {title}",
        "",
        f"Gerado: {now} | run_id: `{cfg.run_id}` | perfil: `{cfg.profile}`",
        f"Conta: `{logger.account}` | Ator: `{logger.actor}`",
        f"Origem: `s3://{cfg.src_bucket}/{cfg.src_prefix}`",
        f"Destino: `s3://{cfg.dst_bucket}/{cfg.dst_prefix}`",
        "",
    ]
    if censuses:
        for name, census in censuses:
            out.append(f"## {name} ({census.scanned} objetos, {census.bytes_total / 1e9:.2f} GB)")
            out.append("")
            out.append(_table(census.by_class, census.scanned))
            out.append("")
            if not census.ok:
                out.append(f"> Censo falhou: `{census.error_code}` {census.error_msg}")
                out.append("")
    for heading, body in sections:
        out.append(f"## {heading}")
        out.append("")
        out.append(body)
        out.append("")
    if verdict:
        out.append("## Veredito")
        out.append("")
        out.append(verdict)
        out.append("")
    out.append("## Auditoria")
    out.append("")
    out.append(f"Trilha completa: `logs/{cfg.run_id}.jsonl`")
    counts = logger.counts
    out.append(f"Eventos: {counts}")
    out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
    return path
