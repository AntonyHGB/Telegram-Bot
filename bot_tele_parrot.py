import json
import logging
import os
import random
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from deals import DealStore, deals_summary_prompt, format_deal, format_deal_alert, parse_deal
from extra_commands import guia, register_extras
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import InvalidToken, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
HTTP_TIMEOUT = 10.0
LLM_TIMEOUT = 120.0
HISTORY_LIMIT = 8
HISTORY_THRESHOLD = 24
REPLY_LIMIT = 4000
EDIT_INTERVAL = 1.5
REMINDER_MAX_SECONDS = 30 * 24 * 3600
START_TIME = time.monotonic()

COIN_URL = "https://economia.awesomeapi.com.br/last/{pairs}"
DEFAULT_COINS = ("USD-BRL", "EUR-BRL", "BTC-BRL")
CEP_URL = "https://cep.awesomeapi.com.br/json/{cep}"
INSULT_URL = "https://evilinsult.com/generate_insult.php?lang=en&type=json"
GEO_URL = (
    "https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=pt&format=json"
)
WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}"
    "&current=temperature_2m,apparent_temperature,weather_code&timezone=auto"
)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
VAULT_PROXY_URL = os.environ.get("VAULT_PROXY_URL", "http://127.0.0.1:11435")
ESTUDOS_PROXY_URL = os.environ.get("ESTUDOS_PROXY_URL", "http://127.0.0.1:11436")
ESTUDOS_PATH = os.environ.get("ESTUDOS_PATH", "/estudos")
TOPIC_LIMIT = 8000
DEALS_WINDOW_HOURS = int(os.environ.get("DEALS_WINDOW_HOURS", "24"))
DEALS_TOP = int(os.environ.get("DEALS_TOP", "5"))
DEALS_ALERT_PERCENT = int(os.environ.get("DEALS_ALERT_PERCENT", "50"))
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
DB_PATH = os.environ.get("BOT_DB_PATH", "bot_history.db")
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "5"))

SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer in Brazilian Portuguese, concisely. "
    "You have no access to this Telegram bot's configuration, access rules or source code; "
    "if asked about them, say you cannot see that and point to /guia."
)
RESUMO_QUESTION = (
    "Resuma o que esta registrado nas notas de diario mais recentes do vault: "
    "o que foi feito, o que ficou pendente e prioridades. Use topicos curtos."
)
TOPIC_SYSTEM_PROMPT = "Voce e um tutor de estudos. Explique em portugues, de forma clara."
TOPIC_QUESTION = (
    "Resuma o material abaixo em topicos curtos, destacando definicoes, pontos-chave "
    "e um exemplo se existir. Use apenas o material, sem inventar."
)

PAIR_RE = re.compile(r"^[A-Z]{3}-[A-Z]{3}$")
DURATION_RE = re.compile(r"^(\d{1,3})([smhd])$")

WEATHER_CODES = {
    0: "Ceu limpo",
    1: "Predominancia de sol",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Nevoeiro",
    48: "Nevoeiro com geada",
    51: "Garoa leve",
    53: "Garoa",
    55: "Garoa forte",
    61: "Chuva leve",
    63: "Chuva",
    65: "Chuva forte",
    71: "Neve leve",
    73: "Neve",
    75: "Neve forte",
    80: "Pancadas de chuva leves",
    81: "Pancadas de chuva",
    82: "Pancadas de chuva fortes",
    95: "Trovoada",
    96: "Trovoada com granizo",
    99: "Trovoada forte",
}

