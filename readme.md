# Telegram Bot

Bot experimental para Telegram, criado para aprender `python-telegram-bot`. Responde a
comandos, consulta APIs públicas simples (moedas, CEP e clima), transcreve mensagens de
voz em CPU, monitora grupos de promoções e usa a IA local (Ollama) para responder
perguntas, guardando o histórico em SQLite. Tudo que usa IA ou dados pessoais (incluindo
`/estudo`, `/nota`, `/resumo`, voz, `/tarefa` e `/ofertas`) é **fechado por padrão**: só
funciona para IDs em `ALLOWED_USER_IDS`. As notas vêm do vault Obsidian via `vault-proxy`,
o material de estudos do `estudos-proxy` e as tarefas vão para um Notion configurado pelo
próprio bot.

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
| `ESTUDOS_PATH` | `/estudos` | pasta local do corpus, montada somente leitura no Docker |
| `BOT_DB_PATH` | `bot_history.db` | banco SQLite do histórico e lembretes |
| `RATE_LIMIT_PER_MINUTE` | `5` | limite de perguntas/minuto na IA, `/tarefa` e voz |
| `DEALS_WINDOW_HOURS` | `24` | janela de tempo do `/ofertas` |
| `DEALS_TOP` | `5` | quantas ofertas o `/ofertas` mostra |
| `DEALS_ALERT_PERCENT` | `50` | desconto mínimo (%) para receber alerta em DM; `0` desliga |
| `DEALS_JEV_RANKING` | vazio (auto) | liga/desliga o piloto de ranking com Jev; vazio liga se houver `JEV_API_KEY`, `0` força desligado |
| `JEV_API_KEY` | vazio | API key do OpenCode Console para o Jev; sem ela o piloto não roda |
| `JEV_MODEL` | `jev-1.13-free` | modelo do Jev usado no ranking |
| `JEV_TIMEOUT` | `8` | timeout (s) da chamada ao Jev; em falha o `/ofertas` segue normal |
| `DEALS_HISTORY_RETENTION_DAYS` | `90` | retenção do histórico de preços (dias) |
| `DEALS_HISTORY_DEDUPE_HOURS` | `6` | janela de dedupe de observações iguais (horas) |
| `DEALS_HISTORY_MIN_SAMPLE` | `5` | amostra mínima de observações para haver veredito |
| `DEALS_HISTORY_WINDOWS_DAYS` | `7,30,90` | janelas (dias) comparadas com a mediana/mínimo |
| `DEALS_HISTORY_CHEAP_PCT` | `20` | % mínimo abaixo da mediana para considerar barato |
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
| `/estudo <pergunta>` | Pergunta ao tutor sobre o material de estudos (restrito) |
| `/estudo` | Sorteia um tópico do corpus e devolve um resumo, com botão "Outro tópico" (restrito) |
| `/topicos` | Lista os tópicos de estudos disponíveis (restrito) |
| `/ofertas [termo]` | Melhores ofertas capturadas dos grupos, por desconto (restrito) |
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
devolve a lista de notas consultadas. `/estudo <pergunta>` usa o mesmo caminho em
`{ESTUDOS_PROXY_URL}/api/chat` sobre o corpus de estudos, enquanto `/estudo` **sem
argumento** sorteia um `.md` do corpus (`ESTUDOS_PATH`), resume o conteúdo com o Ollama
local e mostra o botão "Outro tópico"; `/topicos` lista os títulos disponíveis. **Todos
são restritos** por `ALLOWED_USER_IDS` (fail-closed), assim como `/ask`, `/status` e
`/lembrete`. As perguntas de IA têm limite de `RATE_LIMIT_PER_MINUTE` por usuário para
proteger a GPU local.

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

## Ofertas dos grupos (Telegram)

Para o bot capturar promoções automaticamente:

1. No BotFather, envie `/setprivacy` → escolha **testando_tudo** → **Disable**. Sem isso
   ele só recebe mensagens que mencionam o bot.
2. Adicione o bot aos grupos de promoções (se o grupo restringe, peça ao admin). Se ele
   já estava no grupo antes de desativar o privacy mode, remova e adicione de novo.
3. A partir daí, cada mensagem de texto em grupo é analisada **localmente** por regex
   (R$, "de/por", % off, cupom, loja e link). O bot não responde nos grupos e só grava o
   que parece oferta, no SQLite (`deals`), dentro do volume `bot_data`.

O bot **não lê o histórico** dos grupos: a captura começa quando ele entra (ou é
re-adicionado). O parsing é por regras — o LLM não fica varrendo as mensagens, o que
seria lento e impreciso; a IA entra só no botão de resumo.

