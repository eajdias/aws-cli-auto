# Runbook - Restauracao de cliente via CLI

Fluxo: `s3://<SRC_BUCKET>/<PREFIXO>/` → `s3://<DST_BUCKET>/<PREFIXO>/`
(destino sempre `STANDARD`) → download no cliente.

Cada run é escopado a um par bucket+prefixo. O layout legado `<SERIAL>/`
(`1234-5678-90/`) continua válido, mas qualquer prefixo funciona: prefira
`--src s3://bucket/origem/ --dst s3://bucket/destino/` (a etiqueta é inferida
do último segmento; `--serial`/`--label` virou etiqueta opcional). Nos
exemplos abaixo, `<SERIAL>` pode ser lido como "etiqueta do run".

Ferramenta: `uv run python -m s3_glacier_restore` (este projeto). No CLI, credenciais vêm
do perfil AWS (`--profile`); segredos não são gravados em arquivo, log ou relatório.

**Caminho recomendado: `auto` (seção 10).** As seções 2–9 abaixo documentam
cada etapa isolada — use-as para diagnóstico, intervenção ou simulação.

Setup (uma vez):

```powershell
uv sync   # venv local .venv + uv.lock
$env:S3_RESTORE_SRC_BUCKET = "<SRC_BUCKET>"
$env:S3_RESTORE_DST_BUCKET = "<DST_BUCKET>"
```

Sem essas variáveis (ou as flags `--src-bucket`/`--dst-bucket`), qualquer comando
falha com `EXIT_BLOCKED` — nao existe bucket default no codigo.

## 0. Fixos (nao mexer sem testar)

| Item | Valor |
|---|---|
| Conta | `<ACCOUNT_ID>` |
| Regiao | `sa-east-1` |
| Origem | `<SRC_BUCKET>` |
| Destino | `<DST_BUCKET>` |
| Classe do destino | `STANDARD` |
| Lifecycle origem | `30d STANDARD_IA` → `90d GLACIER_IR` → `180d DEEP_ARCHIVE` |
| Lifecycle destino | `Delete-after-30d` (apaga em 30 dias) |

## 1. Credencial (uma vez por maquina)

```bash
aws configure --profile <nome>
# Region: sa-east-1 | Output: json
aws sts get-caller-identity --profile <nome>
```

## 2. Preflight (OBRIGATORIO, read-only)

```bash
uv run python -m s3_glacier_restore plan --serial <SERIAL> --profile <nome>
```

- `OK - pode copiar` → vá ao passo 4.
- `BLOQUEADO - restore necessario` → passo 3.
- `BLOQUEADO - prefixo vazio` → confira o serial.

## 3. Restore (quando o plan bloquear)

```bash
uv run python -m s3_glacier_restore restore --serial <SERIAL> --profile <nome> --dry-run
uv run python -m s3_glacier_restore restore --serial <SERIAL> --profile <nome>
# Prefixo grande (>100k objetos): suba o paralelismo
uv run python -m s3_glacier_restore restore --serial <SERIAL> --profile <nome> --workers 20 --max-list 700000
uv run python -m s3_glacier_restore status --serial <SERIAL> --profile <nome>   # repita
```

Deep Archive: ~12h (`--tier Standard`) ou ~48h (`--tier Bulk`).
Só siga quando `status` disser
`restaurados=N em_andamento=0 nao_solicitados=0 falhas=0 de N` com `N > 0`.
`restaurados=N em_andamento=0` sozinho não basta: `nao_solicitados` ou
`falhas` também bloqueiam o `copy` (mesmo gate do `auto`, seção 10).

## 4. Copiar para o public

```bash
uv run python -m s3_glacier_restore copy --serial <SERIAL> --profile <nome> --dry-run
uv run python -m s3_glacier_restore copy --serial <SERIAL> --profile <nome> --workers 20 --max-list 700000
```

