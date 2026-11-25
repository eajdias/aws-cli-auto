# Task 02 — Listagem de prefixos e HEAD amostral em runtime

## Escopo

Entra: módulo puro `prefixes.py`, descoberta de pastas de 1º nível via `ListObjectsV2`,
agregação de objetos/bytes por prefixo, amostragem de HEAD por prefixo selecionado e testes
com S3 falso. Não entra: download, restore, escrita em `state/`/`handoff/`, manifesto
embutido, chamada AWS real nos testes, registro dos comandos `__child` (Task 04).

Motivo: o exe é buildado uma vez; pastas e contagens vêm do bucket na hora do atendimento.
O HEAD amostral substitui a confiança num `verified_run_id` embutido.

## Arquivos

- criar: `src/s3_glacier_restore/prefixes.py`, `tests/test_prefixes.py`
- reutilizar: `s3ops.normalize_class`, `s3ops.head_restore_state`, `s3ops.is_downloadable`

## Interfaces

```python
@dataclass
class PrefixStats:
    prefix: str          # "Images/" relativo ao serial; "" = objetos diretos na raiz
    objects: int
    bytes: int

@dataclass
class PrefixListing:
    prefixes: list[PrefixStats]   # ordenados por prefixo
    objects: int                  # total já com o corte de max_list aplicado
    bytes_total: int
    elapsed_ms: int
    truncated: bool               # True se max_list foi atingido

def list_prefixes(s3: S3Like, bucket: str, serial: str, max_list: int = 700000) -> PrefixListing

@dataclass
class HeadSample:
    prefix: str
    key: str
    storage_class: str
    state: str                    # restored|restoring|none|error
    downloadable: bool

@dataclass
class HeadSampleResult:
    ok: bool
    samples: list[HeadSample]
    failures: list[dict[str, str]]   # {"prefix": ..., "key": ..., "reason": ...}

def sample_heads(
    s3: S3Like,
    bucket: str,
    serial: str,
    prefixes: list[str],
    sample_size: int = 5,
) -> HeadSampleResult
```

Regras:

- Descoberta: `client.get_paginator("list_objects_v2").paginate(Bucket=..., Prefix=f"{serial}/",
  Delimiter="/")` para obter os nomes em `CommonPrefixes`. É uma chamada barata (só pastas).
- Contagem: segunda paginação com o mesmo `Prefix` e **sem** `Delimiter`, agrupando cada key
  pelo 1º segmento depois do serial. É a única passada sobre ~669 mil objetos; sem HEAD por objeto.
- Marcadores de pasta (key termina em `/` ou `StorageClass` ausente) são excluídos de
  `objects`, `bytes_total` e `prefixes`, conforme a convenção já registrada em `AGENTS.md`.
- `max_list` limita o total de objetos contados; ao atingir, parar e marcar `truncated=True`.
  Nunca usar projeção JMESPath sobre listagem paginada.
- `sample_heads` lista no máximo `sample_size` chaves não-marcador por prefixo e faz HEAD nelas;
  usa `normalize_class` + `head_restore_state` + `is_downloadable`. Prefixo sem chave ou
  HEAD com erro/não-baixável entra em `failures` e vira `ok=False`.

## Passos

- [x] Teste RED: página falsa com 2 `CommonPrefixes`, 3 objetos + 1 marcador ⇒ prefixos, contagens e bytes corretos.
- [x] Teste RED: objeto direto na raiz do serial ⇒ `prefix=""`.
- [x] Teste RED: `max_list=2` com 3 objetos ⇒ `objects=2`, `truncated=True`.
- [x] Teste RED: `sample_heads` com 2 prefixos e 2 chaves cada (uma arquivada) ⇒ `ok=False`, `failures` aponta a chave.
- [x] Rodar `uv run pytest tests/test_prefixes.py -q` e colar o RED.
- [x] Implementar `list_prefixes` com paginador e agregação em memória.
- [x] Implementar `sample_heads` com limite por prefixo.
- [x] GREEN + gates.

## Critérios de aceite (Dado/Quando/Então)

- Dado bucket com marcadores de pasta, Quando `list_prefixes` roda, Então marcadores não entram em nenhuma contagem.
- Dado `max_list` atingido, Quando a listagem continua, Então a paginação para e `truncated` é `True`.
- Dado prefixo selecionado com 5 objetos `STANDARD`, Quando `sample_heads` roda, Então `ok=True` com 5 amostras.
- Dado um objeto `DEEP_ARCHIVE` sem restore na amostra, Quando `sample_heads` roda, Então `ok=False` e o download é bloqueado pelo backend.

## Verificação

```bash
uv run pytest tests/test_prefixes.py -q     # RED → GREEN
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

Evidência (2026-11-25):

- RED inicial: `ModuleNotFoundError: No module named 's3_glacier_restore.prefixes'`.
- RED intermediário: `..FFF` (3 falhas de normalização: `prefix='Images'` sem `/`) e depois `..FF.` (fixture com página única em vez de lista de páginas).
- GREEN: `uv run pytest tests/test_prefixes.py -q` → `..... [100%]`.
- Suíte completa: `uv run pytest -q` → `........................................................ [100%]` (56 testes).
- `uv run ruff check src tests` → `All checks passed!`; `uv run ruff format --check src tests` → `17 files already formatted`; `uv lock --check` → `Resolved 15 packages`.

## Rollback

Remover os 2 arquivos novos. Nenhum outro módulo os referencia até a Task 04.

## Resultado esperado

Funções puras, testadas sem AWS, que alimentam a listagem ao vivo e o gate de HEAD amostral
do modo cliente — sem manifesto, sem rebuild por cliente.
