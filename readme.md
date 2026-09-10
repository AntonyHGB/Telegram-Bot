# Telegram Bot

Bot experimental para Telegram, criado para aprender `python-telegram-bot`. Responde a
comandos e consulta APIs públicas simples (cotação de BTC, CEP e insultos). O bot só
responde a quem envia mensagem; não há banco de dados nem estado persistente.

## Requisitos

- Python 3.10+
- Um token do [@BotFather](https://t.me/BotFather)

## Instalar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configurar

O token **nunca** fica no código. Copie o exemplo e preencha:

```bash
cp .env.example .env
export TELEGRAM_BOT_TOKEN="seu-token-aqui"
```

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
| `/coin` | Cotação do BTC em BRL (AwesomeAPI) |
| `/cep <codigo>` | Endereço do CEP, ex.: `/cep 01310100` |
| `/insult` | Insulto aleatório (evilinsult.com) |
| `/end` | Despedida |

Qualquer outra mensagem de texto é ecoada de volta.

## Testes e lint

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

Os testes cobrem as funções puras de formatação, a validação do CEP e o registro dos
handlers, sem rede nem token real. As chamadas HTTP usam timeout de 10s e falham com
mensagem amigável.

## Estrutura

```text
.
├── bot_tele_parrot.py   # handlers, integrações HTTP e entrypoint
├── requirements.txt     # runtime
├── requirements-dev.txt # runtime + testes/lint
├── pyproject.toml       # config de pytest e ruff
└── tests/test_bot.py
```

## Histórico

Projeto mantido como referência de aprendizado (2021). A versão atual usa a API assíncrona
do `python-telegram-bot` v21+; a API antiga (`Updater`, `Filters`) foi removida na v20.