Objetos arquivados **não** são pulados só pela classe: o `copy` faz `HeadObject`
e copia se o header `Restore` disser `ongoing-request="false"`. Sem restore
concluído eles viram `skip` (auditado). O restore **não muda** a `StorageClass`,
então checar só a classe — como era feito antes — copiava zero arquivos.
Marcadores de pasta também são pulados.

## 5. Verificar antes de entregar (gate)

```bash
uv run python -m s3_glacier_restore verify --serial <SERIAL> --profile <nome>
```

- `OK - destino liberado` → entregue o comando abaixo.
- `NAO ENVIAR` → ainda há arquivado no destino; copie de novo como STANDARD.
- `BLOQUEADO - destino vazio` → a cópia não produziu objetos; revise `copy`/`status`.
- `INCONCLUSIVO` → o censo foi cortado pelo `--max-list`; rode com valor maior.

## 5b. Conferir paridade origem x destino

```bash
uv run python -m s3_glacier_restore parity --serial <SERIAL> --profile <nome> --max-list 700000
# ou genérico:
uv run python -m s3_glacier_restore parity \
  --src s3://origem-bkt/prefixo/ --dst s3://destino-bkt/prefixo/ --profile <nome>
```

Compara os dois prefixos chave a chave (só leitura): ausentes no destino,
extras, tamanhos divergentes, idade do ausente mais velho/novo e amostra HEAD
de 20 ausentes na origem (`nunca solicitados` × `em restauracao` ×
`restaurados`). `OK` libera; `DIVERGENTE` manda rodar restore/status/copy de
novo; `INCONCLUSIVO` pede `--max-list` maior. O cartão `parity` no painel admin
faz o mesmo com um clique.

## 6. Baixar localmente (tudo ou específico)

```bash
# Tudo sob o serial (prefixo grande exige --max-list):
uv run python -m s3_glacier_restore download --serial <SERIAL> --profile <nome> --dest ./restored-<SERIAL> --max-list 700000

# Só chaves específicas (repita --key):
uv run python -m s3_glacier_restore download --serial <SERIAL> --profile <nome> --dest ./caso --key <SERIAL>/Images/foto.png --key <SERIAL>/Database/dump.sql

# Simular antes (não baixa nada):
uv run python -m s3_glacier_restore download --serial <SERIAL> --profile <nome> --dest ./x --dry-run --max-list 700000
```

Sem `--max-list`, o `download` isolado para em 20000 objetos — insuficiente para
um serial com centenas de milhares de chaves. O comando em
`handoff/<SERIAL>-download.txt` já inclui o `--max-list` do `auto`.

O `download` espelha a estrutura do prefixo, pula arquivados (`skip`) e registra
o tamanho do arquivo baixado em `extra.bytes` no JSONL — não há comparação com
`Content-Length`. Exit `2` = nada baixado (tudo arquivado ou inexistente).

Equivalente manual:

```bash
aws s3 sync s3://<DST_BUCKET>/<SERIAL>/ ./restored-<SERIAL>/ --region sa-east-1
```

Avise: o destino **apaga em 30 dias**.

## 7. Auditoria

Cada run gera:

- `logs/<run_id>.jsonl` — trilha auditável (ação, recurso, resultado, erro,
  duração; sem segredos).
- `reports/<serial>-<run_id>.md` — resumo pronto para encaminhar.

`--dry-run` registra `result: dry_run` sem executar a mutação.

## 8. Erros comuns

| Erro | Causa | Solução |
|---|---|---|
| `Storage class does not support synchronous retrieval` | objeto em DEEP_ARCHIVE/GLACIER | `restore` + aguardar |
| `InvalidObjectState` no download | destino arquivado | copiar de novo com `--storage-class STANDARD` |
| `AccessDenied` em `RestoreObject`/`PutObject` | falta Allow na identity | anexar policy (ver `perms`) |
| `Deny` explícito em `RestoreObject` | Deny na policy | remover o Deny antes de qualquer Allow |
| Prefixo vazio | serial errado | confira `1234-5678-90/` (uma barra) |

## 9. Auditar a credencial

```bash
uv run python -m s3_glacier_restore perms --serial <SERIAL> --profile <nome>
```