BOT_COMMANDS = [
    BotCommand("start", "Diz Hello World"),
    BotCommand("help", "Guia de recursos e exemplos"),
    BotCommand("commands", "Lista os comandos"),
    BotCommand("time", "Data e hora atuais"),
    BotCommand("coin", "Cotacoes em BRL"),
    BotCommand("cep", "Consulta um CEP"),
    BotCommand("clima", "Clima de uma cidade"),
    BotCommand("insult", "Um insulto aleatorio"),
    BotCommand("ask", "Pergunta para a IA local (restrito)"),
    BotCommand("estudo", "Sorteia um tópico ou pergunta (restrito)"),
    BotCommand("topicos", "Lista os tópicos de estudos (restrito)"),
    BotCommand("ofertas", "Melhores ofertas dos grupos (restrito)"),
    BotCommand("nota", "Consulta o vault (restrito)"),
    BotCommand("resumo", "Resumo do diario (restrito)"),
    BotCommand("lembrete", "Cria um lembrete (restrito)"),
    BotCommand("status", "Status dos servicos (restrito)"),
    BotCommand("reset", "Limpa o historico"),
    BotCommand("end", "Despedida"),
]

COMMANDS_TEXT = (
    "/start will say Hello World to you\n"
    "/help e /guia mostram recursos e exemplos\n"
    "/meuid mostra seu ID\n"
    "/traduzir ingles Bom dia traduz um texto (restrito)\n"
    "/tarefa <titulo> cria uma tarefa no Notion (restrito)\n"
    "Envie uma mensagem de voz para transcrever (restrito)\n"
    "/commands you already understand\n"
    "/time will show the hour to you\n"
    "/coin [USD] will show quotes in Real (default: USD, EUR, BTC)\n"
    "/cep <codigo> will show more information about your cep\n"
    "/clima <cidade> will show the weather\n"
    "/insult will insult you\n"
    "/ask <question> asks the local AI (restricted)\n"
    "/estudo <question> asks the study tutor (restricted)\n"
    "/estudo sem argumento sorteia um tópico com resumo (restricted)\n"
    "/topicos lists the study topics (restricted)\n"
    "/ofertas shows the best captured deals (restricted)\n"
    "/nota <question> asks using your Obsidian vault (restricted)\n"
    "/resumo summarizes the latest diary notes (restricted)\n"
    "/lembrete <10m> <texto> creates a reminder (restricted)\n"
    "/status shows Ollama, vault and history status (restricted)\n"
    "/reset clears your conversation history\n"
    "/end will say bye bye to you"
)

RATE_HITS = {}


def parse_allowed_ids(value):
    ids = set()
    for part in (value or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


ALLOWED_USER_IDS = parse_allowed_ids(os.environ.get("ALLOWED_USER_IDS"))


class History:
    def __init__(self, path):
        self.path = path
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "user_id INTEGER NOT NULL,"
                "role TEXT NOT NULL,"
                "content TEXT NOT NULL,"
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS summaries ("
                "user_id INTEGER PRIMARY KEY,"
                "content TEXT NOT NULL)"
            )

    def _connect(self):
        return sqlite3.connect(self.path)

    def add(self, user_id, role, content):
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )

    def recent(self, user_id, limit=HISTORY_LIMIT):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [{"role": role, "content": content} for role, content in reversed(rows)]

    def oldest(self, user_id, limit):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, role, content FROM messages WHERE user_id = ? ORDER BY id ASC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [{"id": row[0], "role": row[1], "content": row[2]} for row in rows]

    def delete_ids(self, ids):
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with closing(self._connect()) as connection, connection:
            connection.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", list(ids))

    def count(self, user_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row[0]

    def summary(self, user_id):
        if user_id is None:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT content FROM summaries WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row[0] if row else None

    def set_summary(self, user_id, content):
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO summaries (user_id, content) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET content = excluded.content",
                (user_id, content),
            )

    def clear(self, user_id):
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM summaries WHERE user_id = ?", (user_id,))


