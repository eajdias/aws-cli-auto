# Task 04 — Servidor local, dois modos, comandos `__child`, endpoints e subprocessos

## Escopo

Entra: servidor em `127.0.0.1`, tranca do admin, roteamento dos dois modos, comandos internos
`__child`, runner de subprocessos, seletor de pasta nativo, preflight de espaço e testes.
Não entra: HTML/CSS/JS (Task 05), PyInstaller (Task 06), chamadas AWS reais nos testes.

O ponto central: **credencial do cliente só no `env` do filho**. O servidor lê o bloco,
monta o `env`, spawna e descarta a referência local; nada disso vai para argv/log/disco.

## Arquivos

- criar: `src/s3_glacier_restore/ui/__init__.py`, `ui/entry.py`, `ui/child.py`,
  `ui/server.py`, `ui/runner.py`, `ui/picker.py`, `ui/auth.py`, `ui/resources.py`,
  `tests/test_ui_server.py`, `tests/test_ui_runner.py`
- modificar: `src/s3_glacier_restore/cli.py` (interceptar `__child` no `main()` antes do parser público)

## Interfaces

- `serve(appdata_dir: Path | None = None, port: int = 8765) -> int`: escolhe a primeira porta
  livre entre 8765–8775, grava só o número em `%LOCALAPPDATA%\s3-glacier-restore\ui.port`,
  abre o navegador e bloqueia em `serve_forever()`.
- `resource_path(name: str) -> Path`: `sys._MEIPASS` quando frozen, senão o dir do módulo.
- `auth.password_digest(password: str, salt: str) -> str` e
  `auth.verify_admin_password(password: str) -> bool`: compara SHA-256 com
  `ADMIN_PASSWORD_SHA256`/`ADMIN_SALT` do módulo gerado no build
  (`s3_glacier_restore._build_config`, gitignored) ou, em dev, das variáveis
  `S3_RESTORE_ADMIN_PASSWORD_SHA256`/`S3_RESTORE_ADMIN_SALT`. Sem ambos ⇒ `False`.
- `runner.validate(block, bucket, serial) -> dict`,
  `runner.list_prefixes(block, bucket, serial) -> dict`,
  `runner.sample_heads(block, bucket, serial, prefixes, sample_size=5) -> dict`:
  chamam `parse_env_block`, montam `env = os.environ | creds.as_child_env()`, spawnam
  `<exe> __child <cmd> ...` com `stdout=PIPE`, leem a última linha JSON e descartam `creds`.
- `runner.start_download(block, bucket, serial, region, prefixes, dest, max_list=700000) -> str`:
  spawna `<exe> __child download --serial S --src-bucket B --dst-bucket B --region R
  --profile env --prefix P ... --skip-head --max-list 700000 --dest D --log-dir L --report-dir E`.
  O `--src-bucket` recebe o mesmo bucket apenas para satisfazer a validação de config;
  `cmd_download` não faz chamada à origem. Captura `run_id` da linha `Trilha: logs/<id>.jsonl`.
- `runner.admin_run(command, args[]) -> str`: allowlist
  `{"perms","plan","restore","status","copy","verify","auto","batch"}`; recusa
  `download` e `__child`; `shell=False`; herda o env local (AWS CLI do técnico).
- `runner.progress(run_id) -> dict` e `runner.cancel(run_id) -> bool`: leem o stdout
  acumulado (últimas 200 linhas) e extraem
  `DOWNLOAD: baixados=(\d+) pulados=(\d+) falhas=(\d+)`; `exit_code` preenchido ao fim.
- `picker.pick_folder() -> str | None`: `tkinter.filedialog.askdirectory()` com root oculto.

### Comandos `__child` (não aparecem no `--help`)

`cli.main()` intercepta `argv[0] == "__child"` e chama `ui.child.main(argv[1:])`.
Cada comando imprime **uma linha JSON** no stdout e sai com 0/2:

| Comando | Saída |
| --- | --- |
| `validate --bucket B --serial S` | `{ok, region, arn}` / `{ok:false, errors}` |
| `list-prefixes --bucket B --serial S --max-list 700000` | `PrefixListing` da Task 02 |
| `sample-heads --bucket B --serial S --prefix P --sample-size 5 ...` | `HeadSampleResult` da Task 02 |
| `download ...` | stdout normal do `cmd_download` (progresso) |

Todos usam `build_session("env", region)` ⇒ `profile_name=None`; a sessão pega as variáveis
do próprio ambiente do filho. Nenhum comando `__child` recebe credencial em argumento.

### Endpoints

