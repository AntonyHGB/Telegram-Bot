# Telegram Bot

Bot experimental para Telegram, criado para aprender `python-telegram-bot`. Responde a
comandos, consulta APIs públicas simples (moedas, CEP e clima) e usa a IA local (Ollama)
para responder perguntas, guardando o histórico em SQLite. Os comandos `/nota` e `/resumo`
são restritos e consultam o vault Obsidian via `vault-proxy` da ai-stack.

## Requisitos

- Python 3.10+
- Um token do [@BotFather](https://t.me/BotFather)
- Para `/ask`: Ollama local (padrão `qwen2.5:3b`)
- Para `/nota`: ai-stack no ar com o `vault-proxy`

## Instalar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configurar

O token **nunca** fica no código. Copie o exemplo e exporte as variáveis:

```bash
cp .env.example .env
set -a; source .env; set +a
```

Variáveis reconhecidas:

| Variável | Padrão | Uso |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | obrigatória para rodar |
| `ALLOWED_USER_IDS` | vazio | IDs autorizados no `/nota`; vazio desabilita o comando |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | endpoint do Ollama para `/ask` |
| `OLLAMA_MODEL` | `qwen2.5:3b` | modelo usado em `/ask` e `/nota` |
| `VAULT_PROXY_URL` | `http://127.0.0.1:11435` | proxy do vault para `/nota` e `/resumo` |
| `BOT_DB_PATH` | `bot_history.db` | banco SQLite do histórico e lembretes |
| `RATE_LIMIT_PER_MINUTE` | `5` | limite de perguntas por minuto no `/ask`, `/nota` e `/resumo` |

## Rodar

```bash
python bot_tele_parrot.py
```

Se `TELEGRAM_BOT_TOKEN` não estiver definido, o programa encerra com uma mensagem clara.

## Comandos

| Comando | Efeito |
|---|---|
| `/start` | Responde "Hello World!" |
| `/help` | Piada padrão |
| `/commands` | Lista os comandos |
| `/time` | Data e hora atuais |
| `/coin [USD]` | Cotações em BRL (padrão USD, EUR, BTC), com botão de atualizar |
| `/cep <codigo>` | Endereço do CEP, ex.: `/cep 01310100` |
| `/clima <cidade>` | Clima atual via Open-Meteo, ex.: `/clima Sao Paulo` |
| `/insult` | Insulto aleatório (evilinsult.com) |
| `/ask <pergunta>` | Responde usando o Ollama local, com streaming e contexto |
| `/nota <pergunta>` | Responde usando as notas do vault Obsidian (restrito) |
| `/resumo` | Resume as notas de diário mais recentes do vault (restrito) |
| `/lembrete 10m texto` | Agenda um lembrete (`s`/`m`/`h`/`d`), persistido em SQLite |
| `/status` | Mostra Ollama, modelo, vault, histórico, lembretes e uptime |
| `/reset` | Limpa o histórico de conversa do usuário |
| `/end` | Despedida |

Qualquer outra mensagem de texto é ecoada de volta em conversa privada; em grupos, só
quando o bot é mencionado. O menu nativo do Telegram é registrado via `set_my_commands`.

## IA local e vault

`/ask` chama `POST {OLLAMA_URL}/api/chat` com streaming: a resposta é editada conforme
chega. As 8 últimas mensagens ficam no SQLite; quando o histórico passa de 24 mensagens,
as mais antigas são resumidas pelo próprio modelo e substituídas por um resumo.
`/reset` apaga tudo. Cada resposta traz um botão "Limpar historico".

`/nota` e `/resumo` chamam `{VAULT_PROXY_URL}/api/chat`, que injeta trechos do vault e
devolve a lista de notas consultadas. Como são dados privados, só funcionam para IDs
em `ALLOWED_USER_IDS`; sem essa variável ficam desabilitados. As perguntas de IA têm
limite de `RATE_LIMIT_PER_MINUTE` por usuário para proteger a GPU local.

`/lembrete` usa o `JobQueue` do PTB e uma tabela `reminders` no SQLite: lembretes
pendentes são reagendados automaticamente quando o bot reinicia.

## Docker

O serviço já está no `docker-compose.yml` da ai-stack (profile `bot`) e sobe junto com ela:

```bash
cd ~/Projetos/ai-stack
cp .env.example .env   # defina TELEGRAM_BOT_TOKEN e ALLOWED_USER_IDS
docker compose --profile bot up -d --build telegram-bot
docker compose logs -f telegram-bot
```

Na rede da stack o bot usa `OLLAMA_URL=http://ollama:11434` e
`VAULT_PROXY_URL=http://vault-proxy:11434`; o banco fica no volume `bot_data`.

## Testes e lint

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

Os testes (51) cobrem as funções puras de formatação, validação de CEP/moedas/clima,
histórico SQLite, sumarização, lembretes, autorização do `/nota`/`/resumo`, rate limit,
echo em grupo e o registro dos handlers, sem rede nem token real.
`fetch_json` e `chat` aceitam `httpx.MockTransport`/monkeypatch para simular as APIs.

## Estrutura

```text
.
├── bot_tele_parrot.py   # handlers, histórico SQLite, integrações HTTP e entrypoint
├── Dockerfile           # imagem usada pelo serviço telegram-bot da ai-stack
├── requirements.txt     # runtime (versões pinadas)
├── requirements-dev.txt # runtime + testes/lint
├── pyproject.toml       # config de pytest e ruff
├── .env.example
└── tests/test_bot.py
```

## Histórico

Projeto mantido como referência de aprendizado (2021). A versão atual usa a API assíncrona
do `python-telegram-bot` v21+; a API antiga (`Updater`, `Filters`) foi removida na v20.
Em 2026-09-10 ganhou IA local, histórico SQLite, `/nota` restrito e empacotamento Docker.
Em 2026-09-11 ganhou streaming, lembretes persistentes, `/clima`, `/resumo`, `/status`,
menu nativo, botões inline, rate limit e resumo automático do histórico.