class ReminderStore:
    def __init__(self, path):
        self.path = path
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS reminders ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "user_id INTEGER NOT NULL,"
                "chat_id INTEGER NOT NULL,"
                "text TEXT NOT NULL,"
                "due_at REAL NOT NULL,"
                "sent INTEGER NOT NULL DEFAULT 0)"
            )

    def _connect(self):
        return sqlite3.connect(self.path)

    def add(self, user_id, chat_id, text, due_at):
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO reminders (user_id, chat_id, text, due_at) VALUES (?, ?, ?, ?)",
                (user_id, chat_id, text, due_at),
            )
            return cursor.lastrowid

    def pending(self):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, chat_id, text, due_at FROM reminders "
                "WHERE sent = 0 ORDER BY due_at"
            ).fetchall()
        return [
            {"id": row[0], "chat_id": row[1], "text": row[2], "due_at": row[3]} for row in rows
        ]

    def pop(self, reminder_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT chat_id, text FROM reminders WHERE id = ? AND sent = 0",
                (reminder_id,),
            ).fetchone()
        if not row:
            return None
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
        return {"chat_id": row[0], "text": row[1]}


HISTORY = History(DB_PATH)
REMINDERS = ReminderStore(DB_PATH)
DEALS = DealStore(DB_PATH)


def get_user_id(update):
    return update.effective_user.id if update.effective_user else None


def truncate(text, limit=REPLY_LIMIT):
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def format_number(value, decimals=2):
    try:
        number = f"{float(value):,.{decimals}f}"
        return number.replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(value)


def format_duration(seconds):
    seconds = int(seconds)
    if seconds >= 86400:
        return f"{seconds // 86400}d"
    if seconds >= 3600:
        return f"{seconds // 3600}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def parse_duration(value):
    match = DURATION_RE.match((value or "").lower())
    if not match:
        return None
    seconds = int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    if seconds <= 0 or seconds > REMINDER_MAX_SECONDS:
        return None
    return seconds


def check_rate_limit(user_id, limit=RATE_LIMIT_PER_MINUTE, window=60.0):
    if user_id is None:
        return False
    now = time.monotonic()
    hits = [hit for hit in RATE_HITS.get(user_id, []) if now - hit < window]
    if len(hits) >= limit:
        RATE_HITS[user_id] = hits
        return False
    hits.append(now)
    RATE_HITS[user_id] = hits
    return True


def parse_pairs(args):
    if not args:
        return list(DEFAULT_COINS)
    pairs = []
    for arg in args:
        pair = arg.upper().replace("/", "-")
        if "-" not in pair and len(pair) == 6:
            pair = f"{pair[:3]}-{pair[3:]}"
        elif "-" not in pair and len(pair) == 3:
            pair = f"{pair}-BRL"
        if not PAIR_RE.match(pair):
            return []
        pairs.append(pair)
    return pairs


def format_coin(data, pairs):
    lines = []
    for pair in pairs:
        quote_data = data.get(pair.replace("-", ""))
        if not quote_data:
            continue
        line = f"{quote_data.get('name', pair)}: R$ {format_number(quote_data.get('bid'))}"
        try:
            change = f"{float(quote_data['pctChange']):+.2f}%".replace(".", ",")
            line += f" ({change})"
        except (KeyError, TypeError, ValueError):
            pass
        lines.append(line)
    return "\n".join(lines)


def format_weather(place, data):
    current = data["current"]
    local = ", ".join(part for part in (place.get("name"), place.get("country")) if part)
    temperature = format_number(current["temperature_2m"], 1)
    apparent = format_number(current["apparent_temperature"], 1)
    description = WEATHER_CODES.get(current.get("weather_code"), "Condicao desconhecida")
    return (
        f"Clima em {local}\n"
        f"Temperatura: {temperature} C (sensacao {apparent} C)\n"
        f"{description}"
    )


def format_cep(data):
    return (
        f"CEP = {data['cep']}\n Address = {data['address']}\n State = {data['state']}\n"
        f" District = {data['district']}\n City = {data['city']}\n DDD = {data['ddd']}\n"
    )


def format_insult(data):
    return f"Insult: \n{data['insult']}"


def coin_keyboard(pairs):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Atualizar", callback_data="coin:" + ",".join(pairs))]]
    )


def history_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Limpar historico", callback_data="history:reset")]]
    )


def topic_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Outro tópico", callback_data="estudo:random")]]
    )


def deals_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Resumo com IA", callback_data="deals:summary")]]
    )


