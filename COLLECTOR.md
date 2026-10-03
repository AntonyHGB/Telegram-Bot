# Coletor MTProto (Telegram user API) — piloto do canal aprovado

Coletor mínimo, **isolado**, que lê **apenas o canal público aprovado** no
export (`source='tdesktop_export'`) usando a user API (conta do usuário), e
alimenta as mesmas tabelas `deals`/`price_observations` do bot.

> ⚠️ Nada aqui foi instalado, logado ou executado. O agente não roda login,
> não lê `tdata`, caches, `.env` nem tokens do bot, e não configura timer.

## Segurança e exposição de sessão

- `api_id`/`api_hash` vêm **só do ambiente** (`TELEGRAM_API_ID`,
  `TELEGRAM_API_HASH`). Nunca do token do bot, de `tdata` ou de arquivos de auth.
- O arquivo de sessão do Telethon (`*.session`) concede **acesso total à conta**
  (ler/enviar como você). Trate como senha:
  - guarde com permissão `600`, fora do repositório (o `.gitignore` já cobre
    `*.session`, `*.session-journal`);
  - nunca cole o conteúdo da sessão em chat, issue ou log;
  - se vazar, faça logout (`client.log_out()`) e revogue a sessão no Telegram
    (Configurações → Dispositivos) e recrie.
- Prefira uma conta dedicada. O coletor **não** envia mensagens, não entra em
  grupos, não baixa mídia e não faz login sozinho.
- O agente não deve executar `--login`, `--preflight` nem `--apply` reais.

## Instalação (manual, quando for usar)

```bash
cd ~/Projetos/Telegram-Bot
python -m venv .venv
.venv/bin/pip install -r requirements-collector.txt   # Telethon
```

## 1. Criar `api_id` / `api_hash`

1. Acesse https://my.telegram.org → **API development tools**.
2. Crie um app e copie `api_id` e `api_hash` **para um lugar seguro** (não no chat).
3. Exporte no shell (ou em arquivo privado `600`, ex. `~/.config/telegram-collector.env`):

```bash
export TELEGRAM_API_ID='<seu api_id>'
export TELEGRAM_API_HASH='<seu api_hash>'
```

## 2. Preencher a allowlist privada

No arquivo `~/.local/share/telegram-bot-import/allowlist.json`, na seção
`mtproto`, preencha `username` com o `@username` do canal aprovado
(`expected_peer_id`, `reuse_source` e `chat_type` já vêm do export):

```json
"mtproto": {
  "username": "<@username-do-canal>",
  "expected_peer_id": "<id do export, já preenchido>",
  "reuse_source": "tdesktop_export",
  "chat_type": "public_channel",
  "max_window_days": 90
}
```

## 3. Login interativo (só o usuário, local)

> Rode no **container** (ver "Onde executar" abaixo): o apply real precisa do
> volume Docker. O login abaixo é o mesmo comando, com `-it` e o volume de
> sessão.

```bash
.venv/bin/python telegram_user_collector.py --login
```

Digite telefone/código/2FA no terminal. Nada de segredo em chat. O arquivo de
sessão fica no caminho de `--session` (padrão
`~/.local/share/telegram-bot-import/collector.session`).

## 4. Preflight (só o canal autorizado)

```bash
.venv/bin/python telegram_user_collector.py --preflight
# -> {"identity_verified": true/false, "is_group_like": true}
```

`identity_verified: true` significa que o peer resolvido bate com o
`expected_peer_id` do export — então `source='tdesktop_export'` é reutilizado e
a dedupe contra o backfill vale. Se `false`, o coletor usa o namespace separado
`source='mtproto'` (não presume que os ids de mensagem coincidem com o export).

## 5. Dry-run e apply

```bash
# dry-run sem rede (valida config/janela, não exige credenciais)
.venv/bin/python telegram_user_collector.py --allowlist <...> --window-days 90

# coleta real (depois do preflight verificado e com aprovação explícita)
.venv/bin/python telegram_user_collector.py --preflight
.venv/bin/python telegram_user_collector.py --apply --db <DB>
```

- Janela **≤ 90 dias** (alinhada à retenção); cursor incremental via `min_id`.
- `FloodWait`/429: aguarda o tempo indicado (padrão até 600 s, 3 tentativas) e
  **aborta** se for maior.
