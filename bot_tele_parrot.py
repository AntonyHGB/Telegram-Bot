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
    update.message.reply_text('/start will say Hello World to you')
    update.message.reply_text('/help will not help you')
    update.message.reply_text('/commands you already understand')
    update.message.reply_text('/time will show the hour to you')
    update.message.reply_text('/coin will show BTC in Real')
    update.message.reply_text('/end will say bye bye to you')


def end(update, context):
    update.message.reply_text('Bye Bye!')


def time(update, context):
    tempo = datetime.now().strftime('%d-%m-%Y %H:%M:%S')
    update.message.reply_text(f'Dia e hora atual: \n{tempo}')


def coin(update, context):
    with urllib.request.urlopen("https://economia.awesomeapi.com.br/last/BTC-BRL") as url:
        data = json.loads(url.read().decode())
        #data_max = data['high']
        #print(contatos['Ana'])
        #x = data['high']
        update.message.reply_text(f'texto : {data["high"]}')

def echo(update, context):
    update.message.reply_text(update.message.text)


def error(update, context):
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def main():
    updater = Updater("1691021481:AAGAj779JAN77DE422eimpoERD14JuykAHg", use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help))
    dp.add_handler(CommandHandler("commands", commands))
    dp.add_handler(CommandHandler("end", end))
    dp.add_handler(CommandHandler("time", time))
    dp.add_handler(CommandHandler("coin", coin))

    dp.add_handler(MessageHandler(Filters.text, echo))
    dp.add_error_handler(error)

    updater.start_polling()

    updater.idle()


if __name__ == '__main__':
    main()