def list_topics(root=None):
    base = Path(root or ESTUDOS_PATH)
    if not base.is_dir():
        return []
    topics = []
    for path in sorted(base.rglob("*.md")):
        if path.is_symlink():
            continue
        relative = path.relative_to(base)
        if any(part.startswith(".") for part in relative.parts):
            continue
        topics.append(relative)
    return topics


def topic_title(relative, text):
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return relative.stem.replace("_", " ").replace("-", " ")


def load_topic(relative, root=None, limit=None):
    path = Path(root or ESTUDOS_PATH) / relative
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text[: (limit or TOPIC_LIMIT)]


def topic_title_for(relative):
    try:
        return topic_title(relative, load_topic(relative, limit=2000))
    except OSError:
        return relative.stem


async def run_topic_summary(placeholder, user_id):
    if not check_rate_limit(user_id):
        await safe_edit(
            placeholder,
            f"Rate limit: max {RATE_LIMIT_PER_MINUTE} perguntas por minuto. Tente em instantes.",
        )
        return
    topics = list_topics()
    if not topics:
        await safe_edit(
            placeholder,
            "Nenhum material de estudos encontrado. Monte o corpus em "
            "~/Projetos/estudos (ESTUDOS_PATH) e reinicie o bot.",
        )
        return
    relative = random.choice(topics)
    try:
        text = load_topic(relative)
    except OSError:
        await safe_edit(placeholder, "Não consegui ler o material de estudos agora.")
        return
    title = topic_title(relative, text)
    messages = [
        {"role": "system", "content": TOPIC_SYSTEM_PROMPT},
        {"role": "user", "content": f"{TOPIC_QUESTION}\n\n[Arquivo: {relative}]\n{text}"},
    ]
    parts = []
    last_edit = 0.0
    try:
        async for chunk in stream_chat(messages, OLLAMA_URL):
            parts.append(chunk)
            if time.monotonic() - last_edit >= EDIT_INTERVAL:
                last_edit = time.monotonic()
                await safe_edit(placeholder, f"Tópico: {title}\n\n" + "".join(parts))
    except (httpx.HTTPError, ValueError, KeyError):
        answer = "".join(parts).strip()
        await safe_edit(
            placeholder, answer or "Não consegui montar o resumo agora. Tente de novo."
        )
        return
    answer = "".join(parts).strip()
    if not answer:
        await safe_edit(placeholder, "Não consegui montar o resumo agora. Tente de novo.")
        return
    await safe_edit(placeholder, f"Tópico: {title}\n\n{answer}", reply_markup=topic_keyboard())


def normalize_cep(value):
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) == 8 else None


async def fetch_json(url, timeout=HTTP_TIMEOUT, transport=None):
    async with httpx.AsyncClient(trust_env=False, timeout=timeout, transport=transport) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def probe(url, timeout=HTTP_TIMEOUT):
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return True
    except httpx.HTTPError:
        return False


async def chat(messages, base_url, timeout=LLM_TIMEOUT):
    payload = {"model": OLLAMA_MODEL, "messages": messages, "stream": False}
    url = f"{base_url.rstrip('/')}/api/chat"
    async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()["message"]["content"].strip()


async def stream_chat(messages, base_url, timeout=LLM_TIMEOUT):
    payload = {"model": OLLAMA_MODEL, "messages": messages, "stream": True}
    url = f"{base_url.rstrip('/')}/api/chat"
    async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
        async with client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                content = (item.get("message") or {}).get("content") or ""
                if content:
                    yield content


async def service_status():
    status = {"ollama": False, "model": False, "vault": False}
    try:
        data = await fetch_json(f"{OLLAMA_URL}/api/tags")
        names = [item.get("name", "") for item in data.get("models", [])]
        status["ollama"] = True
        status["model"] = OLLAMA_MODEL in names
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    status["vault"] = await probe(f"{VAULT_PROXY_URL}/health")
    return status


