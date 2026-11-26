# Task 00 — Repo genérico: rename e buckets por parâmetro

## Escopo

Entra: rename do pacote, do console script, do `prog`/user-agent da CLI, de todos os imports
e docs, remoção dos buckets de cliente como defaults e resolução por flag/env. Não entra:
mudança de fluxo S3, mudança de formato de estado/handoff/log/relatório, shim de
compatibilidade com o nome antigo (o usuário quer o repo sem resquícios de cliente).

**Breaking change explícito:** `python -m zscan_restore` deixa de existir. Toda invocação
passa a ser `python -m s3_glacier_restore` com `--src-bucket`/`--dst-bucket` ou
`S3_RESTORE_SRC_BUCKET`/`S3_RESTORE_DST_BUCKET`.

## Arquivos

- renomear: `src/zscan_restore/` → `src/s3_glacier_restore/` (usar `git mv`)
- modificar: `pyproject.toml`, `uv.lock`, `src/s3_glacier_restore/{config.py,cli.py,aws_factory.py,auto.py}`,
  `tests/*.py`, `README.md`, `docs/RUNBOOK.md`, `AGENTS.md`
- testar: `tests/test_cli.py`, `tests/test_auto.py`, `tests/test_s3ops.py`, `tests/test_workers.py`, `tests/test_audit.py`

## Interfaces

- `pyproject.toml`: `name = "s3-glacier-restore"`; script
  `s3-glacier-restore = "s3_glacier_restore.cli:main"`; `[tool.setuptools.packages.find]` continua em `src`.
- `config.py`: sem `DEFAULT_SRC_BUCKET`/`DEFAULT_DST_BUCKET`; `RestoreConfig.src_bucket` e
  `dst_bucket` obrigatórios e não vazios; mensagem de erro em pt-BR.
- `cli.py`: `_common` usa `default=os.environ.get("S3_RESTORE_SRC_BUCKET", "")` e
  `default=os.environ.get("S3_RESTORE_DST_BUCKET", "")`; `_setup` converte `ValueError` de
  config em `EXIT_BLOCKED` com mensagem clara, sem traceback.
- `write_handoff`: texto gerado passa a usar `python -m s3_glacier_restore download ...`
  com `--max-list 700000`.
- `aws_factory.py`: `user_agent_extra="s3-glacier-restore/0.1.0"`.
- CLI `prog="s3-glacier-restore"`.

## Passos

- [x] Teste RED: `RestoreConfig(serial=..., src_bucket="", dst_bucket="...")` levanta `ValueError`.
- [x] Teste RED: `build_parser().parse_args(["plan", "--serial", ...])` com env limpo ⇒ buckets vazios ⇒ `_setup` retorna `EXIT_BLOCKED`.
- [x] Teste RED: com env `S3_RESTORE_SRC_BUCKET`/`S3_RESTORE_DST_BUCKET`, parser.resolve usa os valores.
- [x] Teste RED: `write_handoff` contém `s3_glacier_restore`, `--max-list 700000` e não contém o nome antigo.
- [x] Rodar RED e colar a saída.
- [x] Em `tests/test_audit.py`, substituir o literal com formato de access key por fake montado
      em runtime (`"AKIA" + "0" * 16`), preservando a asserção de redação.
- [x] `git mv src/zscan_restore src/s3_glacier_restore` e atualizar todos os imports.
- [x] Aplicar rename em `pyproject.toml`, `prog`, user-agent, `auto.py`, docs e `AGENTS.md`.
- [x] Remover defaults de bucket e implementar validação/env/EXIT_BLOCKED.
- [x] `uv lock` para atualizar o nome do projeto no lockfile.
- [x] GREEN + gates + `rg`.

## Critérios de aceite (Dado/Quando/Então)

- Dado env limpo e sem flags, Quando qualquer comando roda, Então falha com `EXIT_BLOCKED` e mensagem dizendo quais variáveis/flags informar.
- Dado as variáveis de ambiente, Quando o comando roda, Então os buckets são resolvidos sem alterar o código.
- Dado o working tree (exceto `spec-agent/`), Quando `rg -n "zscan|Zscan|ZSCAN"` roda, Então não há nenhum match (inclusive em docs, testes, handoff gerado e lockfile).
- Dado o working tree, Quando `rg -l "AKIA[0-9A-Z]{16}"` roda, Então nenhum arquivo é listado.
- Dada a suíte atual, Quando roda, Então os 45 testes passam com os novos nomes e buckets de teste fictícios.

## Verificação

```bash
uv run pytest tests/test_cli.py tests/test_auto.py -q   # RED → GREEN
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv lock --check
rg -n "zscan|Zscan|ZSCAN" . --glob '!*.pyc' --glob '!spec-agent/**'
rg -l "AKIA[0-9A-Z]{16}" . --glob '!*.pyc'
```

## Rollback

`git mv src/s3_glacier_restore src/zscan_restore` e `git checkout HEAD -- pyproject.toml uv.lock
src tests README.md docs/RUNBOOK.md AGENTS.md`. Fazer em commit atômico único, antes de qualquer
outra task, para não misturar rename com feature nova.

## Resultado esperado

Repo com nome genérico, zero resquício de cliente e buckets configuráveis — suíte verde nos
novos nomes antes de seguir para a Task 01.

## Evidência (2026-11-25)

RED (`uv run pytest tests/test_cli.py tests/test_auto.py -q`): `....FFFF......F.......` —
5 falhas: bucket obrigatório não validado, env não lido, `_setup` não bloqueia, `prog` antigo,
handoff com nome antigo.

GREEN e gates:

```
uv run pytest                    → 45 passed in 0.80s
uv run ruff check src tests      → All checks passed!
uv run ruff format --check src tests → 15 files already formatted
uv lock --check                  → Resolved 15 packages in 1ms
rg -n "zscan|Zscan|ZSCAN" . --hidden --glob '!*.pyc' --glob '!spec-agent/**' --glob '!.git/**'
                                 → sem saída (exit 1 = nenhum match)
rg -l "AKIA[0-9A-Z]{16}" . --hidden --glob '!*.pyc' --glob '!.git/**'
                                 → sem saída (exit 1 = nenhum arquivo)
uv run python -m s3_glacier_restore plan --serial 1234-5678-90
                                 → "ERRO: configuracao invalida: bucket de origem ausente:
                                    informe --src-bucket ou S3_RESTORE_SRC_BUCKET" (rc=2)
```

Branch: `refactor/generic-rename`. Commit atômico único com rename, testes, docs e plano.
