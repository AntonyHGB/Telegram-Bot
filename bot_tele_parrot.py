import logging
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters


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
    update.message.reply_text('/end will say bye bye to you')

def end(update, context):
    update.message.reply_text('Bye Bye!')


def echo(update, context):
    update.message.reply_text(update.message.text)


def error(update, context):
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def main():
    updater = Updater("token_telegram", use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help))

    dp.add_handler(MessageHandler(Filters.text, echo))
    dp.add_error_handler(error)

    updater.start_polling()

    updater.idle()


if __name__ == '__main__':
    main()