def build_messages(user_id, question):
    prompt = SYSTEM_PROMPT
    summary = HISTORY.summary(user_id)
    if summary:
        prompt += f"\nResumo da conversa anterior: {summary}"
    messages = [{"role": "system", "content": prompt}]
    messages += HISTORY.recent(user_id)
    messages.append({"role": "user", "content": question})
    return messages


async def compact_history(user_id, base_url):
    if HISTORY.count(user_id) <= HISTORY_THRESHOLD:
        return
    old = HISTORY.oldest(user_id, HISTORY_THRESHOLD - HISTORY_LIMIT)
    if not old:
        return
    transcript = "\n".join(f"{item['role']}: {item['content']}" for item in old)
    prompt = (
        "Resuma em poucas frases, em portugues, os fatos e preferencias desta conversa. "
        "Nao invente nada."
    )
    previous = HISTORY.summary(user_id)
    if previous:
        prompt += f"\nResumo anterior: {previous}"
    try:
        summary = await chat(
            [{"role": "user", "content": f"{prompt}\n\n{transcript}"}], base_url
        )
    except (httpx.HTTPError, ValueError, KeyError):
        return
    HISTORY.delete_ids([item["id"] for item in old])
    HISTORY.set_summary(user_id, summary)


async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(truncate(text), reply_markup=reply_markup)
    except TelegramError:
        pass


def vault_allowed(update):
    if update.effective_chat.type != "private":
        return False, "Consulte suas notas apenas na conversa privada comigo."
    user_id = get_user_id(update)
    if not ALLOWED_USER_IDS:
        return False, "Vault access is not configured. Set ALLOWED_USER_IDS to enable it."
    if user_id is None or user_id not in ALLOWED_USER_IDS:
        return False, "You are not allowed to query the vault."
    return True, ""


def is_allowed(update):
    user_id = get_user_id(update)
    return bool(ALLOWED_USER_IDS) and user_id is not None and user_id in ALLOWED_USER_IDS


async def require_allowed(update):
    if is_allowed(update):
        return True
    await update.message.reply_text(
        "Acesso restrito. Envie /meuid e peça para o dono do bot liberar seu ID "
        "(ALLOWED_USER_IDS)."
    )
    return False


async def answer_with_llm(update, question, base_url, error_text):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use a conversa privada para manter seu histórico reservado.")
        return
    user_id = get_user_id(update)
    if user_id is None:
        await update.message.reply_text("This command is not available in this chat.")
        return
    if not check_rate_limit(user_id):
        await update.message.reply_text(
            f"Rate limit: max {RATE_LIMIT_PER_MINUTE} perguntas por minuto. Tente em instantes."
        )
        return
    messages = build_messages(user_id, question)
    placeholder = await update.message.reply_text("Pensando...")
    parts = []
    last_edit = 0.0
    try:
        async for chunk in stream_chat(messages, base_url):
            parts.append(chunk)
            if time.monotonic() - last_edit >= EDIT_INTERVAL:
                last_edit = time.monotonic()
                await safe_edit(placeholder, "".join(parts))
    except (httpx.HTTPError, ValueError, KeyError):
        answer = "".join(parts).strip()
        await safe_edit(placeholder, answer if answer else error_text)
        return
    answer = "".join(parts).strip()
    if not answer:
        await safe_edit(placeholder, error_text)
        return
    await safe_edit(placeholder, answer, reply_markup=history_keyboard())
    HISTORY.add(user_id, "user", question)
    HISTORY.add(user_id, "assistant", answer)
    await compact_history(user_id, base_url)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello World!")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await guia(update, context)


async def commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(COMMANDS_TEXT)


async def end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bye Bye!")


async def time_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tempo = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    await update.message.reply_text(f"Day and hour: \n{tempo}")


async def coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pairs = parse_pairs(context.args)
    if not pairs:
        await update.message.reply_text("Usage: /coin [USD] [EUR] ... e.g. /coin or /coin USD BTC")
        return
    try:
        data = await fetch_json(COIN_URL.format(pairs=",".join(pairs)))
    except (httpx.HTTPError, ValueError, KeyError):
        await update.message.reply_text("Could not fetch quotes right now. Try again later.")
        return
    text = format_coin(data, pairs) or "No quotes found for those codes."
    await update.message.reply_text(text, reply_markup=coin_keyboard(pairs))