Matriz real via API (identidade + bucket policy): `ListBucket`, `GetObject`
(probe via `HeadObject`), `RestoreObject` (probe), `PutObject` + `DeleteObject`
(probe gravado e removido).

## 10. Auto (cadeia completa, retomavel) — USE ESTE

```bash
# Um cliente: faz restore -> espera -> status ate 100% -> copy -> verify -> handoff
uv run python -m s3_glacier_restore auto --serial <SERIAL> --profile <nome> \
  --max-list 700000 --workers 20

# Varios clientes em paralelo (estado/log/handoff separados por serial)
uv run python -m s3_glacier_restore batch <SERIAL1> <SERIAL2> <SERIAL3> \
  --profile <nome> --max-list 700000 --workers 20 --max-parallel 3
```

O `download` **nunca** é executado. Ao terminar:

- `state/<SERIAL>.done` — sinal para observadores externos;
- `state/<SERIAL>-<profile>.json` — checkpoint (`phase`, `status`, `run_ids`,
  resumo do último `status` em `last_status`);
- `handoff/<SERIAL>-download.txt` — comando pronto para o operador.

Retomada: rode o mesmo comando após uma interrupção; a retomada usa o campo
`phase` do checkpoint (`restore`/`wait`/`copy`/`verify`/`handoff`), não um
`status=done` por fase. `status=done` encerra sem fazer nada; `status=failed`
exige `--force` para recomeçar. Se o `restore` foi feito pelos comandos
isolados (seção 3), o `auto` não herda isso e começa em `restore`: continue
pelo fluxo isolado ou ajuste `phase` para `wait` no checkpoint antes de
chamar `auto`.

Gate de seguranca: o `copy` só roda quando `status` disser
`restaurados=N em_andamento=0 nao_solicitados=0 falhas=0 de N` com `N > 0`.
Isso é obrigatório porque o restore não muda a `StorageClass` — sem o gate, o
`copy` skipping todos os arquivados produziria um bucket vazio. `--force-copy`
existe apenas para diagnóstico e não é recomendado em entrega.

Ajustes de janela: `--wait-seconds` (default 14400 = 4h),
`--poll-seconds` (3600 = 1h), `--max-polls` (24). `auto`/`batch` já vêm com
`--workers 20` e `--max-list 700000` (alinhados a `AutoOptions`); os comandos
isolados usam 8 e 20000. Prefixos maiores que 700000 exigem `--max-list`
explícito.

`--dry-run` cobre `restore` e `copy`; o gate de `status` continua fazendo HEAD
e, se o restore não estiver completo, o run falha antes do `copy` — mesmo com
`--wait-seconds 0 --poll-seconds 0 --max-polls 1`. `verify` e a escrita do
handoff não são simulados.

Falha em qualquer fase marca o checkpoint como `failed` e **não** dispara as
seguintes — o handoff só existe se `verify` tiver passado.

## 11. Entrega com o exe único (admin + cliente)

O mesmo `dist/s3-download-client.exe` serve para todos os clientes. Origem,
destino (URIs `s3://bucket/prefixo/`) e etiqueta são de runtime: **não**
existem manifesto por cliente nem rebuild por prefixo.

### 11.1 Build uma vez (técnico)

```bash
# senha padrão do build: eajdias
uv run --group build python scripts/build_client.py

# outra senha:
uv run --group build python scripts/build_client.py --admin-password <SENHA_DO_ADMIN>
# ou: S3_RESTORE_ADMIN_PASSWORD=<SENHA_DO_ADMIN> uv run --group build python scripts/build_client.py
```

Sem `--admin-password`/`S3_RESTORE_ADMIN_PASSWORD`, o build usa a senha padrão
`eajdias`. O script grava só `ADMIN_SALT` + `ADMIN_PASSWORD_SHA256` em
`src/s3_glacier_restore/ui/_build_config.py` (gitignored), embute `ui/index.html`
e gera `dist/s3-download-client.exe`. Se `S3_RESTORE_SIGNTOOL` e
`S3_RESTORE_CERT_SUBJ` estiverem definidos, assina; senão avisa sobre o
SmartScreen. Nunca passe URIs, etiqueta ou credencial de cliente no build.

