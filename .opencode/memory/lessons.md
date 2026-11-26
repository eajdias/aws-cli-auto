# Agent Memory — Lessons (não repetir)

> Consulte antes de cada tarefa; atualize a cada correção/aprendizado.
> Formato: ❌ <não faça> → ✅ <faça> (porque). Entradas curtas e acionáveis.

## Erros / Lições (não repetir)

- 2026-11-25 ❌ Setar `AWS_PROFILE=""` ou herdar `AWS_*` do ambiente → ✅ Omitir a variável, remover `AWS_CONFIG_FILE`/`AWS_SHARED_CREDENTIALS_FILE`/`AWS_SESSION_TOKEN` e overrides de web identity/container, manter o bloco colado só no `env` do filho e limpar o campo da UI ao iniciar (porque string vazia vira `ProfileNotFound: ()` no botocore e credencial fora do env do filho vaza em argv/log/relatório).
- 2026-11-25 ❌ Rodar PyInstaller no shell elevado do agente → ✅ Rodar o build via Agendador com `/RL LIMITED` e fazer o cmd gravar o exit code em arquivo (porque `runas /trustlevel:0x20000` não reduziu o token e o PyInstaller 6.x emite `DEPRECATION`; o arquivo permite esperar sem polling de console).
- 2026-11-25 ❌ Rebuildar com instâncias órfãs do exe segurando `dist/s3-download-client.exe` → ✅ Encerrar processos cuja `CommandLine` contenha o caminho do exe antes do build (porque o PyInstaller falha com `PermissionError: [WinError 5]` ao tentar `os.remove`).
- 2026-11-25 ❌ Usar `Invoke-WebRequest` no smoke HTTP do exe → ✅ Usar `curl.exe` com corpo JSON em arquivo (porque o cmdlet levantou `NullReferenceException`/`ConnectionResetError` e o PowerShell faz stripping de aspas em JSON inline).
- 2026-11-25 ❌ Escrever corpo JSON com `Set-Content -Encoding utf8` no Windows PowerShell 5.1 → ✅ `[System.IO.File]::WriteAllText(..., UTF8Encoding($false))` ou `utf8NoBom` no PS7 (porque o BOM vira `json invalido` no servidor; smoke com `curl.exe` falhou até trocar).
- 2026-11-25 ❌ Gerar build ou manifesto por cliente no exe → ✅ Manter o exe runtime-configurado: serial, buckets e credenciais pela UI (porque 1 binário serve ~10 clientes/dia sem rebuild; dado de cliente no binário cria acoplamento e risco de vazamento).
- 2026-11-25 ❌ Tratar todo primeiro argumento do exe como diretório de appdata → ✅ Rotear subcomandos CLI conhecidos para `cli.main`, com diretório real tendo precedência (porque o painel admin chamava `auto`/`verify` e o exe abria outro servidor em vez de executar).

## Padrões / Preferências (o que funciona)

<!-- - 2026-08-20 ✅ Quando <situação>, faça <o que funciona> -->