Comandos:

- `/ofertas` — top `DEALS_TOP` das últimas `DEALS_WINDOW_HOURS`, ordenado pelo maior
  desconto (o que não tem desconto aparece depois).
- `/ofertas <termo>` — filtra por texto ou loja, ex.: `/ofertas ssd` ou `/ofertas shopee`.
- Botão **"Resumo com IA"** — o `qwen2.5:3b` aponta as 3 mais interessantes.

Alertas: quando o desconto é maior ou igual a `DEALS_ALERT_PERCENT` (padrão 50%), o bot
manda uma DM para cada ID em `ALLOWED_USER_IDS`. Duplicatas com o mesmo link são
ignoradas por 6 horas. `/ofertas` é restrito e só funciona na conversa privada.

### Ranking com Jev (piloto, opt-in)

O `/ofertas` pode reordenar as ofertas pelo score do **Jev** (System One da TypeSafe,
via `https://opencode.ai/zen/v1/systemone`) em vez de só pelo desconto. O piloto é
**opt-in**: basta definir `JEV_API_KEY` (a `DEALS_JEV_RANKING` vazia liga; use `0` para
forçar desligado). Modelo (`JEV_MODEL`, padrão `jev-1.13-free`) e timeout (`JEV_TIMEOUT`)
são configuráveis.

- **O que sai da máquina** (por oferta, já parseada pelo regex local): título genérico
  do produto (sem URL, @menção, e-mail, telefone, convite, cupom, preço ou % off), preço,
  preço antigo, desconto e loja. **Nunca** saem nomes/IDs de usuário, grupo/chat, links,
  mensagens brutas, cupons ou histórico.
- **Fail-closed no título**: se após a limpeza sobrar padrão de dado pessoal (CPF/CNPJ,
  telefone, e-mail, @menção, convite) ou texto livre suspeito, o título é omitido (vira
  "produto"). Se nenhum candidato tiver título seguro, a chamada é pulada.
- **Fallback**: sem chave, erro HTTP/timeout, score com confiança baixa ou resposta
  inválida, o `/ofertas` mantém a ordem atual por desconto e nunca falha. O cabeçalho
  mostra "por score do Jev" só quando o ranking realmente foi aplicado; no fallback
  continua "por desconto".
- **Custo**: uma única chamada por `/ofertas`, no máximo 10 candidatos.
- **Desligar**: defina `DEALS_JEV_RANKING=0` (ou remova `JEV_API_KEY`) e reinicie o bot.

⚠️ A sanitização remove os dados sensíveis conhecidos, mas o título deriva do texto da
mensagem; mensagens de promoção com dados pessoais embutidos podem, em tese, deixar
fragmentos. Mantenha o piloto desligado se isso for inaceitável para os grupos monitorados.

## Histórico de preços (quando a oferta é realmente barata)

Além do desconto anunciado, o bot guarda um histórico de preços por produto e usa-o para
dizer se o preço atual está realmente baixo. O histórico fica na tabela
`price_observations` do mesmo SQLite (`bot_data`), criada de forma **aditiva e
idempotente** (`CREATE TABLE/INDEX IF NOT EXISTS`): não altera `deals`, `reminders` nem
`history`. Um rollback de imagem/código **não** exige mexer no banco: a versão anterior
simplesmente ignora a tabela extra e o histórico é preservado (só um `DROP TABLE
price_observations` manual e destrutivo o apagaria).

- **Identidade conservadora**: loja + título normalizado (+ URL canônica, sem parâmetros
  de tracking). Cupom, contato, preço e texto de promoção não entram na chave. Títulos
  vagos (categoria pura, sem modelo/medida) **não** ganham histórico, para não agregar
  produtos diferentes.
- **Só grava o confiável**: oferta precisa de loja, título específico e preço válido.
- **Comparação robusta**: preço atual vs. mediana/mínimo de janelas anteriores
  (`DEALS_HISTORY_WINDOWS_DAYS`), exigindo `DEALS_HISTORY_MIN_SAMPLE` observações
  anteriores. A observação atual nunca entra no próprio baseline (evita contaminação).
  Só é "realmente barato" se todas as janelas com amostra suficiente concordarem.
- **Rótulos explícitos**: "novo mínimo em Nd (N obs)", "X% abaixo da mediana de Nd
  (N obs)", "dentro do normal de Nd (N obs)" ou **"sem histórico suficiente"**. A janela
  exibida é a **mais curta com amostra suficiente** (a mais recente), então não se alega
  "90d" quando só há algumas horas de dados; o número de observações aparece no rótulo.
  Antes de haver amostra mínima o bot não afirma que a oferta é boa.
