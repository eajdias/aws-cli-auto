# Plano: exe único com interfaces admin/cliente + genericização do repo (`spec-agent/2026-11-25-client-download-ui/`)

> Fonte da verdade. Coordenador lê SÓ este arquivo; worker abre 1 task por vez.
> Constituição: `AGENTS.md` + `code-playbooks/references/{python,frontend}.md`.
> Aprovação do usuário do `index.md`: **aprovada em 2026-11-25** ("Aprovar e iniciar Task 00").

## Objetivo

Tornar o repo genérico e entregar **um único `.exe` buildado uma vez**, com duas interfaces:

- **Admin:** usada pelo técnico no PC dele, desbloqueada por senha definida no build, com as
  operações AWS locais usando a configuração padrão do AWS CLI daquela máquina (`auto` etc.).
- **Cliente:** usada no PC do cliente, com credenciais coladas, listagem ao vivo do S3,
  análise de espaço, seleção de tudo ou de pastas específicas e escolha do destino com
  mesclagem nas pastas de mesmo nome.

O download permanece manual e exclusivo do modo cliente. O modo admin nunca baixa nada.
Rebuild só quando o código muda — nunca por cliente/serial/bucket.

## Evidência de permissão (teste real nesta sessão)

Identidade das credenciais informadas: `arn:aws:iam::<ACCOUNT_ID>:user/pipeline-restore`.

| Verificação | Resultado |
| --- | --- |
| `sts:GetCallerIdentity` | OK |
| `s3api list-objects-v2 --delimiter /` em `destino-bkt/1234-5678-90/` | **PASS** → `Database/`, `Images/`, `Reports/`, `Videos/` |
| `s3api head-object` em amostra de `Database/` | **PASS**, objeto `STANDARD`, 1,36 GB |

Consequência: o modo cliente pode listar ao vivo — não precisa de manifesto distribuído nem
de rebuild por cliente. Esse usuário tem deny explícito de `s3:RestoreObject`; por isso o
modo admin **nunca** usa essas credenciais, e sim o profile padrão local do técnico.

Achado no scan do plano: `rg -l "AKIA[0-9A-Z]{16}"` hoje lista `tests/test_audit.py` (literal
usado no teste de redação). A Task 00 troca esse literal por fake montado em runtime; nenhum
valor é repetido neste plano.

## Nomenclatura (usuário: "conforme as funções na AWS, auto-explicativa")

