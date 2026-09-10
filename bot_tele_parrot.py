import logging
import os
import re
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
COIN_URL = "https://economia.awesomeapi.com.br/last/BTC-BRL"
CEP_URL = "https://cep.awesomeapi.com.br/json/{cep}"
INSULT_URL = "https://evilinsult.com/generate_insult.php?lang=en&type=json"

COMMANDS_TEXT = (
    "/start will say Hello World to you\n"
    "/help will not help you\n"
    "/commands you already understand\n"
    "/time will show the hour to you\n"
    "/coin will show BTC in Real\n"
    "/cep <codigo> will show more information about your cep\n"
    "/insult will insult you\n"
    "/end will say bye bye to you"
)


def format_coin(data):
    coin = data["BTCBRL"]["name"]
    value_coin = data["BTCBRL"]["high"]
    return f"Coins = {coin}\n Max Value in past 24 hours= {value_coin}"


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


async def fetch_json(url):
    async with httpx.AsyncClient(trust_env=False, timeout=HTTP_TIMEOUT) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


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
    try:
        data = await fetch_json(COIN_URL)
    except (httpx.HTTPError, ValueError, KeyError):
        await update.message.reply_text("Could not fetch BTC quote right now. Try again later.")
        return
    await update.message.reply_text(format_coin(data))


async def cep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = normalize_cep(context.args[0]) if context.args else None
    if not code:
        await update.message.reply_text("Usage: /cep 01310100")
        return
    try:
        data = await fetch_json(CEP_URL.format(cep=code))
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


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(update.message.text)


async def error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def build_application(token):
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("commands", commands))
    application.add_handler(CommandHandler("end", end))
    application.add_handler(CommandHandler("time", time_command))
    application.add_handler(CommandHandler("coin", coin))
    application.add_handler(CommandHandler("cep", cep))
    application.add_handler(CommandHandler("insult", insult))
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