### 11.2 Painel admin (técnico, não baixa nada)

1. Abrir o exe → **Administrador** → senha definida no build (padrão `eajdias`).
2. Preencher origem e destino (`s3://bucket/prefixo/`) e, se o prefixo for
   genérico, a etiqueta (quando o prefixo termina no padrão `NNNN-NNNN-NN`,
   ela é inferida sozinha).
3. Executar `auto` (cadeia completa) ou `status` → `copy` → `verify` pelos cartões
   individuais. `restore`/`plan`/`perms` também estão disponíveis.
4. Usar o campo "argumentos extras" para ajustes como `--workers 20`,
   `--log-dir`, `--report-dir` ou `--dry-run`.
5. Para trocar a senha neste computador, usar **Senha administrativa** → nova
   senha → **Alterar senha**. O digest fica em
   `%LOCALAPPDATA%\s3-glacier-restore\admin-auth.json`; apague o arquivo para
   voltar à senha do build.

O painel admin usa o **perfil padrão do AWS CLI local** (`default`) e herda o
ambiente da máquina; não pede o bloco do cliente e não executa `download`.
`auto`/`batch` já usam `--max-list 700000` e `--workers 20`; as etapas isoladas
só recebem o `--max-list` se a opção "max-list 700000" for marcada. Trilhas e
relatórios ficam em `logs\`/`reports\` relativos ao diretório de trabalho do exe,
a menos que os diretórios sejam passados nos argumentos extras.

Se o comando parecer não executar, confira os argumentos extras: o exe encaminha
`auto`, `status`, `copy` etc. direto à CLI (`__child` é uso interno).

### 11.3 Painel cliente (só download)

1. Abrir o exe → **Cliente** → colar o bloco com placeholders:

   ```text
   AWS_ACCESS_KEY_ID=<ACCESS_KEY_ID>
   AWS_SECRET_ACCESS_KEY=<SECRET_ACCESS_KEY>
   AWS_DEFAULT_REGION=sa-east-1
   AWS_DEFAULT_OUTPUT=json
   AWS_SESSION_TOKEN=<TOKEN_SESSAO>
   ```

   O token é opcional. O bloco vai apenas para o `env` do processo filho; nada
   é gravado em arquivo, log ou relatório, e o campo é limpo ao iniciar o download.

2. Preencher **destino** (`s3://bucket/prefixo/`) e **etiqueta** (opcional,
   inferida do prefixo) → **Validar**. A lista de pastas
   vem de `ListObjectsV2` com delimitador `/`, direto do S3.
3. Selecionar **tudo** ou pastas específicas → **Continuar**. Antes de liberar o
   download, o exe faz HEAD amostral de 5 objetos por pasta selecionada.
4. Para testar um arquivo antes de liberar o restante, escolha uma pasta pequena
   (de preferência com 1 arquivo) e conclua o download; depois repita com o resto.
5. Escolher a pasta local → **Verificar espaço** → **Iniciar download**. A UI exige 10% de
   margem sobre o somatório das pastas selecionadas. Mesclagem: pastas iguais são
   mescladas, arquivos iguais sobrescritos, nada é apagado.

Trilha e relatório do download ficam em `%LOCALAPPDATA%\s3-glacier-restore\logs\`
e `reports\`. A porta da UI está em `%LOCALAPPDATA%\s3-glacier-restore\ui.port`.

### 11.4 Cancelamento e retomada

O botão **Cancelar** termina o processo filho; o progresso parcial permanece no
disco. Para retomar, abra o exe de novo, cole o bloco, use o mesmo destino/etiqueta,
reselecione as mesmas pastas e destino e rode o download outra vez. Não há
manifesto nem retomada por arquivo: as chaves já baixadas são sobrescritas.

O destino apaga em 30 dias. Deep Archive tem retenção mínima de 180 dias na
origem — nunca apague o original antes da cópia e da verificação.
