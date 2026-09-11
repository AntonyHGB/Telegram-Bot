"""User-facing commands kept separate from the original bot module."""

import asyncio
import tempfile
from pathlib import Path

import httpx
from telegram import BotCommand
from telegram.ext import CommandHandler, MessageHandler, filters

from integrations import NotionConfigurationError, create_task, speech


GUIDE = """O que posso fazer por você

Tudo que usa IA, suas notas, voz ou tarefas exige acesso liberado
(ALLOWED_USER_IDS). Use /meuid para pegar seu ID e pedir ao dono do bot.

IA local (restrito)
/ask explique listas em Python — conversar com a IA
/estudo o que é modelagem dimensional? — consultar o tutor de estudos
/traduzir inglês Bom dia — traduzir um texto
Envie uma mensagem de voz — receber a transcrição (até 2 minutos)
/reset — apagar seu histórico e resumo da conversa

Organização (restrito)
/lembrete 10m tomar água — agendar um aviso
/tarefa Revisar currículo — criar uma tarefa no Notion configurado

Suas notas (restrito, conversa privada)
/nota o que anotei sobre estudos? — consultar o vault
/resumo — pedir um resumo das notas de diário

Consultas (públicas)
/coin USD EUR BTC — cotações, com botão Atualizar
/cep 01310100 — endereço de um CEP
/clima São Paulo — temperatura e condições atuais
/time — data e hora

Ajuda
/guia — este resumo
/meuid — seu ID para pedir acesso
/status — verificar os serviços (restrito)
/commands — lista de comandos
/insult — comando de humor com conteúdo externo

/tarefa precisa da integração do Notion configurada.
A IA pode errar: confira informações importantes e fontes citadas."""


async def guia(update, context):
    await update.message.reply_text(GUIDE)


async def meuid(update, context):
    if update.effective_user:
        await update.message.reply_text(f"Seu ID do Telegram: {update.effective_user.id}")


def register_extras(application, *, allowed_ids, rate_limit, answer):
    voice_gate = asyncio.Semaphore(1)

    async def authorize(update):
        if update.effective_chat.type != "private":
            await update.message.reply_text("Use este comando na conversa privada comigo.")
            return False
        if not update.effective_user or update.effective_user.id not in allowed_ids:
            await update.message.reply_text("Acesso restrito aos IDs em ALLOWED_USER_IDS.")
            return False
        return True

    async def tarefa(update, context):
        if not await authorize(update):
            return
        title = " ".join(context.args).strip()
        if not title or len(title) > 500:
            await update.message.reply_text("Use /tarefa título da tarefa (até 500 caracteres).")
            return
        if not rate_limit(update.effective_user.id):
            await update.message.reply_text("Muitas solicitações. Aguarde um minuto.")
            return
        try:
            url = await create_task(title)
        except NotionConfigurationError as error:
            await update.message.reply_text(str(error))
        except (httpx.HTTPError, ValueError, KeyError):
            await update.message.reply_text(
                "Não consegui confirmar a criação no Notion. Verifique a base antes de repetir "
                "para evitar duplicatas. Confira também a credencial e as permissões."
            )
        else:
            await update.message.reply_text(f"Tarefa criada no Notion:\n{url}")

    async def traduzir(update, context):
        if len(context.args) < 2:
            await update.message.reply_text("Use /traduzir inglês Bom dia")
            return
        prompt = (
            f"Traduza para {context.args[0]}, retornando apenas a tradução do texto a seguir:\n"
            + " ".join(context.args[1:])
        )
        await answer(update, prompt)

    async def voice(update, context):
        if not await authorize(update):
            return
        audio = update.message.voice
        duration = audio.duration
        seconds = duration.total_seconds() if hasattr(duration, "total_seconds") else duration
        if seconds > 120 or audio.file_size is None or audio.file_size > 10 * 1024 * 1024:
            await update.message.reply_text("Envie uma voz de até 2 minutos e 10 MB.")
            return
        if voice_gate.locked():
            await update.message.reply_text("Já estou transcrevendo um áudio. Tente em instantes.")
            return
        if not rate_limit(update.effective_user.id):
            await update.message.reply_text("Muitas solicitações. Aguarde um minuto.")
            return
        async with voice_gate:
            await update.message.reply_text("Transcrevendo em CPU…")
            try:
                with tempfile.TemporaryDirectory(prefix="telegram-voice-") as directory:
                    path = Path(directory) / "voice.ogg"
                    file = await audio.get_file()
                    await file.download_to_drive(custom_path=path)
                    text = await asyncio.to_thread(speech.transcribe, str(path))
            except Exception:
                # Third-party exceptions may contain Telegram download URLs with the token.
                await update.message.reply_text(
                    "Não consegui transcrever. Confira a instalação de voz/modelo e tente novamente."
                )
                return
            if not text:
                await update.message.reply_text("Não identifiquei fala nesse áudio.")
                return
            # No silent truncation and no automatic forwarding to Notion or the LLM.
            for offset in range(0, len(text), 3500):
                await update.message.reply_text(text[offset:offset + 3500])

    commands = [
        ("guia", guia, "Resumo do que posso fazer, com exemplos"),
        ("meuid", meuid, "Mostra seu ID do Telegram"),
        ("traduzir", traduzir, "Traduzir com a IA local"),
        ("tarefa", tarefa, "Criar uma tarefa no Notion (restrito)"),
    ]
    for name, handler, _ in commands:
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(MessageHandler(filters.VOICE, voice))
    return [BotCommand(name, description) for name, _, description in commands]