async def coin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pairs = [pair for pair in query.data.removeprefix("coin:").split(",") if PAIR_RE.match(pair)]
    pairs = pairs or list(DEFAULT_COINS)
    try:
        data = await fetch_json(COIN_URL.format(pairs=",".join(pairs)))
        text = format_coin(data, pairs) or "No quotes found for those codes."
    except (httpx.HTTPError, ValueError, KeyError):
        text = "Could not fetch quotes right now. Try again later."
    try:
        await query.edit_message_text(text, reply_markup=coin_keyboard(pairs))
    except TelegramError:
        pass


async def history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    if user_id is not None:
        HISTORY.clear(user_id)
    await query.answer("Historico limpo.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass


async def cep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = normalize_cep(context.args[0]) if context.args else None
    if not code:
        await update.message.reply_text("Usage: /cep 01310100")
        return
    try:
        data = await fetch_json(CEP_URL.format(cep=code))
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            await update.message.reply_text("CEP not found. Check the code and try again.")
        else:
            await update.message.reply_text("Could not fetch that CEP right now. Try again later.")
        return
    except (httpx.HTTPError, ValueError, KeyError):
        await update.message.reply_text("Could not fetch that CEP right now. Try again later.")
        return
    await update.message.reply_text(format_cep(data))


async def clima(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = " ".join(context.args or []).strip()
    if not city:
        await update.message.reply_text("Usage: /clima <cidade> e.g. /clima Sao Paulo")
        return
    try:
        geo = await fetch_json(GEO_URL.format(city=quote(city)))
        results = geo.get("results") or []
        if not results:
            await update.message.reply_text("City not found. Try another name.")
            return
        place = results[0]
        weather = await fetch_json(
            WEATHER_URL.format(latitude=place["latitude"], longitude=place["longitude"])
        )
    except (httpx.HTTPError, ValueError, KeyError):
        await update.message.reply_text("Could not fetch the weather right now. Try again later.")
        return
    await update.message.reply_text(format_weather(place, weather))


async def insult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await fetch_json(INSULT_URL)
    except (httpx.HTTPError, ValueError, KeyError):
        await update.message.reply_text("Could not fetch an insult right now. Try again later.")
        return
    await update.message.reply_text(format_insult(data))


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_allowed(update):
        return
    question = " ".join(context.args or []).strip()
    if not question:
        await update.message.reply_text("Usage: /ask <question>")
        return
    await answer_with_llm(
        update, question, OLLAMA_URL, "Could not reach the local AI right now. Try again later."
    )


async def nota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, reason = vault_allowed(update)
    if not allowed:
        await update.message.reply_text(reason)
        return
    question = " ".join(context.args or []).strip()
    if not question:
        await update.message.reply_text("Usage: /nota <question>")
        return
    await answer_with_llm(
        update, question, VAULT_PROXY_URL, "Could not reach the vault right now. Try again later."
    )


async def estudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_allowed(update):
        return
    question = " ".join(context.args or []).strip()
    if question:
        await answer_with_llm(
            update,
            question,
            ESTUDOS_PROXY_URL,
            "Could not reach the study tutor right now. Try again later.",
        )
        return
    placeholder = await update.message.reply_text("Sorteando um tópico...")
    await run_topic_summary(placeholder, get_user_id(update))


async def estudo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_allowed(update):
        await query.answer("Acesso restrito.", show_alert=True)
        return
    await query.answer()
    await safe_edit(query.message, "Sorteando outro tópico...")
    await run_topic_summary(query.message, query.from_user.id)


async def topicos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_allowed(update):
        return
    topics = list_topics()
    if not topics:
        await update.message.reply_text(
            "Nenhum material de estudos encontrado em ESTUDOS_PATH."
        )
        return
    lines = [f"- {topic_title_for(topic)}" for topic in topics]
    text = "Tópicos disponíveis:\n" + "\n".join(lines)
    text += "\n\nUse /estudo <pergunta> para perguntar ou /estudo sem argumento para sortear."
    await update.message.reply_text(truncate(text))


async def resumo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, reason = vault_allowed(update)
    if not allowed:
        await update.message.reply_text(reason)
        return
    await answer_with_llm(
        update,
        RESUMO_QUESTION,
        VAULT_PROXY_URL,
        "Could not reach the vault right now. Try again later.",
    )


async def capture_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat.type not in ("group", "supergroup"):
        return
    deal = parse_deal(message.text or "")
    if not deal:
        return
    if not DEALS.add(message.chat.id, message.message_id, deal):
        return
    if DEALS_ALERT_PERCENT <= 0 or not deal.get("discount"):
        return
    if deal["discount"] < DEALS_ALERT_PERCENT:
        return
    for user_id in ALLOWED_USER_IDS:
        try:
            await context.bot.send_message(user_id, format_deal_alert(deal))
        except TelegramError:
            pass


async def ofertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_allowed(update):
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use a conversa privada para ver suas ofertas.")
        return
    term = " ".join(context.args or []).strip() or None
    deals = DEALS.top(limit=DEALS_TOP, hours=DEALS_WINDOW_HOURS, term=term)
    if not deals:
        await update.message.reply_text(
            f"Nenhuma oferta capturada nas últimas {DEALS_WINDOW_HOURS}h. "
            "Adicione o bot aos grupos de promoções."
        )
        return
    blocks = [format_deal(deal, index) for index, deal in enumerate(deals, start=1)]
    text = f"Melhores ofertas (últimas {DEALS_WINDOW_HOURS}h, por desconto):\n\n"
    text += "\n\n".join(blocks)
    await update.message.reply_text(truncate(text), reply_markup=deals_keyboard())


async def deals_summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_allowed(update):
        await query.answer("Acesso restrito.", show_alert=True)
        return
    user_id = query.from_user.id if query.from_user else None
    if not check_rate_limit(user_id):
        await query.answer("Muitas solicitações. Aguarde um minuto.", show_alert=True)
        return
    deals = DEALS.top(limit=DEALS_TOP, hours=DEALS_WINDOW_HOURS)
    if not deals:
        await query.answer("Sem ofertas para resumir.", show_alert=True)
        return
    await query.answer()
    message = query.message
    await safe_edit(message, "Resumindo com a IA local...")
    parts = []
    last_edit = 0.0
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": deals_summary_prompt(deals)},
    ]
    try:
        async for chunk in stream_chat(messages, OLLAMA_URL):
            parts.append(chunk)
            if time.monotonic() - last_edit >= EDIT_INTERVAL:
                last_edit = time.monotonic()
                await safe_edit(message, "Resumo IA:\n\n" + "".join(parts))
    except (httpx.HTTPError, ValueError, KeyError):
        await safe_edit(message, "Não consegui resumir as ofertas agora. Tente de novo.")
        return
    answer = "".join(parts).strip()
    final = ("Resumo IA:\n\n" + answer) if answer else "Não consegui resumir as ofertas agora."
    await safe_edit(message, final)


async def lembrete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_allowed(update):
        return
    seconds = parse_duration(context.args[0]) if context.args else None
    text = " ".join(context.args[1:]).strip() if context.args else ""
    if not seconds or not text:
        await update.message.reply_text("Usage: /lembrete 10m tomar agua (s/m/h/d)")
        return
    reminder_id = REMINDERS.add(
        get_user_id(update), update.effective_chat.id, text, time.time() + seconds
    )
    context.job_queue.run_once(
        send_reminder, when=seconds, data=reminder_id, name=f"reminder:{reminder_id}"
    )
    await update.message.reply_text(f"Lembrete em {format_duration(seconds)}: {text}")


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    reminder = REMINDERS.pop(context.job.data)
    if reminder:
        await context.bot.send_message(reminder["chat_id"], f"Lembrete: {reminder['text']}")


def reschedule_reminders(application):
    if application.job_queue is None:
        return
    now = time.time()
    for reminder in REMINDERS.pending():
        delay = max(reminder["due_at"] - now, 1.0)
        application.job_queue.run_once(
            send_reminder, when=delay, data=reminder["id"], name=f"reminder:{reminder['id']}"
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_allowed(update):
        return
    info = await service_status()
    user_id = get_user_id(update)
    history_count = HISTORY.count(user_id) if user_id is not None else 0
    lines = [
        f"Ollama: {'online' if info['ollama'] else 'offline'} ({OLLAMA_URL})",
        f"Modelo {OLLAMA_MODEL}: {'disponivel' if info['model'] else 'nao encontrado'}",
        f"Vault: {'online' if info['vault'] else 'offline'} ({VAULT_PROXY_URL})",
        f"Historico deste usuario: {history_count} mensagens",
        f"Lembretes pendentes: {len(REMINDERS.pending())}",
        f"Uptime: {format_duration(time.monotonic() - START_TIME)}",
    ]
    await update.message.reply_text("\n".join(lines))


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    if user_id is not None:
        HISTORY.clear(user_id)
    await update.message.reply_text("Conversation history cleared.")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat.type != "private":
        username = context.bot.username
        if not username or f"@{username.lower()}" not in (message.text or "").lower():
            return
    await message.reply_text(message.text)


async def error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning("Falha no processamento: %s", type(context.error).__name__)


async def post_init(application):
    try:
        await application.bot.set_my_commands(
            BOT_COMMANDS + application.bot_data.get("extra_commands", [])
        )
    except TelegramError:
        logger.warning("Could not register bot commands")
    reschedule_reminders(application)


def build_application(token):
    application = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("commands", commands))
    application.add_handler(CommandHandler("end", end))
    application.add_handler(CommandHandler("time", time_command))
    application.add_handler(CommandHandler("coin", coin))
    application.add_handler(CommandHandler("cep", cep))
    application.add_handler(CommandHandler("clima", clima))
    application.add_handler(CommandHandler("insult", insult))
    application.add_handler(CommandHandler("ask", ask))
    application.add_handler(CommandHandler("nota", nota))
    application.add_handler(CommandHandler("estudo", estudo))
    application.add_handler(CommandHandler("topicos", topicos))
    application.add_handler(CommandHandler("ofertas", ofertas))
    application.add_handler(CommandHandler("resumo", resumo))
    application.add_handler(CommandHandler("lembrete", lembrete))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("reset", reset))
    async def translate_answer(update, prompt):
        await answer_with_llm(update, prompt, OLLAMA_URL, "IA local indisponível. Tente novamente.")

    application.bot_data["extra_commands"] = register_extras(
        application, allowed_ids=ALLOWED_USER_IDS,
        rate_limit=check_rate_limit, answer=translate_answer,
    )
    application.add_handler(CallbackQueryHandler(coin_callback, pattern=r"^coin:"))
    application.add_handler(
        CallbackQueryHandler(history_callback, pattern=r"^history:reset$")
    )
    application.add_handler(
        CallbackQueryHandler(estudo_callback, pattern=r"^estudo:random$")
    )
    application.add_handler(
        CallbackQueryHandler(deals_summary_callback, pattern=r"^deals:summary$")
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, capture_deal)
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    application.add_error_handler(error)
    return application


def main():
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"Set {TOKEN_ENV} in the environment before running the bot.")
    if ":" not in token:
        raise SystemExit(
            f"{TOKEN_ENV} looks invalid. Use the BotFather token (123456:AA...), not a user ID."
        )
    try:
        build_application(token).run_polling()
    except InvalidToken:
        raise SystemExit(
            f"Telegram rejected {TOKEN_ENV}. Get a fresh token from @BotFather and update .env."
        ) from None


if __name__ == "__main__":
    main()
