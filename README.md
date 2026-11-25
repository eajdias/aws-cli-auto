# s3-glacier-restore

Pipeline S3 para restaurar objetos do Glacier: copia
`s3://<SRC_BUCKET>/<PREFIXO>/` para `s3://<DST_BUCKET>/<PREFIXO>/`
em `STANDARD` e libera o download do cliente.

O escopo de cada run é um par bucket+prefixo. O formato legado
`<SERIAL>/` (`1234-5678-90/`) continua funcionando, mas qualquer prefixo
vale: cole `s3://bucket/prefixo/` e a etiqueta é inferida do último segmento
(`--serial`/`--label` só é obrigatório quando o prefixo é a raiz do bucket).

> **Por que S3 direto e não DataSync?** As credenciais operacionais têm acesso
> S3 completo e zero acesso DataSync. O `aws s3 cp` com `--storage-class
> STANDARD` faz a mesma cópia sem depender de roles do DataSync.

## Stack

| Camada   | Tecnologia                                    |
| -------- | --------------------------------------------- |
| Linguagem| Python 3.11+ via `uv` (venv `.venv` + `uv.lock`) |
| AWS      | boto3 (S3: list/head/restore/copy/download)   |
| CLI      | argparse stdlib (`uv run python -m s3_glacier_restore`) |
| UI       | servidor HTTP local (loopback) + HTML/CSS/JS embutidos em `ui/index.html` |
| Exe      | PyInstaller onefile (`s3-download-client.exe`), buildado uma vez |
| Logs     | JSONL auditável (`logs/<run_id>.jsonl`)       |
| Testes   | pytest mockado (`uv run pytest`)              |
| Lint     | ruff tolerância zero (`uv run ruff check`)    |

## Arquitetura

```
src/s3_glacier_restore/
├── cli.py         → subcomandos (perms/plan/restore/status/copy/verify/download/auto/batch)
├── auto.py        → orquestração auto (checkpoint, espera, gate de status, batch)
├── config.py      → RestoreConfig (sem segredos; profile/região/buckets)
├── aws_factory.py→ sessão boto3 por --profile, retries, identidade (sts)
├── s3ops.py      → census, head, restore, copy, download (puro boto3)
├── audit.py      → AuditLogger: JSONL + console, run_id, sem segredos
├── report.py     → relatório .md por run (evidência p/ supervisor)
└── ui/           → entry/server/runner/child/auth: painéis admin e cliente no exe
```

## Quickstart

Buckets são obrigatórios: use as flags `--src-bucket`/`--dst-bucket` ou defina uma vez
no shell (não há default embutido):

```powershell
$env:S3_RESTORE_SRC_BUCKET = "<SRC_BUCKET>"
$env:S3_RESTORE_DST_BUCKET = "<DST_BUCKET>"
```

```bash
uv sync   # cria .venv e instala tudo (runtime + dev)

# 0. Preflight — OBRIGATÓRIO antes de tudo (read-only)
uv run python -m s3_glacier_restore plan --serial 1234-5678-90 --profile verificar

# Forma genérica (qualquer prefixo; etiqueta inferida do último segmento):
uv run python -m s3_glacier_restore plan \
  --src s3://origem-bkt/1234-5678-90/ --dst s3://destino-bkt/1234-5678-90/ \
  --profile verificar

# 1. Se o plan bloquear → restaurar (Deep Archive: ~12h Standard / ~48h Bulk)
uv run python -m s3_glacier_restore restore --serial 1234-5678-90 --profile verificar --dry-run
uv run python -m s3_glacier_restore restore --serial 1234-5678-90 --profile verificar
uv run python -m s3_glacier_restore status --serial 1234-5678-90 --profile verificar

# 2. Copiar para o public em STANDARD
uv run python -m s3_glacier_restore copy --serial 1234-5678-90 --profile verificar --dry-run
uv run python -m s3_glacier_restore copy --serial 1234-5678-90 --profile verificar

# 3. Verificar antes de entregar (recusa se houver arquivado ou se o destino estiver vazio)
uv run python -m s3_glacier_restore verify --serial 1234-5678-90 --profile verificar --max-list 700000

# 3b. Conferir paridade origem x destino (ausentes, extras, divergentes + idades)
uv run python -m s3_glacier_restore parity --serial 1234-5678-90 --profile verificar --max-list 700000

# 4. Baixar localmente (tudo ou chaves específicas)
#    (se usou `auto`, o comando pronto já está em handoff/<SERIAL>-download.txt)
uv run python -m s3_glacier_restore download --serial 1234-5678-90 --profile verificar --dest ./restored-1234-5678-90 --max-list 700000
uv run python -m s3_glacier_restore download --serial 1234-5678-90 --profile verificar --dest ./caso --key 1234-5678-90/Images/foto.png --key 1234-5678-90/Database/dump.sql

# Prefixo grande: `--max-list` é obrigatório. O default isolado é 20000;
# `plan`, `verify` e `parity` recusam resultado cortado (INCONCLUSIVO) —
# sem `--max-list 700000` você só vê as primeiras 20 mil chaves.

# 5. Auditar permissões da credencial
uv run python -m s3_glacier_restore perms --serial 1234-5678-90 --profile verificar
```

