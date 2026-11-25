# URI S3 genérica + etiqueta inferida

## Problema

O `serial` (`NNNN-NNNN-NN`) estava hardcoded como prefixo em `config.py`
(`src_prefix = f"{serial}/"`), `prefixes.py`, `child.py`, UI e docs. Buckets de
outros projetos com layout diferente (`s3://bkt/backups/2024/`) não passavam
na validação — o projeto só servia ao layout de um cliente.

## Decisão

- Escopo do run = par bucket+prefixo (`s3://bucket/prefixo/`), não serial.
- `serial` vira **etiqueta** do run (run_id, logs, relatórios, state):
  explícita via `--serial`/`--label`, ou inferida do último segmento do
  prefixo (ex.: `.../1234-5678-90/` → `1234-5678-90`, `.../2024/` → `2024`).
- CLI aceita `--src/--dst` (URIs); legado `--serial + buckets` preservado.
- `batch` aceita etiquetas ou pares `SRC=DST` por item; etiquetas simples
  escopam a `<prefixo-base><etiqueta>/`.
- UI admin: Origem + Destino (URIs) + Etiqueta opcional. UI cliente: Destino
  (URI) + Etiqueta opcional. Protocolo `__child` usa `--bucket/--root/--label`.
- Gate inalterado: `restaurados=N em_andamento=0 nao_solicitados=0 falhas=0`,
  `N > 0`, destino `STANDARD`. Credenciais do cliente só no `env` do filho.

## Arquivos

- novo: `src/s3_glacier_restore/locations.py` (parse, `normalize_root`,
  `infer_label`, `LABEL_RE`), `tests/test_locations.py`
- alterados: `config.py`, `cli.py`, `auto.py`, `prefixes.py`,
  `ui/{child,runner,server}.py`, `ui/index.html`, testes e docs

## Verificação

- `uv run pytest` verde; `ruff check`, `ruff format --check`,
  `uv lock --check` e `git diff --check` verdes.
- Smoke do exe: `health`, `unlock` certo/errado, marcadores do tema,
  zero recurso externo, zero segredo no HTML.
