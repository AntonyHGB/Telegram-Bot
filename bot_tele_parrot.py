import logging
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, InlineQueryHandler
from datetime import datetime
import urllib.request, json
import requests
import re


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

def start(update, context):
    update.message.reply_text('Hello World!')


def help(update, context):
    update.message.reply_text('Nobody will help you!')


def commands(update, context):
    update.message.reply_text(f'''
    /start will say Hello World to you \n
    /help will not help you \n
    /commands you already understand \n
    /time will show the hour to you \n
    /coin will show BTC in Real \n
    /cep will show more information about your cep \n
    /insult will insult you \n
    /end will say bye bye to you \n ''')


def end(update, context):
    update.message.reply_text('Bye Bye!')


def time(update, context):
    tempo = datetime.now().strftime('%d-%m-%Y %H:%M:%S')
    update.message.reply_text(f'Day and hour: \n{tempo}')


def coin(update, context):
    with urllib.request.urlopen("https://economia.awesomeapi.com.br/last/BTC-BRL") as url:
        data = json.loads(url.read().decode())
        coin = data['BTCBRL']["name"]
        value_coin = data['BTCBRL']["high"]
        update.message.reply_text(f"Coins = {coin} \n Max Value in past 24 hours= {value_coin}")

def cep(update, context):
    with urllib.request.urlopen("https://cep.awesomeapi.com.br/json/13313129") as url:
        data = json.loads(url.read().decode())
        numbers_cep = data["cep"]
        address = data["address"]
        state = data["state"]
        district = data["district"]
        city = data["city"]
        ddd = data["ddd"]
        update.message.reply_text(f'''CEP = {numbers_cep} \n Address = {address} \n State = {state}\n District = {district}\n City = {city}\n DDD = {ddd}\n''')

def insult(update, context):
    with urllib.request.urlopen("https://evilinsult.com/generate_insult.php?lang=en&type=json") as url:
        data = json.loads(url.read().decode())
        sentence = data["insult"]
        update.message.reply_text(f"Insult: \n{sentence}")

def echo(update, context):
    update.message.reply_text(update.message.text)


def error(update, context):
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def main():
    updater = Updater("Token", use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help))
    dp.add_handler(CommandHandler("commands", commands))
    dp.add_handler(CommandHandler("end", end))
    dp.add_handler(CommandHandler("time", time))
    dp.add_handler(CommandHandler("coin", coin))
    dp.add_handler(CommandHandler("cep", cep))
    dp.add_handler(CommandHandler("insult", insult))

    dp.add_handler(MessageHandler(Filters.text, echo))
    dp.add_error_handler(error)

    updater.start_polling()

    updater.idle()


if __name__ == '__main__':
    main()