## Entrega ao cliente (exe único)

O `s3-download-client.exe` é buildado **uma vez** e atende todos os clientes. Origem,
destino (URIs `s3://bucket/prefixo/`) e etiqueta são informados na UI: não existe
manifesto por cliente nem rebuild por prefixo.

### Pré-requisitos do cliente

- `s3:ListBucket` e `s3:GetObject` no bucket de destino; o cliente não restaura e não copia nada.
- Espaço livre suficiente para o serial `1234-5678-90` (a UI checa com 10% de margem).
- O bucket de destino apaga objetos em 30 dias — baixe e guarde a cópia local.

### Build (técnico, uma vez)

```bash
# senha padrão do build: eajdias
uv run --group build python scripts/build_client.py

# outra senha:
uv run --group build python scripts/build_client.py --admin-password <SENHA_DO_ADMIN>
```

Sem `--admin-password`/`S3_RESTORE_ADMIN_PASSWORD`, o build usa a senha padrão
`eajdias`. A senha vira `ADMIN_SALT` + `ADMIN_PASSWORD_SHA256` em
`src/s3_glacier_restore/ui/_build_config.py` (gitignored); o valor em claro nunca
é gravado. Depois de logar, o painel admin permite trocar a senha; o novo digest
fica em `%LOCALAPPDATA%\s3-glacier-restore\admin-auth.json` (só neste computador;
apague o arquivo para voltar à senha do build). `S3_RESTORE_SIGNTOOL` +
`S3_RESTORE_CERT_SUBJ` habilitam a assinatura; sem isso, o SmartScreen pode
bloquear a 1ª execução.

### Painel admin (técnico)

Abra o exe → **Administrador** → senha (padrão `eajdias` ou a definida no build) →
origem e destino (`s3://bucket/prefixo/`, etiqueta opcional) → executar `auto`
(ou as etapas isoladas). O painel usa o perfil padrão do AWS CLI da máquina
(`default`), não pede credencial do cliente e **nunca baixa**. `auto` e `batch`
já entram com `--max-list 700000`.

### Painel cliente

1. **Cliente** → cole o bloco (valores abaixo são placeholders, não credenciais reais):

   ```text
   AWS_ACCESS_KEY_ID=<ACCESS_KEY_ID>
   AWS_SECRET_ACCESS_KEY=<SECRET_ACCESS_KEY>
   AWS_DEFAULT_REGION=sa-east-1
   AWS_DEFAULT_OUTPUT=json
   AWS_SESSION_TOKEN=<TOKEN_SESSAO>
   ```

   `AWS_SESSION_TOKEN` é opcional. O bloco fica apenas no `env` do processo filho e
   o campo é limpo ao iniciar o download; se a chave for rotacionada, cole o bloco
   novo — sem rebuild.

2. Informe o destino (`s3://bucket/prefixo/`) e a etiqueta, se o prefixo for
   genérico → **Validar** (lista pastas ao vivo).
3. Selecione tudo ou pastas específicas → **Continuar** (HEAD amostral: 5 por pasta).
4. Escolha a pasta local → **Verificar espaço** → **Iniciar download**. Pastas de mesmo nome
   são mescladas, arquivos de mesmo nome são sobrescritos e nada é apagado.

Trilha e relatório do download ficam em `%LOCALAPPDATA%\s3-glacier-restore\logs\`
e `reports\`; a porta da UI em `%LOCALAPPDATA%\s3-glacier-restore\ui.port`. Comandos
admin gravam em `logs\`/`reports\` relativos ao diretório de trabalho do exe,
salvo `--log-dir`/`--report-dir` no campo de argumentos extras.

## Modo automático (`auto` / `batch`)

Um comando faz a cadeia inteira e **para antes do download** (que é manual):

```bash
uv run python -m s3_glacier_restore auto --serial <SERIAL> --profile verificar

# Genérico (etiqueta inferida de `2024/`):
uv run python -m s3_glacier_restore auto \
  --src s3://origem-bkt/backups/2024/ --dst s3://destino-bkt/entrega/2024/ \
  --profile verificar