- **Preço listado ≠ preço final**: só o preço listado (o único confiável do regex) entra no
  baseline; frete e desconto de cupom **não** são incorporados. O `old_price` anunciado
  nunca é usado como referência (pode ser fictício).
- **Crescimento controlado**: dedupe de repetições na mesma janela
  (`DEALS_HISTORY_DEDUPE_HOURS`) e retenção (`DEALS_HISTORY_RETENTION_DAYS`), com índices
  por identidade/tempo e `WAL` + `busy_timeout` para concorrência.
- **Sem degradar o `/ofertas`**: a ordenação continua sendo por desconto (ou pelo Jev,
  quando ligado). O histórico só acrescenta uma linha informativa por oferta; falhas do
  histórico são silenciosas e nunca quebram a captura nem o comando.

Limitações conhecidas: não distingue variantes/tamanhos que compartilhem loja e título;
não incorpora frete/cupom; o histórico é **ponderado pelo tempo**, então um produto
repetidamente anunciado no mesmo preço pesa mais na mediana (a dedupe de 6h limita
repetições imediatas, não ao longo de dias); pode haver falsos positivos quando o preço
muda de patamar (queda estrutural) ou falsos "sem histórico" quando o título varia entre
reposts; o histórico é privado e fica no mesmo banco local das demais tabelas.

### Backup e restauração (SQLite)

Com `WAL` ativo, copiar só o `.db` (`cp`) pode perder ou corromper dados que estão no
`-wal`. Use a **API de backup do SQLite**, que produz uma cópia consistente:

```bash
# cópia consistente para dentro do volume (execute no host, via container)
docker compose --profile bot exec -T telegram-bot python - <<'PY'
import os
from datetime import datetime
from price_history import PriceHistory
os.makedirs("/data/backups", exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
PriceHistory("/data/bot_history.db").backup(f"/data/backups/bot_history-{stamp}.db")
print("backup ok")
PY
```

- O método `PriceHistory.backup()` usa `sqlite3.Connection.backup` e é seguro com WAL
  ativo e escrita concorrente.
- Restauração: pare o bot, substitua `/data/bot_history.db` pelo arquivo de backup e
  suba de novo. **Valide a restauração apenas em banco sintético** antes de qualquer
  procedimento real — nunca sobrescreva o banco de produção às cegas.


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
`ESTUDOS_PROXY_URL=http://estudos-proxy:11434`; o corpus de `~/Projetos/estudos` é
montado somente leitura em `/estudos` (`ESTUDOS_PATH`) para os resumos sorteados. O banco
e o cache de voz ficam no volume `bot_data`. A imagem já inclui o `faster-whisper` via
`requirements-audio.txt`.

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

Os testes (152) cobrem as funções puras de formatação, validação de CEP/moedas/clima,
histórico SQLite, sumarização, lembretes, autorização, rate limit, echo em grupo,
seleção de tópicos de estudo, parsing/ranking de ofertas (incluindo o piloto com Jev via
`httpx.MockTransport`), histórico de preços (schema/migração aditiva, identidade e
colisões, dedupe, janelas/amostra mínima, rótulos, retenção, rollback de código, backup e
restauração via API do SQLite e integração com o bot), transcrição de voz (com mock),
criação de tarefa no Notion (com `httpx.MockTransport`) e o registro dos handlers, sem
rede nem token real.

## Estrutura

```text
.
├── bot_tele_parrot.py     # handlers, histórico SQLite, integrações HTTP e entrypoint
├── extra_commands.py      # /guia, /meuid, /traduzir, /tarefa e transcrição de voz
├── deals.py               # parsing, score e storage das ofertas dos grupos
├── jev.py                 # piloto de ranking de ofertas com Jev (opt-in)
├── price_history.py       # histórico de preços e detecção de oferta realmente barata
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
    ├── test_deals.py
    ├── test_extras.py
    ├── test_jev.py
    └── test_price_history.py
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
Em 2026-09-11 (fase 4) ganhou `/estudo` sem argumento sorteando um tópico com resumo e
botão "Outro tópico", além de `/topicos` para listar o corpus local.
Em 2026-09-11 (fase 5) ganhou captura de ofertas dos grupos (`/ofertas`, alertas por
desconto e resumo com IA), ativada ao desativar o privacy mode no BotFather.
Em 2026-09-23 ganhou o piloto opt-in de ranking de ofertas com Jev (System One da
TypeSafe), com sanitização dos campos enviados e fallback para a ordem por desconto.
