# Telegram Bot

Bot experimental para Telegram, criado para aprender `python-telegram-bot`. Responde a
comandos, consulta APIs públicas simples (moedas e CEP) e usa a IA local (Ollama) para
responder perguntas, guardando o histórico em SQLite. O comando `/nota` é restrito e
consulta o vault Obsidian via `vault-proxy` da ai-stack.

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
| `VAULT_PROXY_URL` | `http://127.0.0.1:11435` | proxy do vault para `/nota` |
| `BOT_DB_PATH` | `bot_history.db` | banco SQLite do histórico |

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
| `/coin [USD]` | Cotações em BRL (padrão USD, EUR, BTC) |
| `/cep <codigo>` | Endereço do CEP, ex.: `/cep 01310100` |
| `/insult` | Insulto aleatório (evilinsult.com) |
| `/ask <pergunta>` | Responde usando o Ollama local, com contexto das últimas mensagens |
| `/nota <pergunta>` | Responde usando as notas do vault Obsidian (restrito) |
| `/reset` | Limpa o histórico de conversa do usuário |
| `/end` | Despedida |

Qualquer outra mensagem de texto é ecoada de volta.

## IA local e vault

`/ask` chama `POST {OLLAMA_URL}/api/chat` (sem streaming) e mantém as 8 últimas
mensagens por usuário no SQLite. `/reset` apaga esse histórico.

`/nota` chama `{VAULT_PROXY_URL}/api/chat`, que injeta trechos do vault e devolve a
lista de notas consultadas. Como são dados privados, o comando só funciona para IDs
em `ALLOWED_USER_IDS`; sem essa variável ele fica desabilitado.

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

Os testes cobrem as funções puras de formatação, validação de CEP/moedas, histórico
SQLite, autorização do `/nota` e o registro dos handlers, sem rede nem token real.
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