```

Sequência: `restore` → espera 4h → `status` repeat até 100% → `copy` em
`STANDARD` → `verify` → grava `handoff/<SERIAL>-download.txt` e
`state/<SERIAL>.done`. O `download` **nunca** é disparado.

O gate de `status` é obrigatório: o restore **não altera** a `StorageClass`,
então copiar sem confirmar o header `Restore` geraria um destino vazio.
Se quiser furar o gate (uso exploratório), `--force-copy`.

Checkpoint em `state/<SERIAL>-<profile>.json`: interrompeu? Rode o mesmo
comando de novo e ele retoma da fase gravada em `phase`
(`restore`/`wait`/`copy`/`verify`/`handoff`). Não existe `status=done` por
fase: `status` é do run inteiro (`done` encerra; `failed` exige `--force`).
`--dry-run` simula `restore`/`copy` (o gate de `status` continua lendo via
HEAD; `verify` e o handoff seguem reais). Ajustes de tempo: `--wait-seconds`,
`--poll-seconds`, `--max-polls`.

A retomada é do `auto`, não do projeto inteiro: se você rodou `restore`/`copy`
pelos comandos isolados, ele não herda esse progresso e começa em `restore`.
Para continuar sem repetir o restore, siga com as etapas isoladas ou ajuste
`phase` para `wait` no checkpoint antes de chamar `auto`.

`auto`/`batch` usam `--workers 20` e `--max-list 700000` por padrão (alinhados
a `AutoOptions`); `copy`, `restore` e `status` isolados usam 8 e 20000.

Vários clientes ao mesmo tempo (cada um com estado, log e handoff próprios):

```bash
uv run python -m s3_glacier_restore batch 1234-5678-90 1234-5678-91 1234-5678-92 \
  --profile verificar --max-parallel 3

# Pares genéricos (cada item carrega as próprias URIs):
uv run python -m s3_glacier_restore batch \
  "s3://origem-bkt/backups/2024=s3://destino-bkt/entrega/2024" \
  --profile verificar
```

Equivalente manual (após o `verify` liberar):

```bash
aws s3 sync s3://<DST_BUCKET>/<SERIAL>/ ./restored-<SERIAL>/ --region sa-east-1
```

## Audit trail (rastreabilidade)

Cada execução gera um `run_id` (`<serial>-<utc>-<8hex>`) e dois artefatos:

- `logs/<run_id>.jsonl` — um evento JSON por linha (ação, recurso, resultado,
  erro, duração). É a fonte de verdade auditável.
- `reports/<serial>-<run_id>.md` — resumo humano com a mesma informação,
  pronto para encaminhar.

Em `auto`/`batch`, a orquestração também grava seu próprio log (`auto:run`,
`auto:phase:*`) com `run_id` próprio, além dos logs de `restore`/`copy`/`verify`.

### Padrão do evento

```json
{"ts":"2026-11-24T15:00:00.123Z","level":"INFO","run_id":"...","serial":"...","profile":"verificar","account":"<ACCOUNT_ID>","actor":"arn:aws:iam::...:user/...","step":"copy","action":"s3:CopyObject","resource":"s3://bucket/key","result":"ok","error_code":null,"error_msg":null,"duration_ms":123,"extra":{"storage_class":"STANDARD"}}
```

Campos e regras:

| Campo | Regra |
|---|---|
| `ts` | ISO-8601 UTC, milissegundos, sufixo `Z` |
| `level` | `INFO` ok/skip, `WARN` recuperável, `ERROR` falha |
| `result` | `ok` \| `fail` \| `skip` \| `blocked` \| `dry_run` |
| `error_code` | código AWS (`AccessDenied`, `InvalidObjectState`...) ou `null` |
| `error_msg` | mensagem AWS exata, sem truncar o essencial |
| `resource` | `s3://bucket/key` em operações de objeto; eventos de run/auto/perms usam `serial:<SERIAL>`, ARN ou perfil |
| segredos | **proibido**: nunca access key, secret, token ou senha |

Console é pt-BR e resumido; o JSONL é inglês estruturado e completo.
`--dry-run` registra `result: dry_run` sem executar a mutação.

## Configuração

| Item | Valor |
|---|---|
| Região | `sa-east-1` (default alterável por `--region`) |
| Origem | `<SRC_BUCKET>` — `--src-bucket` ou `S3_RESTORE_SRC_BUCKET` |
| Destino | `<DST_BUCKET>` — `--dst-bucket` ou `S3_RESTORE_DST_BUCKET` |
| Classe do destino | `STANDARD` (única que o cliente baixa) |
| Lifecycle origem | `30d STANDARD_IA` → `90d GLACIER_IR` → `180d DEEP_ARCHIVE` |
| Lifecycle destino | `Delete-after-30d` (apaga em 30 dias) |

Credenciais vivem em `%USERPROFILE%\.aws\credentials` via `aws configure`.
O projeto recebe apenas `--profile`; nunca chave ou segredo.

## Documentação

- Runbook operacional: [`docs/RUNBOOK.md`](docs/RUNBOOK.md)
- Guia do agente: [`AGENTS.md`](AGENTS.md)
