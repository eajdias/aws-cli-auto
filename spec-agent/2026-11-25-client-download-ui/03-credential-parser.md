# Task 03 — Parser do bloco de credenciais (sem vazamento)

## Escopo

Entra: parse do bloco colado pelo técnico, validação, redação em `repr` e mensagens de erro,
construção do `env` do processo filho. Não entra: escrita em disco, persistência em
`~/.aws`, chamada AWS, log do conteúdo. Nenhum valor real de credencial é usado nos testes.
Para o teste de formato válido, construa o fake em runtime (`"AKIA" + "0" * 16`, `"0" * 40`) —
nunca escreva uma chave com formato real como literal no repositório.

## Arquivos

- criar: `src/s3_glacier_restore/credentials.py`, `tests/test_credentials.py`

## Interfaces

```python
@dataclass(repr=False)
class EnvCredentials:
    access_key_id: str
    secret_access_key: str
    region: str
    output: str
    session_token: str | None = None

    def __repr__(self) -> str: ...        # "[REDACTED] region=sa-east-1"
    def as_child_env(self) -> dict[str, str]: ...
    def masked_summary(self) -> str: ...  # "regiao=sa-east-1; credencial preenchida"

def parse_env_block(text: str) -> EnvCredentials: ...
class CredentialBlockError(ValueError): ...
```

`as_child_env()` devolve exatamente:

- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, `AWS_DEFAULT_OUTPUT`;
- `AWS_SESSION_TOKEN` quando houver token;
- `AWS_PROFILE=""` para impedir que um profile local sobrescreva as variáveis.

Formato aceito: 4 linhas obrigatórias `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_DEFAULT_REGION`, `AWS_DEFAULT_OUTPUT`; separador `:` ou `=`; CRLF/LF; espaços ao redor
removidos; `AWS_SESSION_TOKEN` opcional; chave desconhecida ⇒ erro.
`AWS_DEFAULT_OUTPUT` normalizado para minúsculas e limitado a
`json|text|table|yaml|yaml-stream`. Região validada com o padrão `^[a-z]{2}(-gov)?-[a-z]+-\d+$`.

O backend usa `as_child_env()` para montar o `env` dos comandos `__child`. A credencial
nunca entra em argv, arquivo, log, relatório ou exceção; só vive no env do filho e na
variável local que é descartada após o spawn.

## Passos

- [x] Teste RED: bloco com 4 linhas (valores falsos montados em runtime) ⇒ objeto preenchido.
- [x] Teste RED: CRLF + linhas vazias + `=` no lugar de `:` ⇒ aceito.
- [x] Teste RED: bloco com `AWS_SESSION_TOKEN` ⇒ `as_child_env()` contém a 5ª variável.
- [x] Teste RED: chave faltando, chave desconhecida, região inválida, output inválido, access key curta ⇒ `CredentialBlockError`.
- [x] Teste RED: `str(exc)`, `repr(cred)` e `masked_summary()` não contêm nenhum valor sensível.
- [x] Rodar RED e colar a saída.
- [x] Implementar parser e validações; mensagens de erro citam **só o nome da chave**.
- [x] GREEN + gates.

## Critérios de aceite (Dado/Quando/Então)

- Dado o bloco colado com `+` e `/` no secret, Quando é parseado, Então o valor é preservado integralmente.
- Dado erro de validação, Quando a mensagem é lida, Então nenhum valor aparece — só o nome da variável.
- Dado um objeto parseado, Quando é representado em log ou f-string, Então o resultado é `[REDACTED]`.
- Dado `as_child_env()`, Quando é usado no spawn, Então o valor não aparece em nenhum argv.

## Verificação

```bash
uv run pytest tests/test_credentials.py -q      # RED → GREEN
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
rg -l "AKIA[0-9A-Z]{16}" src tests
# -> vazio (o fake dos testes é montado em runtime, nunca literal)
```

Evidência (2026-11-25):

- RED: `ModuleNotFoundError: No module named 's3_glacier_restore.credentials'`.
- GREEN: `uv run pytest tests/test_credentials.py -q` → `......... [100%]`.
- Suíte completa: `uv run pytest -q` → `................................................................. [100%]` (65 testes).
- `uv run ruff check src tests` → `All checks passed!`; `uv run ruff format --check src tests` → `19 files already formatted`.
- `rg -l "AKIA[0-9A-Z]{16}" src tests` sem saída (exit 1).

## Rollback

Remover os 2 arquivos novos. Nenhum outro módulo depende deles até a Task 04.

## Resultado esperado

Parser auditável que transforma o bloco colado no `env` do filho, sem transformar credencial
em texto de log, exceção, relatório ou arquivo.