- Só a entidade autorizada é acessada; **nunca** `get_dialogs`/contatos.
- Mídia não é baixada; no máximo um caminho de metadado opcional.
- Paginação cobre a janela inteira; se `--limit` for atingido antes de cobrir a
  janela, o coletor **aborta** (não cria buraco no cursor incremental).

### Onde executar (host × volume Docker) — importante

O banco real vive no volume Docker `ai-stack_bot_data` (`/data/bot_history.db`),
que é **root-owned**. Um serviço `systemd --user` no host **não** consegue
escrever nesse volume sem `sudo`. Portanto o coletor deve rodar **num
container** que monte o volume (o daemon do Docker escreve como root), e não
diretamente no host apontando para `/data`.

Como a imagem do bot **não** inclui o Telethon, use a imagem dedicada
`Dockerfile.collector` (manifest; não construída ainda):

```bash
# construir (quando autorizado)
docker build -f Dockerfile.collector -t telegram-collector .

# login interativo (uma vez)
docker run -it --rm \
  -v telegram_collector_session:/session \
  -e TELEGRAM_API_ID -e TELEGRAM_API_HASH \
  telegram-collector --session /session/collector.session --login

# preflight + apply (dry-run é o padrão; --apply grava)
docker run --rm \
  -v ai-stack_bot_data:/data \
  -v telegram_collector_session:/session \
  -v "$HOME/.local/share/telegram-bot-import/allowlist.json:/allowlist/allowlist.json:ro" \
  -e TELEGRAM_API_ID -e TELEGRAM_API_HASH \
  telegram-collector --session /session/collector.session \
  --allowlist /allowlist/allowlist.json --db /data/bot_history.db --preflight
```

> Em Fedora com SELinux, bind mount de arquivo no host pode exigir `:z`
> (`...:ro,z`) ou copiar a allowlist para o volume de sessão. A sessão deve
> ficar em volume nomeado (não em bind), pelo mesmo motivo.

## Limites da Telegram API (por que o coletor é conservador)

- **FloodWait/429**: a API limita a frequência; o servidor manda esperar N
  segundos. Ignorar arrisca banimento temporário da conta. Por isso o backoff e
  o aborto.
- **Sem janela server-side de 90d**: o limite de 90d é nossa **retenção**, não
  da API. A API expõe o histórico do canal, mas varrer tudo é caro e arriscado.
- **Conta real**: user API age como a conta; erros podem afetar a conta inteira.
- **Sessão**: qualquer `*.session` vale como login completo — vazamento = conta
  comprometida.

## Plano de timer (NÃO configurado)

Só depois de uma chamada real aprovada e verificada. Como o volume é root-owned,
o timer no host **executa `docker run`** (o usuário está no grupo `docker`; sem
`sudo`), e não o coletor direto no host.

```ini
# ~/.config/systemd/user/telegram-collector.service
[Service]
Type=oneshot
ExecStart=/usr/bin/docker run --rm \
  -v ai-stack_bot_data:/data \
  -v telegram_collector_session:/session \
  -v %h/.local/share/telegram-bot-import/allowlist.json:/allowlist/allowlist.json:ro,z \
  -e TELEGRAM_API_ID -e TELEGRAM_API_HASH \
  telegram-collector --session /session/collector.session \
  --allowlist /allowlist/allowlist.json --db /data/bot_history.db --apply
```

```ini
# ~/.config/systemd/user/telegram-collector.timer
[Timer]
OnCalendar=*-*-* 06,18:00:00
Persistent=true
[Install]
WantedBy=timers.target
```

⚠️ As variáveis `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` precisam estar no ambiente
do `systemd --user` (ex.: `EnvironmentFile=%h/.config/telegram-collector.env`,
modo `600`) — nunca no chat nem no repositório. O bot **não** precisa reiniciar
(abre conexão por chamada). **Não criar o timer** antes da validação real.

## Privacidade

- O coletor reutiliza `telegram_export_import.build_record`: o texto gravado é
  minimizado (sem URL/@/CPF/preço bruto), nunca a mensagem crua.
- Logs só têm contagens/booleanos — sem id, username ou texto.
- Sem envio ao Telegram ou ao Zen; sem download de mídia.
