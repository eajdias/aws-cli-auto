# Task 05 — UI no navegador (admin, cliente, seleção, espaço, progresso)

## Escopo

Entra: um `index.html` único com CSS e JS inline, tela de escolha de modo, painel admin e
fluxo cliente completo, seleção por prefixo vindo do bucket, checagem de espaço, escolha de
pasta, progresso por polling e estados de erro. Não entra: SDK AWS no navegador, CDN,
framework, build de JS, download automático, rebuild por cliente.

## Arquivos

- criar: `src/s3_glacier_restore/ui/index.html`, `tests/test_ui_frontend.py`

## Interfaces

Consome os endpoints da Task 04. Constantes que os testes leem:

```js
const ADMIN_COMMANDS = ["perms", "plan", "restore", "status", "copy", "verify", "auto", "batch"];
const CLIENT_STEPS = ["credentials", "selection", "destination", "progress", "result"];
```

Telas (`<section>` com `hidden` alternado):

0. **Modo** — dois cartões: `Administração (técnico)` e `Cliente (download)`.
1. **Admin / senha** — `<input type="password">`, botão `Entrar` chama `/api/admin/unlock`.
   Senha errada ⇒ erro inline; senha correta ⇒ painel.
2. **Admin / painel** — um cartão por comando de `ADMIN_COMMANDS`, com campos adequados
   (`serial`, buckets, `--max-list 700000` explícito em `auto`/`batch`, `dry-run` quando
   fizer sentido). Botão `Executar` chama `/api/admin/run`; progresso compartilhado.
3. **Cliente / credenciais** — `<textarea>` para o bloco de 4/5 linhas, `autocomplete="off"`,
   `spellcheck="false"`; campos `Bucket destino` e `Serial`; botão `Validar e listar` chama
   `/api/validate-credentials` e, se ok, `/api/list-prefixes`.
4. **Cliente / seleção** — tabela com checkbox por prefixo (`objects`, `bytes`),
   `Selecionar todos`, `Limpar`, total selecionado, aviso "objetos expiram em 30 dias",
   sinalizador `truncated` quando a listagem foi cortada. `Continuar` chama `/api/sample-heads`;
   qualquer falha bloqueia o download e mostra o prefixo/chave afetado.
5. **Cliente / destino** — campo `Destino` + botão `Selecionar pasta` (`/api/pick-folder`),
   `/api/check-destination` com o total selecionado e margem de 10%; mostra espaço livre e
   aviso se insuficiente. `Baixar` chama `/api/download`.
6. **Progresso** — `role="progressbar"` com `aria-valuenow`, linhas do stdout, contadores
   baixados/pulados/falhas, botão `Cancelar`.
7. **Resultado** — veredito, caminho final, link para `logs/` e `reports/` exibidos pelo backend.

Mesclagem: texto fixo na tela de destino — pastas do S3 com o mesmo nome são mescladas;
arquivos de mesmo nome são sobrescritos; nada é apagado.

## Passos

- [x] Teste RED (estático): HTML não contém `http://`, `https://`, `cdn`, `<script src=` externo nem `<link>` remoto.
- [x] Teste RED: `ADMIN_COMMANDS` não contém `download` nem `__child`; `CLIENT_STEPS` contém as 5 etapas.
- [x] Teste RED: cada input tem `<label>` associado; botões têm texto acessível; `progressbar` existe.
- [x] Teste RED: JS limpa o `<textarea>` logo após `/api/download` responder `ok`.
- [x] Rodar RED e colar a saída.
- [x] Implementar HTML/CSS/JS com custom properties, contraste ≥ 4.5:1, foco visível,
      `prefers-reduced-motion`, alvos ≥ 24×24 px e ordem de tabulação igual à visual.
- [x] Implementar polling de progresso com recuo (1s → 3s) e parada em `exit_code != null`.
- [x] GREEN + gates + smoke visual.

## Critérios de aceite (Dado/Quando/Então)

- Dado o exe aberto, Quando a tela inicial aparece, Então há exatamente dois modos e nenhum modo mostra ações do outro.
- Dado `list-prefixes` com N prefixos, Quando a tela 4 abre, Então N checkboxes aparecem com contagens e tamanhos.
- Dado nenhum prefixo selecionado ou pasta vazia, Quando `Baixar` é clicado, Então nada é enviado e há mensagem inline associada por `aria-describedby`.
- Dado `sample-heads` com falha, Quando a resposta chega, Então o download é bloqueado e a chave falha aparece na tela.
- Dado download iniciado, Quando o texto da credencial ainda está no DOM, Então ele é limpo imediatamente após a resposta.
- Dado `exit_code=0`, Quando o polling termina, Então a tela 7 mostra o veredito e o destino.

## Verificação

```bash
uv run pytest tests/test_ui_frontend.py -q   # RED → GREEN
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

Smoke visual: subir o servidor da Task 04 em modo dev e usar o MCP `chrome-devtools`
(`navigate_page` → `take_snapshot` → `fill_form` → `click` → `list_console_messages`).
**Não usar `pw`/playwright-cli** — quebrado nesta máquina (browser chrome-for-testing ausente).

Evidência (2026-11-25):

- RED: `uv run pytest tests/test_ui_frontend.py -q` ⇒ `FFFFFFF` (arquivo `index.html` ausente).
- GREEN: `.......` (7 testes estáticos).
- Smoke `chrome-devtools` com `fetch` mockado: tela inicial com 2 modos; Cliente → Validar e listar
  ⇒ `Images/` (2 objetos, 2.0 KB) e `Database/` (1 objeto, 4.0 KB); Selecionar todos →
  Continuar → destino → Baixar ⇒ `OK — 3 arquivo(s) baixado(s).` com destino e trilha.
- Smoke admin: Entrar ⇒ painel com 8 comandos (sem `download`); `auto` enviou
  `["--serial","","--max-list","700000"]` e voltou ao painel com `Comando concluído.`.
- Pós-download: `credentials-block.value === ""` e `state.block === ""`; chamadas registradas
  foram `validate-credentials`, `list-prefixes`, `sample-heads`, `check-destination`,
  `download`, 2× `progress`, sem credencial em argv/URL.
- Console: 1 aviso inicial (`password field is not contained in a form`) corrigido com `<form>`;
  `list_console_messages` final ⇒ `<no console messages found>`.
- Correção de servidor durante o smoke: `do_GET` passou a servir `resource_path(name)` em vez de
  `resource_path("static") / name`; `protocol_version = "HTTP/1.1"` eliminou RST intermitente
  (WinError 10053) no teste de Host forjado — 20/20 execuções isoladas verdes.
- Suíte: `uv run pytest -q` ⇒ `.... [100%]` (87 testes) em 5 execuções consecutivas.
- `uv run ruff check src tests` ⇒ `All checks passed!`; `uv run ruff format --check src tests` ⇒
  `30 files already formatted`; `uv lock --check` ⇒ `Resolved 15 packages`.
- `rg -l "AKIA[0-9A-Z]{16}" . --hidden --glob '!*.pyc' --glob '!.git/**' --glob '!spec-agent/**'`
  e `rg -n "zscan|Zscan|ZSCAN" ...` sem saída (exit 1).

## Rollback

Remover os 2 arquivos novos; nenhum outro módulo os referencia além do servidor da Task 04.

## Resultado esperado

UI apresentável, sem dependência externa, com admin e cliente bem separados e utilizável por
técnico sem terminal — sem exigir rebuild quando muda o cliente.
