# Comando `parity`: auditoria de paridade origem x destino

## Problema

`verify` diz se o destino está liberado, mas não diz se está **completo**:
com `--max-list` baixo ele aprovava censo cortado, e não havia como listar
o que faltava (caso real: 656 arquivos novos + 1 divergente após o `auto`).

## Decisão

- Novo comando read-only `parity --src/--dst` (ou legado `--serial`):
  lista os dois prefixos, compara chave a chave (ausentes, extras,
  divergentes), informa idade do ausente mais velho/novo e amostra HEAD de
  20 ausentes (`nunca solicitados`/`em restauracao`/`restaurados`/`falhas`).
- `INCONCLUSIVO` (exit 2) quando qualquer lado corta no `--max-list`;
  `DIVERGENTE` (exit 2) com gaps; `OK` (exit 0) quando espelha.
- `run_census` ganha `truncated`; `plan` avisa e `verify` bloqueia em censo
  cortado (o default isolado de 20000 não aprova mais parcial).
- Cartão `parity` no painel admin (mesma lista `ADMIN_COMMANDS`).

## Arquivos

- novo: `src/s3_glacier_restore/parity.py`, `tests/test_parity.py`
- alterados: `cli.py` (`cmd_parity`, parser, handlers, avisos de corte),
  `s3ops.py` (`Census.truncated`), `ui/entry.py`, `ui/runner.py`,
  `ui/index.html`, testes, `README.md`, `docs/RUNBOOK.md`

## Verificação

- `uv run pytest` verde; `ruff check`, `ruff format --check`,
  `uv lock --check` e `git diff --check` verdes.
- `parity` real contra `origem-bkt` × `destino-bkt` reproduz o
  diagnóstico manual (656 ausentes de hoje + 1 divergente).
