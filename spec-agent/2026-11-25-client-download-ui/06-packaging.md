# Task 06 — Build do `.exe` único (senha do admin definida no build)

## Escopo

Entra: dependência de build, spec do PyInstaller, script de build que gera o hash da senha do
admin, embute o HTML e assina opcionalmente, além do smoke do executável. Não entra:
installer MSI, updater, multiplataforma, manifesto por cliente, `--serial`/bucket no build,
mudança no pipeline `auto`.

O build é **um**: o mesmo `.exe` atende todos os clientes. Serial, bucket e credenciais são
informados na UI em runtime.

## Arquivos

- criar: `scripts/build_client.py`, `scripts/exe_entry.py`, `s3-download-client.spec`,
  `tests/test_packaging.py`
- gerado no build (gitignored): `src/s3_glacier_restore/ui/_build_config.py`
- modificar: `pyproject.toml` (`[dependency-groups] build = ["pyinstaller>=6"]`), `.gitignore`
  (`build_assets/`, `build/`, `dist/`, `ui.port`, `_build_config.py`), `uv.lock`

## Interfaces

- `scripts/exe_entry.py`: `from s3_glacier_restore.ui.entry import main; raise SystemExit(main())`.
- `scripts/build_client.py --admin-password <SENHA>` (ou `S3_RESTORE_ADMIN_PASSWORD` no env):
  1. valida senha não vazia;
  2. escreve `_build_config.py` com `ADMIN_SALT` (hex aleatório) e
     `ADMIN_PASSWORD_SHA256 = sha256(salt_bytes + password.encode()).hexdigest()` —
     nunca grava a senha em claro;
  3. invoca PyInstaller com `s3-download-client.spec`: `Analysis` com `pathex=["src"]`,
     `datas` só com `ui/index.html` em `s3_glacier_restore/ui`, `EXE(..., name="s3-download-client",
     console=False, upx=False)`; `_build_config.py` entra via import dinâmico em `ui/auth.py` mais `hiddenimports` no spec;
  4. assina com `signtool.exe` se `S3_RESTORE_SIGNTOOL` + `S3_RESTORE_CERT_SUBJ` estiverem
     definidos; senão imprime aviso explícito de SmartScreen e segue;
  5. imprime `dist/s3-download-client.exe`.
- O exe ao iniciar sem comando chama `serve()` (Task 04), grava a porta em `ui.port` e abre o
  navegador; `__child` e subcomandos CLI conhecidos vão para `cli.main()`, com diretório
  existente mantido como appdata.

Nota PyInstaller (Context7): em spec, `datas` é lista de tuplas `(origem, destino)`; a CArchive
do onefile carrega tudo dentro do EXE e extrai em `runtime_tmpdir` no start. Evitar `--add-data`
na linha de comando (separador `;` no Windows).

## Passos

- [x] Teste RED: `build_client.py --help` **não** expõe `--serial`, `--bucket`, `--verified-run-id` nem `--manifest`.
- [x] Teste RED: `write_build_config(password, path)` gera hash + salt e não contém a senha em claro.
- [x] Teste RED: spec referencia apenas arquivos existentes e `datas` não contém `manifest.json`.
- [x] Rodar RED e colar a saída.
- [x] Adicionar grupo `build` e atualizar `uv.lock` com `uv lock`.
- [x] Implementar spec, entry e script de build.
- [x] GREEN + gates.
- [x] Rodar o build real com senha de teste e o smoke (evidência obrigatória do `done`).

## Critérios de aceite (Dado/Quando/Então)

- Dado o comando de build, Quando termina, Então existe um único `.exe` em `dist/` com Python, boto3, HTML e hash do admin — e nenhum manifesto.
- Dado o exe executado sem Python instalado, Quando ele sobe, Então `ui.port` é criado e `/api/health` responde 200 via `curl.exe`.
- Dado senha errada no painel admin, Quando `/api/admin/unlock` é chamado, Então `{ok:false}`.
- Dado serial/bucket diferentes, Quando o cliente preenche a UI, Então a listagem muda sem qualquer rebuild.
- Dado variáveis de assinatura ausentes, Quando o build roda, Então ele succeede com aviso explícito (não falha silenciosa).

## Verificação

```bash
uv run pytest tests/test_packaging.py -q
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv lock --check
uv run --group build python scripts/build_client.py --admin-password <SENHA_TESTE>
Get-Content "$env:LOCALAPPDATA\s3-glacier-restore\ui.port"
curl.exe http://127.0.0.1:<PORTA>/api/health
```

Smoke do unlock: usar a mesma senha de teste do build via variável temporária; não imprimir a
senha no log nem no relatório.

## Evidência (2026-11-25)

- RED: 6 falhas esperadas em `tests/test_credentials.py`, `tests/test_ui_runner.py` e
  `tests/test_packaging.py` (`AWS_PROFILE` presente, override herdado não removido, `upx=True`).
- GREEN: `uv run pytest` → `97 passed in 4.30s`; `uv run ruff check src tests scripts` →
  `All checks passed!`; `uv run ruff format --check src tests scripts` → `33 files already
  formatted`; `uv lock --check` → `Resolved 22 packages in 1ms`.
- Build: `build_ok=True exe=...dist\s3-download-client.exe`; log termina em `Build complete!` e
  `AVISO: exe nao assinado; o SmartScreen pode bloquear a primeira execucao.` — sem
  `DEPRECATION` de admin e sem traceback do UPX. O build foi executado com token médio
  (`schtasks /RL LIMITED`) porque o shell do agente é elevado; `runas /trustlevel` não reduziu
  o token nesta máquina.
- Smoke do exe: `health=True unlock_ok=True unlock_bad=True validate_json=True
  validate_status=400 validate_errors=InvalidClientTokenId: The security token included in the
  request is invalid. stderr_lines=0`. Credencial fake; valor real nunca impresso nem gravado.
- Causa raiz do `ProfileNotFound: The config profile () could not be found`: `as_child_env()`
  injetava `AWS_PROFILE=""`. O valor foi removido e `_child_env()` agora descarta também
  `AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_SESSION_TOKEN` (quando não vem do bloco)
  e overrides de web identity/container herdados do técnico.
- Correção na auditoria final: `entry.main()` passou a encaminhar `__child` e subcomandos CLI
  conhecidos para `cli.main()`, mantendo diretório existente como appdata. Sem isso, o painel
  admin chamava `auto`/`verify` e o exe abria outro servidor. Evidência no exe real:
  `admin_cli_routed=True exited=True stderr_lines=0`.

## Rollback

`git checkout HEAD -- pyproject.toml .gitignore uv.lock`; remover arquivos novos; apagar `dist/`,
`build/` e `_build_config.py` (gitignored, regeneráveis). Manter qualquer exe anterior até a entrega.

## Resultado esperado

Entregável de arquivo único, testado localmente, reutilizável para qualquer cliente sem rebuild
e sem serial/bucket/manifesto embutidos.
