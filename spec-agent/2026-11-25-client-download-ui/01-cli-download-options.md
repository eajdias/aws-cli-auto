# Task 01 — Opções de `download` para o cliente

## Escopo

Entra: `--profile ""|env` como "credencial somente por env", `--prefix` repetível e
`--skip-head` no subcomando `download`. Não entra: mudança de defaults (20000/8), mudança
em `auto`/`batch`, download automático, alteração de formato de log/relatório, HEAD amostral
(Task 02) ou UI (Task 04/05).

## Arquivos

- modificar: `src/s3_glacier_restore/aws_factory.py`, `src/s3_glacier_restore/cli.py`
- testar: `tests/test_cli.py`

## Interfaces

- `build_session(profile: str, region: str) -> boto3.Session`: `profile in ("", "env")`
  ⇒ `boto3.Session(profile_name=None, region_name=region)`; qualquer outro valor mantém
  `profile_name=profile`. Isso é o que faz o bloco colado prevalecer sobre `~/.aws/credentials`.
- `download --prefix <rel>`: normaliza `rel.strip("/")` e filtra chaves que começam com
  `cfg.dst_prefix + rel`; aceito `Images/` e `<SERIAL>/Images/` (ambos viram `<SERIAL>/Images/`).
- `download --skip-head`: pula `head_restore_state` por objeto, considera a chave baixável e
  grava `extra={"head_skipped": True}` no evento `s3:GetObject`. O backend só usa essa flag
  depois do HEAD amostral da Task 02 ter passado.

## Passos

- [x] Teste RED: monkeypatch `boto3.Session` e afirmar `profile_name is None` para `"env"` e `""`.
- [x] Teste RED: `build_parser().parse_args(["download", ..., "--prefix", "Images/", "--prefix", "Database/", "--skip-head"])`.
- [x] Teste RED: `cmd_download` com `--prefix Images/` e S3 falso só lista/baixa chaves do prefixo.
- [x] Teste RED: `--skip-head` não chama `head_restore_state`; `--dry-run` continua chamando.
- [x] Rodar `uv run pytest tests/test_cli.py -q` e colar o RED (5 falhas observadas; o plano previa 4).
- [x] Implementar o sentinel em `build_session`.
- [x] Implementar `--prefix`/`--skip-head` mantendo `--key` e o comportamento padrão intactos.
- [x] Rodar GREEN e a suíte completa.

## Critérios de aceite (Dado/Quando/Então)

- Dado `--profile env`, Quando `build_session` é chamado, Então nenhum profile é passado ao boto3.
- Dado `--prefix Images/`, Quando o censo lista 3 chaves em 2 prefixos, Então só as do prefixo escolhido vão para `download_file`.
- Dado `--skip-head`, Quando `cmd_download` roda, Então `head_restore_state` não é chamado e o log registra `head_skipped=True`.
- Dado nenhuma flag nova, Quando `cmd_download` roda, Então o comportamento é idêntico ao atual.

## Verificação

```bash
uv run pytest tests/test_cli.py -q            # RED primeiro, depois GREEN
uv run pytest                                 # 40 testes antigos + novos
uv run ruff check src tests
uv run ruff format --check src tests
uv lock --check
```

Evidência (2026-11-25):

- RED: `...........FFFFF` (5 falhas: `profile_name='env'`, flags não reconhecidas, filtro não aplicado, HEAD chamado com `--skip-head`, HEAD chamado em `--dry-run --skip-head`).
- RED adicional: `--prefix <SERIAL>/Images/` → `Filtro de prefixo: 1 -> 0 alvo(s)`; corrigido para reduzir o prefixo relativo ao serial.
- GREEN: `uv run pytest tests/test_cli.py -q` → `................. [100%]`.
- Suíte completa: `uv run pytest -q` → `................................................... [100%]` (51 testes).
- `uv run ruff check src tests` → `All checks passed!`; `uv run ruff format --check src tests` → `15 files already formatted`; `uv lock --check` → `Resolved 15 packages`.
- `uv run python -m s3_glacier_restore download --help` expõe `--prefix PREFIX` e `--skip-head`.
- `rg -l "AKIA[0-9A-Z]{16}" . --hidden ...` e `rg -n "zscan|Zscan|ZSCAN" . --hidden ...` sem saída (exit 1 = sem matches).

## Rollback

`git checkout HEAD -- src/s3_glacier_restore/aws_factory.py src/s3_glacier_restore/cli.py tests/test_cli.py`.
Sem efeito persistente: nenhum arquivo de estado é tocado.

## Resultado esperado

Suíte verde, `--prefix` e `--skip-head` aceitos pelo parser, defaults preservados, nenhuma
mudança em `auto`/`batch`.