| Coisa | Nome |
| --- | --- |
| Repo / distribuição | `s3-glacier-restore` |
| Pacote Python | `s3_glacier_restore` |
| CLI | `python -m s3_glacier_restore` |
| Entregável | `s3-download-client.exe` (um build, para todos os clientes) |
| AppData | `%LOCALAPPDATA%\s3-glacier-restore\` |
| Buckets | `--src-bucket`/`--dst-bucket` ou `S3_RESTORE_SRC_BUCKET`/`S3_RESTORE_DST_BUCKET` |

## Decisões fechadas

| Decisão | Escolha |
| --- | --- |
| Genericização | Repo inteiro, sem resquício de nome de cliente |
| Build | **Um exe genérico**, rebuild só quando o código muda (10 clientes/dia sem rebuild) |
| Descoberta de pastas | Listagem ao vivo com `ListObjectsV2` + `Delimiter="/"` no modo cliente |
| Credenciais cliente | Bloco `AWS_*` colado na tela; enviado ao filho por `env`, nunca em argv/log/disco |
| Modo admin | Senha definida no build (hash gerado no build); operações com o profile padrão local |
| Seleção | Tudo ou pastas específicas (prefixos de 1º nível) |
| Destino | Pasta escolhida no PC do cliente; mesclagem nas subpastas de mesmo nome |
| Escopo | Admin = orquestração; cliente = somente download |
| Gate do download | `verify` no admin + HEAD amostral (5 por prefixo) no cliente antes de baixar |

## Arquitetura

**Admin (PC do técnico):** exe → senha → painel com `plan`, `perms`, `restore`, `status`,
`copy`, `verify`, `auto`, `batch` → subprocesso do próprio exe com a configuração padrão da
AWS CLI local → trilha/relatório em `logs\`/`reports\` relativos ao cwd do exe (salvo
`--log-dir`/`--report-dir`). Sem download.

**Cliente (PC do cliente):** exe → bloco `AWS_*` + serial/bucket → comandos internos `__child`
com credencial somente no `env` do filho → listagem paginada e agregada por prefixo →
checkboxes + espaço livre → pasta destino (dialog nativo, com campo manual como fallback) →
HEAD amostral por prefixo selecionado → `download` em subprocesso com `--skip-head` →
progresso → relatório/auditoria no appdata.

Mesclagem: o mapeamento existente `local_path_for(key, dst_prefix, dest_root)` já preserva
as subpastas do S3, então `<serial>/Database/...` cai em `<destino>\Database\...` e se funde
ao que já existe. Reexecutar sobrescreve os arquivos de mesmo nome; nada é apagado.

## Contratos

### Públicos (CLI)

- CLI `download` ganha `--prefix` (append), `--skip-head` e aceita `--profile ""|env`.
- `auto`/`batch` continuam com `--max-list 700000`; `write_handoff` carrega esse valor.
- Buckets são obrigatórios por flag/env; ausência ⇒ `EXIT_BLOCKED`, nunca default de cliente.

### Internos (`__child`, não expostos no help)

Todos recebem credencial pelo `env` do filho e respondem JSON no stdout (sem segredo):

- `__child validate --bucket B --serial S` ⇒ `{ok, region, arn}` ou `{ok:false, errors}`.
- `__child list-prefixes --bucket B --serial S --max-list 700000` ⇒
  `{prefixes[{prefix, objects, bytes}], objects, bytes_total, elapsed_ms, truncated}`.
- `__child sample-heads --bucket B --serial S --prefix P --sample-size 5 ...` ⇒
  `{ok, samples[{prefix, key, storage_class, state, downloadable}], failures}`.
- `__child download ...` delega ao `cmd_download` com `--profile env --skip-head --prefix ...`
  e mantém o stdout de progresso.

### HTTP local (127.0.0.1)

- `POST /api/admin/unlock` → `{password}` ⇒ `{ok}` ou `{ok:false}` (senha nunca é logada).
- `POST /api/admin/run` → `{command, args[]}` ⇒ `{ok, run_id}`; allowlist sem `download`
  e sem `__child`.
- `POST /api/validate-credentials` → `{block, bucket, serial}` ⇒ `{ok, region, arn}`.
- `POST /api/list-prefixes` → `{block, bucket, serial}` ⇒ objeto do `__child list-prefixes`.
- `POST /api/sample-heads` → `{block, bucket, serial, prefixes[], sample_size}` ⇒ objeto do `__child sample-heads`.
- `POST /api/check-destination` → `{path, required_bytes}` ⇒ `{exists, free_bytes, ok}`.
- `POST /api/download` → `{block, bucket, serial, prefixes[], dest}` ⇒ `{ok, run_id}`.
- `GET /api/progress?run_id=...` → `{status, lines[], downloaded, skipped, failed, exit_code}`.
- `POST /api/pick-folder` → `{path}` ou `{path:null}`.
- `GET /api/health` → `{ok:true}`.

Preflight: `shutil.disk_usage(dest).free >= bytes_total * 1.10`, senão 409 antes de spawnar.

## Não regressão

Fluxo S3 de `plan`/`restore`/`status`/`copy`/`verify`/`download`/`auto`/`batch` inalterado;
só mudam nome de invocação e origem dos buckets. Formato de `state/`, `handoff/`, `logs/` e
`reports/` preservado. `auto`/`batch` continuam sem download. O gate
`restaurados=N em_andamento=0 nao_solicitados=0 falhas=0 de N` continua intacto.

## Ordem e dependências

0. `00-generic-rename.md` — rename e buckets por parâmetro (sem deps)
1. `01-cli-download-options.md` — `--prefix`, `--skip-head`, `--profile env` (dep: 00)
2. `02-prefix-listing.md` — listagem/agregação e HEAD amostral em runtime (dep: 00)
3. `03-credential-parser.md` — parser do bloco `AWS_*` com redação (dep: 00)
4. `04-ui-backend.md` — servidor local, dois modos, `__child`, endpoints, subprocessos (deps: 01, 02, 03)
5. `05-ui-frontend.md` — duas interfaces, seleção, espaço, mesclagem, progresso (dep: 04)
6. `06-packaging.md` — PyInstaller onefile, senha do admin no build, smoke (deps: 00, 04, 05)
7. `07-docs-and-gates.md` — README/RUNBOOK, docs-sync, gates finais (dep: 06)

## Status

| Task | Status | Evidência do `done` |
| --- | --- | --- |
| 00 | done | RED 5 falhas → GREEN `45 passed`; ruff/lock/rg verdes (2026-11-25) |
| 01 | done | RED 5 falhas + 1 caso serial-prefixed → GREEN `51 passed`; ruff/lock/rg/--help verdes (2026-11-25) |
| 02 | done | RED import + `..FFF`/`..FF.` → GREEN `56 passed`; ruff/lock verdes (2026-11-25) |
| 03 | done | RED import → GREEN `65 passed`; ruff/format/rg verdes (2026-11-25) |
| 04 | done | RED import + 3 falhas → GREEN `78 passed`; ruff/lock/rg/--help verdes (2026-11-25) |
| 05 | done | RED 7 falhas → GREEN `87 passed`; smoke visual/console limpo, ruff/format/lock/rg verdes (2026-11-25) |
| 06 | done | RED 6 falhas → GREEN; build sem warning de admin/UPX; smoke `health=True unlock_ok=True unlock_bad=True validate_json=True stderr_lines=0`; correção do roteamento admin no exe validada com `admin_cli_routed=True exited=True stderr_lines=0` (2026-11-25) |
| 07 | done | Docs auditadas (`docs-writer`) e corrigidas; `100 passed`; ruff/format/lock/`git diff --check` verdes; scans de segredo/nome morto limpos; exe rebuildado e smoke HTTP + admin OK (2026-11-25) |

## Riscos

- **Breaking change do rename (alta / média).** Mitigação: Task 00 atômica, docs/testes no
  mesmo commit, sem shim do nome antigo.
- **Listagem de ~669 mil objetos no cliente (média / médio).** Mitigação: paginação com
  `get_paginator("list_objects_v2")` + `Delimiter="/"` só para descobrir nomes; agregação em
  memória na passada completa; sem HEAD por objeto; `elapsed_ms` e `truncated` exibidos.
- **Senha do admin no binário (média / baixo).** É tranca de UI local, não credencial AWS —
  decisão explícita do usuário. Mitigação: hash gerado no build, nunca reaproveitado.
- **Build elevado (média / baixo).** PyInstaller 6.x avisa e 7.0 bloqueará; mitigação: rodar o
  build em terminal não elevado (nesta máquina, tarefa agendada com `/RL LIMITED`).
- **Dialog de pasta nativo (média / médio).** `tkinter.filedialog.askdirectory()` no thread
  da requisição (alvo Windows); campo manual sempre disponível.
- **Espaço insuficiente (alta / alto).** ~500 GB no caso atual. Mitigação: preflight com
  10% de folga antes de iniciar.
- **Cancelamento parcial (média / médio).** Arquivos já gravados ficam; reexecução sobrescreve.
- **Janela do bucket (alta / alto).** Objetos expiram em 30 dias; UI mostra o aviso.

## Unknowns (dono e próximo passo)

1. **Buckets por cliente.** Dono: usuário. Confirmar se o técnico sempre informa
   destino/serial na tela ou se deve haver um default de build por lote. Default do plano:
   informar na tela.
2. **Certificado de assinatura.** Dono: usuário. Sem certificado, o exe sai sem assinar.
3. **Escopo do painel admin.** Dono: usuário. Default do plano: expor todos os comandos
   exceto `download` e `__child`; se quiser só `auto`/`batch`, ajustar a allowlist na Task 04.

## Breaking changes

- `zscan_restore` → `s3_glacier_restore`, `zscan-restore` → `s3-glacier-restore`, user-agent e `prog` atualizados.
- Consumidores: README, RUNBOOK, `AGENTS.md`, `write_handoff`, testes, comandos operacionais.
- Migração: trocar `python -m zscan_restore` por `python -m s3_glacier_restore` e passar
  buckets por flag/env. Sem janela de depreciação (repo privado, pedido explícito).
- Verificação: suíte verde + `rg -n "zscan|Zscan|ZSCAN" . --glob '!spec-agent/**'` vazio.

## Rollback global

Código por task com `git checkout HEAD -- <paths>`; `dist/`, `build/` e `build_assets/`
gitignored; manter o exe anterior até a entrega. Nenhuma migração de dados; irreversível é
apenas a entrega do exe, que exige o gate final.

## Verificação global

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest
uv lock --check
uv run --group build python scripts/build_client.py --admin-password <SENHA>
Get-Content "$env:LOCALAPPDATA\s3-glacier-restore\ui.port"
curl.exe http://127.0.0.1:<PORTA>/api/health
rg -l "AKIA[0-9A-Z]{16}" . --glob '!*.pyc' --glob '!spec-agent/**'
rg -n "zscan|Zscan|ZSCAN" . --glob '!*.pyc' --glob '!spec-agent/**'
```

