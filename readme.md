# Telegram Bot

Bot experimental para Telegram, criado para aprender `python-telegram-bot`. Responde a
comandos, consulta APIs públicas simples (moedas, CEP e clima), transcreve mensagens de
voz em CPU e usa a IA local (Ollama) para responder perguntas, guardando o histórico em
SQLite. Tudo que usa IA ou dados pessoais (incluindo `/estudo`, `/nota`, `/resumo`, voz e
`/tarefa`) é **fechado por padrão**: só funciona para IDs em `ALLOWED_USER_IDS`. As notas
vêm do vault Obsidian via `vault-proxy`, o material de estudos do `estudos-proxy` e as
tarefas vão para um Notion configurado pelo próprio bot.

## Requisitos

- Python 3.10+
- Um token do [@BotFather](https://t.me/BotFather)
- Para `/ask`: Ollama local (padrão `qwen2.5:3b`)
- Para `/nota` e `/resumo`: ai-stack no ar com o `vault-proxy`
- Para `/estudo`: ai-stack no ar com o `estudos-proxy`
- Para voz: `requirements-audio.txt` (`faster-whisper`, roda em CPU)
- Para `/tarefa`: integração interna do Notion com acesso à base escolhida

## Instalar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime + testes/lint
pip install -r requirements-audio.txt # opcional: transcrição de voz
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
| `ALLOWED_USER_IDS` | vazio | lista de IDs autorizados em `/ask`, `/estudo`, `/nota`, `/resumo`, `/status`, `/lembrete`, `/tarefa` e voz; vazio desabilita esses comandos |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | endpoint do Ollama para `/ask` |
| `OLLAMA_MODEL` | `qwen2.5:3b` | modelo usado em `/ask`, `/nota` e `/traduzir` |
| `VAULT_PROXY_URL` | `http://127.0.0.1:11435` | proxy do vault para `/nota` e `/resumo` |
| `ESTUDOS_PROXY_URL` | `http://127.0.0.1:11436` | proxy do tutor de estudos para `/estudo` |
| `BOT_DB_PATH` | `bot_history.db` | banco SQLite do histórico e lembretes |
| `RATE_LIMIT_PER_MINUTE` | `5` | limite de perguntas/minuto na IA, `/tarefa` e voz |
| `NOTION_TOKEN` | vazio | credencial da integração interna do Notion |
| `NOTION_DATA_SOURCE_ID` | vazio | data source onde `/tarefa` cria páginas |
| `WHISPER_MODEL` | `base` | tamanho do modelo Whisper (`tiny`/`base`/`small`...) |
| `WHISPER_CACHE` | temp do sistema | pasta de cache do modelo de voz |

Use `/meuid` no Telegram para descobrir seu ID e colocar em `ALLOWED_USER_IDS`.

## Rodar

```bash
python bot_tele_parrot.py
```

Se `TELEGRAM_BOT_TOKEN` não estiver definido, o programa encerra com uma mensagem clara.

## Comandos

| Comando | Efeito |
|---|---|
| `/start` | Responde "Hello World!" |
| `/guia` | Resumo do que o bot faz, com exemplos |
| `/help` | Mostra o guia (atalho) |
| `/meuid` | Mostra seu ID do Telegram |
| `/commands` | Lista os comandos |
| `/time` | Data e hora atuais |
| `/coin [USD]` | Cotações em BRL (padrão USD, EUR, BTC), com botão de atualizar |
| `/cep <codigo>` | Endereço do CEP, ex.: `/cep 01310100` |
| `/clima <cidade>` | Clima atual via Open-Meteo, ex.: `/clima Sao Paulo` |
| `/insult` | Insulto aleatório (evilinsult.com) |
| `/ask <pergunta>` | IA local com streaming e contexto (restrito) |
| `/traduzir <idioma> <texto>` | Tradução pela IA local, ex.: `/traduzir inglês Bom dia` (restrito) |
| `/nota <pergunta>` | Responde usando as notas do vault Obsidian (restrito) |
| `/estudo <pergunta>` | Responde usando o material de estudos (restrito) |
| `/resumo` | Resume as notas de diário mais recentes do vault (restrito) |
| `/lembrete 10m texto` | Agenda um lembrete (`s`/`m`/`h`/`d`), persistido em SQLite (restrito) |
| `/tarefa <titulo>` | Cria uma página no Notion configurado (restrito) |
| `/status` | Mostra Ollama, modelo, vault, histórico, lembretes e uptime (restrito) |
| `/reset` | Limpa o histórico de conversa do usuário |
| `/end` | Despedida |
| *(mensagem de voz)* | Transcreve o áudio em CPU (restrito, até 2 min/10 MB) |

Qualquer outra mensagem de texto é ecoada de volta em conversa privada; em grupos, só
quando o bot é mencionado. O menu nativo do Telegram é registrado via `set_my_commands`.
Comandos que usam histórico (IA, notas e voz) só funcionam em conversa privada, para não
expor seu histórico em grupos.

## Controle de acesso

O bot adota **fail-closed**: sem `ALLOWED_USER_IDS` configurado, os comandos de IA, dados
e automações ficam desabilitados (respondem "Acesso restrito"). Públicos ficam apenas os
comandos sem dados pessoais e sem custo de GPU: `/start`, `/guia`, `/help`, `/meuid`,
`/commands`, `/time`, `/coin`, `/cep`, `/clima`, `/insult`, `/reset` (só o próprio
histórico) e o eco em conversa privada.

Para liberar alguém: a pessoa envia `/meuid`, o ID entra em `ALLOWED_USER_IDS` (separado
por vírgula) e o bot é reiniciado. O rate limit continua valendo por usuário.

## IA local e vault

`/ask` chama `POST {OLLAMA_URL}/api/chat` com streaming: a resposta é editada conforme
chega. As 8 últimas mensagens ficam no SQLite; quando o histórico passa de 24 mensagens,
as mais antigas são resumidas pelo próprio modelo e substituídas por um resumo.
`/reset` apaga tudo. Cada resposta traz um botão "Limpar historico".

`/nota` e `/resumo` chamam `{VAULT_PROXY_URL}/api/chat`, que injeta trechos do vault e
devolve a lista de notas consultadas. `/estudo` usa o mesmo caminho em
`{ESTUDOS_PROXY_URL}/api/chat` sobre o corpus de estudos. **Todos são restritos** por
`ALLOWED_USER_IDS` (fail-closed), assim como `/ask`, `/status` e `/lembrete`; o corpus
de estudos não é exposto a usuários não autorizados. As perguntas de IA têm limite de
`RATE_LIMIT_PER_MINUTE` por usuário para proteger a GPU local.

`/lembrete` usa o `JobQueue` do PTB e uma tabela `reminders` no SQLite: lembretes
pendentes são reagendados automaticamente quando o bot reinicia.

## Voz (faster-whisper)

Mensagens de voz de usuários autorizados são baixadas para um arquivo temporário,
transcritas em CPU com `faster-whisper` (`device="cpu"`, `compute_type="int8"`,
`vad_filter=True`) e o arquivo é apagado em seguida. A transcrição é enviada em partes
de até 3500 caracteres e **não** é enviada automaticamente para a IA nem para o Notion.

- Limite de 2 minutos e 10 MB por áudio; apenas uma transcrição por vez.
- O modelo é baixado na primeira transcrição e fica no cache (`WHISPER_CACHE`).
- No Docker o cache fica em `/data/whisper`, dentro do volume `bot_data`, então
  sobrevive a rebuilds. O modelo `base` ocupa ~150 MB e roda em português.

## Notion (`/tarefa`)

A integração é independente do MCP do Notion usado pelo OpenCode: o bot fala direto com
a API do Notion usando uma credencial só dele.

1. Crie uma integração interna em https://www.notion.so/my-integrations e copie o token.
2. No Notion, abra a base de tarefas → `•••` → **Connections** → conecte a integração.
3. Descubra o data source da base. Com o OpenCode, use o fetch na base e copie o ID do
   `<data-source url="collection://...">`; sem OpenCode, use a API de search do Notion.
4. Preencha `NOTION_TOKEN` e `NOTION_DATA_SOURCE_ID` no `.env` e reinicie o bot.

`/tarefa` lê o schema do data source e usa a propriedade de título, então funciona com
bases em português ("Tarefa", "Nome" etc.). Em caso de erro/timeout depois do POST, o
bot avisa para conferir a base antes de repetir, porque a página pode ter sido criada.

## Docker

O serviço já está no `docker-compose.yml` da ai-stack (profile `bot`) e sobe junto com ela:

```bash
cd ~/Projetos/ai-stack
cp .env.example .env   # defina TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS e, se quiser, Notion
docker compose --profile bot up -d --build telegram-bot
docker compose logs -f telegram-bot
```

Na rede da stack o bot usa `OLLAMA_URL=http://ollama:11434`,
`VAULT_PROXY_URL=http://vault-proxy:11434` e
`ESTUDOS_PROXY_URL=http://estudos-proxy:11434`; o banco e o cache de voz ficam no volume
`bot_data`. A imagem já inclui o `faster-whisper` via `requirements-audio.txt`.

Se a ai-stack não for editada, o arquivo `compose.bot.yaml` deste repositório adiciona
as variáveis de Notion/voz por merge:

```bash
cd ~/Projetos/ai-stack
docker compose -f docker-compose.yml -f ../Telegram-Bot/compose.bot.yaml \
  --profile bot up -d --build telegram-bot
```

## Testes e lint

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

Os testes (66) cobrem as funções puras de formatação, validação de CEP/moedas/clima,
histórico SQLite, sumarização, lembretes, autorização, rate limit, echo em grupo,
transcrição de voz (com mock), criação de tarefa no Notion (com `httpx.MockTransport`)
e o registro dos handlers, sem rede nem token real.

## Estrutura

```text
.
├── bot_tele_parrot.py     # handlers, histórico SQLite, integrações HTTP e entrypoint
├── extra_commands.py      # /guia, /meuid, /traduzir, /tarefa e transcrição de voz
├── integrations.py        # faster-whisper + cliente independente do Notion
├── Dockerfile             # imagem usada pelo serviço telegram-bot da ai-stack
├── compose.bot.yaml       # override opcional do compose da ai-stack
├── requirements.txt       # runtime (versões pinadas)
├── requirements-audio.txt # runtime + faster-whisper
├── requirements-dev.txt   # runtime + testes/lint
├── pyproject.toml         # config de pytest e ruff
├── .env.example
└── tests/
    ├── test_bot.py
    └── test_extras.py
```

## Histórico

Projeto mantido como referência de aprendizado (2021). A versão atual usa a API assíncrona
do `python-telegram-bot` v21+; a API antiga (`Updater`, `Filters`) foi removida na v20.
Em 2026-09-10 ganhou IA local, histórico SQLite, `/nota` restrito e empacotamento Docker.
Em 2026-09-11 ganhou streaming, lembretes persistentes, `/clima`, `/resumo`, `/status`,
menu nativo, botões inline, rate limit e resumo automático do histórico.
Em 2026-09-11 (fase 2) ganhou transcrição de voz local, `/tarefa` no Notion, `/guia`,
`/meuid` e `/traduzir`.
Em 2026-09-11 (fase 3) ganhou `/estudo`, o tutor de estudos servido pelo `estudos-proxy`
da ai-stack, e o acesso ficou **fail-closed**: todos os comandos de IA/dados
(`/ask`, `/estudo`, `/nota`, `/resumo`, `/status`, `/lembrete`, voz e `/tarefa`) exigem
`ALLOWED_USER_IDS`, e o prompt do modelo não finge conhecer a configuração do bot.
