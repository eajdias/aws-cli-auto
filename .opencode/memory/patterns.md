# Agent Memory — Patterns (o que funciona)

> Padrões reutilizáveis e preferências do usuário que já funcionaram.
> Formato: ✅ Quando <situação>, faça <o que funciona>. Entradas curtas e acionáveis.

## Padrões / Preferências (o que funciona)

- 2026-11-25 ✅ Quando smokear o exe, usar senha em arquivo temporário fora do repo (sem imprimir) e validar `health`, `unlock` (certo/errado) e `validate-credentials` com credencial fake (porque prova o protocolo JSON do `__child` sem vazar valor nem depender da AWS).
- 2026-11-25 ✅ Quando escrever nota operacional para este usuário, ser curto e concreto: uma linha sobre rotação de chave basta, sem repetir conselho de segurança já aceito.
- 2026-11-25 ✅ Quando mexer na UI single-file do cliente, manter a direção "ferramenta desktop sóbria": janela clara com moldura fina, tinta grafite, um accent rust, bordas hairline, grade de 4px, tipografia de sistema, números tabulares e log escuro; sem gradientes, glass, cards genéricos ou dark slate (porque o usuário rejeita explicitamente UI com cara de "AI Slop").
- 2026-11-25 ✅ Quando a senha admin precisar ser alterável no exe congelado, manter o default no build (`eajdias`), persistir só salt+SHA-256 em `%LOCALAPPDATA%\s3-glacier-restore\admin-auth.json` e deixar `S3_RESTORE_ADMIN_*` vencer para testes (porque não exige rebuild por máquina e nunca grava a senha em claro).
- 2026-11-25 ✅ Quando adicionar tema escuro na UI single-file, usar tokens em `:root` + `:root[data-theme="dark"]`, script inline no `<head>` para evitar flash, `localStorage` e toggle com `aria-pressed`; manter a paleta quente (grafite + rust) e validar contraste WCAG no pytest (porque preserva a direção visual e prova o requisito de acessibilidade).