## Fontes consultadas

- Boto3: `get_paginator("list_objects_v2")` com `Delimiter`/`Prefix`/`PaginationConfig`;
  credenciais por `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN` e
  `profile_name=None` (Context7 `/boto/boto3`).
- PyInstaller: spec onefile com `EXE(console=False, name=...)`, `datas` embutidos no CArchive
  (Context7 `/pyinstaller/pyinstaller`).
- Teste ao vivo de `ListBucket`/`HeadObject` com as credenciais do cliente nesta sessão.

## Definition of Done

- [ ] Exe único, buildado uma vez, com modo admin (senha no build) e modo cliente.
- [ ] Listagem ao vivo funciona com as credenciais do cliente; sem manifesto embutido/distribuído.
- [ ] Seleção de tudo/pastas, análise de espaço e mesclagem nas subpastas existentes.
- [ ] Modo admin não oferece download; cliente não oferece restore/copy.
- [ ] Repo sem nome de cliente; buckets por flag/env.
- [ ] RED observado antes do GREEN nas tasks que mudam comportamento.
- [ ] `ruff check`, `ruff format --check`, `pytest`, `uv lock --check` verdes com saída colada.
- [ ] README e RUNBOOK atualizados e conferidos contra código/manifesto.
- [ ] Nenhuma credencial gravada em arquivo, log, relatório, lockfile ou commit.
