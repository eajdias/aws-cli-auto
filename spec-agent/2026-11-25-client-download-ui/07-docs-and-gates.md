# Task 07 — Documentação, auditoria de docs e gate final

## Escopo

Entra: README, RUNBOOK, memória de projeto, conferência de docs contra código e `pyproject.toml`,
varredura por credencial e gates finais. Não entra: entrega física do exe, alteração de fluxo S3.

## Arquivos

- modificar: `README.md`, `docs/RUNBOOK.md`, `.opencode/memory/lessons.md`,
  `.opencode/memory/patterns.md`, `spec-agent/2026-11-25-client-download-ui/index.md`

## Conteúdo esperado

- README: seção "Entrega ao cliente (exe)" com pré-requisitos (`s3:GetObject`, ~500 GB no
  caso atual, antes dos 30 dias), o bloco de credenciais em **placeholder** (nunca valor real),
  passo a passo das duas interfaces, onde ficam `logs/`/`reports/` no appdata e aviso curto de
  rotação de chaves.
- RUNBOOK: como buildar **uma vez** (`build_client.py --admin-password`), como o técnico roda
  `auto` no painel admin, como o cliente lista/seleciona/baixa, como testar 1 arquivo antes de
  liberar o restante, como retomar após cancelamento. Explicitar que não há manifesto por cliente
  e que `auto` continua local e não baixa nada.
- Memória de projeto (sem valores): padrão "credencial colada ⇒ `env` do processo filho",
  lição "exe é runtime-configurado; nunca gerar manifesto/build por cliente" e preferência do
  usuário por notas operacionais curtas, sem repetir conselhos de segurança já aceitos.

## Passos

- [x] Atualizar README e RUNBOOK.
- [x] Rodar subagente `docs-writer` para conferir docs contra código e `pyproject.toml`; aplicar correções.
- [x] Confirmar que README/RUNBOOK não têm termo morto: `manifesto embutido`, `zscan-client`, `ZSCAN_`, `--serial` no build (o fake corrigido em `tests/test_audit.py` já limpa o scan de chave na Task 00).
- [x] Rodar `rg -l "AKIA[0-9A-Z]{16}"` no working tree; resultado deve ser vazio (saída só com nomes de arquivo, nunca o valor).
- [x] Confirmar que nenhum arquivo gerado (`dist/`, `build/`, `_build_config.py`, `ui.port`) foi commitado.
- [x] Rodar gates finais e colar a saída real.
- [x] Marcar tasks no `index.md` com a evidência colada.

## Critérios de aceite (Dado/Quando/Então)

- Dado a doc lida por alguém sem contexto, Quando segue os passos, Então consegue buildar uma vez e atender vários clientes sem rebuild.
- Dado o diff final, Quando varrido pela regex de chave, Então nenhum arquivo é listado.
- Dado o plano, Quando todas as tasks estão `done`, Então cada status tem evidência de comando executado.

## Verificação

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest
uv lock --check
rg -l "AKIA[0-9A-Z]{16}" . --glob '!*.pyc'
rg -n "zscan|Zscan|ZSCAN" . --glob '!*.pyc' --glob '!spec-agent/**'
rg -n "manifesto embutido|zscan-client|ZSCAN_" README.md docs
git status --short
git --no-pager diff --stat
```

## Evidência (2026-11-25)

- `docs-writer` corrigiu `README.md` (pré-requisito `s3:ListBucket` + `s3:GetObject`, nomes de
  `ADMIN_SALT`/`ADMIN_PASSWORD_SHA256` e do build com `--group build`) e `docs/RUNBOOK.md`
  (escopo do CLI, `--workers 20` em `auto`/`batch`, troubleshooting do roteamento CLI).
  Patches adicionais aplicados em `AGENTS.md` e `spec-agent/**` (o subagente não tem permissão
  de escrita fora de `docs/**` e `README.md`).
- Bug encontrado na auditoria: `ui/entry.main()` tratava `auto`/`verify` do painel admin como
  diretório de appdata. RED em 2 testes novos → GREEN; no exe real:
  `admin_cli_routed=True exited=True stderr_lines=0`.
- `uv run pytest` → `100 passed in 4.43s`; `uv run ruff check src tests scripts` →
  `All checks passed!`; `uv run ruff format --check src tests scripts` → `33 files already
  formatted`; `uv lock --check` → `Resolved 22 packages in 1ms`; `git diff --check` limpo.
- Build real refeito: `build_ok=True exe=...dist\s3-download-client.exe`; log termina em
  `Build complete!` e `AVISO: exe nao assinado; o SmartScreen pode bloquear a primeira execucao.`
- Smoke HTTP do exe: `health=True unlock_ok=True unlock_bad=True validate_json=True
  validate_status=400 validate_errors=InvalidClientTokenId: The security token included in the
  request is invalid. stderr_lines=0`; nenhum processo órfão do exe restou.
- `secret_scan=clean`, `legacy_name_scan=clean`, `dead_term_scan=clean`; `git ls-files dist build
  src/s3_glacier_restore/ui/_build_config.py ui.port` vazio.

## Rollback

`git checkout HEAD -- README.md docs/RUNBOOK.md spec-agent/2026-11-25-client-download-ui/index.md`;
memória de projeto é reversível por edição (não contém dado sensível).

## Resultado esperado

Docs fiéis ao código, gates verdes com evidência colada e nenhum segredo no working tree.
