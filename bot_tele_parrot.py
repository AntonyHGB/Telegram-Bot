import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
HTTP_TIMEOUT = 10.0
LLM_TIMEOUT = 120.0
HISTORY_LIMIT = 8
REPLY_LIMIT = 4000

COIN_URL = "https://economia.awesomeapi.com.br/last/{pairs}"
DEFAULT_COINS = ("USD-BRL", "EUR-BRL", "BTC-BRL")
CEP_URL = "https://cep.awesomeapi.com.br/json/{cep}"
INSULT_URL = "https://evilinsult.com/generate_insult.php?lang=en&type=json"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
VAULT_PROXY_URL = os.environ.get("VAULT_PROXY_URL", "http://127.0.0.1:11435")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
DB_PATH = os.environ.get("BOT_DB_PATH", "bot_history.db")

SYSTEM_PROMPT = "You are a helpful assistant. Answer in Brazilian Portuguese, concisely."

PAIR_RE = re.compile(r"^[A-Z]{3}-[A-Z]{3}$")

COMMANDS_TEXT = (
    "/start will say Hello World to you\n"
    "/help will not help you\n"
    "/commands you already understand\n"
    "/time will show the hour to you\n"
    "/coin [USD] will show quotes in Real (default: USD, EUR, BTC)\n"
    "/cep <codigo> will show more information about your cep\n"
    "/insult will insult you\n"
    "/ask <question> asks the local AI (Ollama)\n"
    "/nota <question> asks using your Obsidian vault (restricted)\n"
    "/reset clears your conversation history\n"
    "/end will say bye bye to you"
)


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

    def clear(self, user_id):
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))


HISTORY = History(DB_PATH)


def get_user_id(update):
    return update.effective_user.id if update.effective_user else None


def truncate(text, limit=REPLY_LIMIT):
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def format_number(value):
    try:
        return f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(value)


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
        quote = data.get(pair.replace("-", ""))
        if not quote:
            continue
        line = f"{quote.get('name', pair)}: R$ {format_number(quote.get('bid'))}"
        try:
            change = f"{float(quote['pctChange']):+.2f}%".replace(".", ",")
            line += f" ({change})"
        except (KeyError, TypeError, ValueError):
            pass
        lines.append(line)
    return "\n".join(lines)


def format_cep(data):
    return (
        f"CEP = {data['cep']}\n Address = {data['address']}\n State = {data['state']}\n"
        f" District = {data['district']}\n City = {data['city']}\n DDD = {data['ddd']}\n"
    )


def format_insult(data):
    return f"Insult: \n{data['insult']}"


def normalize_cep(value):
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) == 8 else None


async def fetch_json(url, timeout=HTTP_TIMEOUT, transport=None):
    async with httpx.AsyncClient(trust_env=False, timeout=timeout, transport=transport) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def chat(messages, base_url, timeout=LLM_TIMEOUT):
    payload = {"model": OLLAMA_MODEL, "messages": messages, "stream": False}
    url = f"{base_url.rstrip('/')}/api/chat"
    async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()["message"]["content"].strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello World!")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Nobody will help you!")


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
    await update.message.reply_text(format_coin(data, pairs) or "No quotes found for those codes.")


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


async def insult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await fetch_json(INSULT_URL)
    except (httpx.HTTPError, ValueError, KeyError):
        await update.message.reply_text("Could not fetch an insult right now. Try again later.")
        return
    await update.message.reply_text(format_insult(data))


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args or []).strip()
    if not question:
        await update.message.reply_text("Usage: /ask <question>")
        return
    user_id = get_user_id(update)
    if user_id is None:
        await update.message.reply_text("This command is not available in this chat.")
        return
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += HISTORY.recent(user_id)
    messages.append({"role": "user", "content": question})
    try:
        answer = await chat(messages, OLLAMA_URL)
    except (httpx.HTTPError, ValueError, KeyError):
        await update.message.reply_text("Could not reach the local AI right now. Try again later.")
        return
    HISTORY.add(user_id, "user", question)
    HISTORY.add(user_id, "assistant", answer)
    await update.message.reply_text(truncate(answer))


async def nota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    if not ALLOWED_USER_IDS:
        await update.message.reply_text(
            "Vault access is not configured. Set ALLOWED_USER_IDS to enable /nota."
        )
        return
    if user_id is None or user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("You are not allowed to query the vault.")
        return
    question = " ".join(context.args or []).strip()
    if not question:
        await update.message.reply_text("Usage: /nota <question>")
        return
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += HISTORY.recent(user_id)
    messages.append({"role": "user", "content": question})
    try:
        answer = await chat(messages, VAULT_PROXY_URL)
    except (httpx.HTTPError, ValueError, KeyError):
        await update.message.reply_text("Could not reach the vault right now. Try again later.")
        return
    HISTORY.add(user_id, "user", question)
    HISTORY.add(user_id, "assistant", answer)
    await update.message.reply_text(truncate(answer))


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update)
    if user_id is not None:
        HISTORY.clear(user_id)
    await update.message.reply_text("Conversation history cleared.")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(update.message.text)


async def error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def build_application(token):
    application = Application.builder().token(token).concurrent_updates(True).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("commands", commands))
    application.add_handler(CommandHandler("end", end))
    application.add_handler(CommandHandler("time", time_command))
    application.add_handler(CommandHandler("coin", coin))
    application.add_handler(CommandHandler("cep", cep))
    application.add_handler(CommandHandler("insult", insult))
    application.add_handler(CommandHandler("ask", ask))
    application.add_handler(CommandHandler("nota", nota))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    application.add_error_handler(error)
    return application


def main():
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"Set {TOKEN_ENV} in the environment before running the bot.")
    build_application(token).run_polling()


if __name__ == "__main__":
    main()