| Método | Path | Contrato |
| --- | --- | --- |
| GET | `/`, `/styles.css`, `/app.js` | arquivos embutidos, `Cache-Control: no-store` |
| GET | `/api/health` | `{ok:true}` |
| POST | `/api/admin/unlock` | `{password}` ⇒ `{ok}` ou `{ok:false}` (senha não é logada) |
| POST | `/api/admin/run` | `{command, args[]}` ⇒ `{ok, run_id}`; allowlist sem `download`/`__child` |
| POST | `/api/validate-credentials` | `{block, bucket, serial}` ⇒ `{ok, region, arn}` |
| POST | `/api/list-prefixes` | `{block, bucket, serial}` ⇒ `PrefixListing` |
| POST | `/api/sample-heads` | `{block, bucket, serial, prefixes[], sample_size}` ⇒ `HeadSampleResult` |
| POST | `/api/check-destination` | `{path, required_bytes}` ⇒ `{exists, free_bytes, ok}` |
| POST | `/api/pick-folder` | `{}` ⇒ `{path}` ou `{path:null}` |
| POST | `/api/download` | `{block, bucket, serial, region, prefixes[], dest}` ⇒ `{ok, run_id}` |
| GET | `/api/progress` | `?run_id=` ⇒ `{status, lines[], downloaded, skipped, failed, exit_code}` |
| POST | `/api/cancel` | `{run_id}` ⇒ `{ok}` |

### Regras do servidor

- bind **somente** `127.0.0.1`; `Host` fora de `127.0.0.1[:porta]`/`localhost[:porta]` ⇒ 403.
- `Cache-Control: no-store`,
  `Content-Security-Policy: default-src 'self'; style-src 'unsafe-inline' 'self'; script-src 'unsafe-inline' 'self'`,
  sem CORS.
- Nenhum middleware de log de request body; logs só com metadados não-secretos.
- `block`/`creds` não são gravados, não entram em exceção e são limpos após o spawn.
- Preflight: `shutil.disk_usage(dest).free >= required_bytes * 1.10`, senão 409 **antes** de spawnar.
- `logs/` e `reports/` ficam em `%LOCALAPPDATA%\s3-glacier-restore\`.

## Passos

- [x] Teste RED: GET `/api/health` com `ThreadingHTTPServer` efêmero ⇒ 200.
- [x] Teste RED: `Host: evil.example` ⇒ 403.
- [x] Teste RED: `/api/admin/unlock` com senha errada ⇒ `{ok:false}`; com hash de teste ⇒ `{ok:true}`.
- [x] Teste RED: `/api/admin/run` com `command="download"` ⇒ 400 e nenhum spawn.
- [x] Teste RED: `validate-credentials` com bloco inválido ⇒ erro sem valor sensível.
- [x] Teste RED: `download` com espaço insuficiente ⇒ 409 e nenhum filho spawnado.
- [x] Teste RED: runner monta argv sem credencial e env com as 4/5 variáveis; `progress` parseia `DOWNLOAD: baixados=...`.
- [x] Rodar RED e colar a saída.
- [x] Implementar `resources.py`, `auth.py`, `picker.py`, `child.py`, `runner.py`, `server.py`, `entry.py`.
- [x] Interceptar `__child` em `cli.main()`.
- [x] GREEN + gates.

## Critérios de aceite (Dado/Quando/Então)

- Dado bloco válido e pasta válida, Quando `POST /api/download` é chamado, Então o filho nasce com `AWS_*` no env e nada na linha de comando.
- Dado `Host` estranho, Quando qualquer requisição chega, Então 403 sem processar o body.
- Dado espaço livre menor que 110% do total listado, Quando o download é pedido, Então 409 com mensagem em pt-BR.
- Dado `command="download"` no painel admin, Quando a requisição chega, Então 400 sem spawn.
- Dado linhas de stdout do `cmd_download`, Quando `/api/progress` é consultado, Então contadores e `exit_code` refletem o fim.

## Verificação

```bash
uv run pytest tests/test_ui_runner.py tests/test_ui_server.py -q   # RED → GREEN
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

Evidência (2026-11-25):

- RED: `ModuleNotFoundError: No module named 's3_glacier_restore.ui'` nos dois arquivos de teste.
- RED intermediário: `..F..F.F.....` → 3 falhas (`audit_run_id` com `.jsonl`, sombra de `runner`
  no teste de host, `cast(subprocess.Popen[str], ...)` avaliado com `Popen` monkeypatchado).
- GREEN: `uv run pytest tests/test_ui_runner.py tests/test_ui_server.py -q` → `............. [100%]`.
- Suíte completa: `uv run pytest -q` → `..................................................................... [100%]`
  (78 testes, executada 3x sem flakiness).
- `uv run ruff check src tests` → `All checks passed!`; `uv run ruff format --check src tests` →
  `29 files already formatted`; `uv lock --check` → `Resolved 15 packages`.
- `rg -l "AKIA[0-9A-Z]{16}" src tests` sem saída (exit 1).
- `--help` público não lista `__child`; `python -m s3_glacier_restore __child --help` mostra apenas
  os 4 comandos internos. `_setup` honra `run_id` explícito (teste em `tests/test_cli.py`).

## Rollback

`git checkout HEAD -- src/s3_glacier_restore/cli.py`; remover os arquivos novos de `ui/` e os
2 testes. Sem efeito persistente fora do appdata criado pelo smoke.

## Resultado esperado

Backend testável sem AWS, com dois modos separados, credencial confinada ao env do filho e
nenhum vazamento por log/argv/relatório.
