import os
import io
import base64
import logging
import tempfile
import subprocess

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic
import speech_recognition as sr

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = (
    "You are a translator. Detect the language of the user's message. "
    "If the text is in Russian — translate it into natural, fluent English. "
    "If the text is in English — translate it into natural, fluent Russian. "
    "If the text is in any other language — translate it into English. "
    "Reply with ONLY the translation — no explanations, no notes, "
    "no extra text. Just the translation."
)

PHOTO_PROMPT = (
    "You are a translator. Look at this image and find any text in it. "
    "If the text is in Russian — translate it into English. "
    "If the text is in English — translate it into Russian. "
    "If the text is in any other language — translate it into English. "
    "Reply with ONLY the translation — no explanations, no notes. "
    "If there is no text in the image, reply: 'No text found in the image.'"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я переводчик с ИИ. Отправь мне:\n\n"
        "📝 Текст на русском → английский\n"
        "📝 Текст на английском → русский\n"
        "🎤 Голосовое сообщение — распознаю и переведу\n"
        "📷 Фото с текстом — прочитаю и переведу"
    )


async def translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    if not user_text:
        return

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
        translation = response.content[0].text
        await update.message.reply_text(translation)
    except Exception as e:
        logger.error("Translation error: %s", e)
        await update.message.reply_text("Ошибка при переводе. Попробуйте ещё раз.")


async def translate_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        voice = update.message.voice or update.message.audio
        if not voice:
            return
        voice_file = await context.bot.get_file(voice.file_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            ogg_path = os.path.join(tmpdir, "voice.ogg")
            wav_path = os.path.join(tmpdir, "voice.wav")

            await voice_file.download_to_drive(ogg_path)
            logger.info("Downloaded voice: %d bytes", os.path.getsize(ogg_path))

            result = subprocess.run(
                ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
                capture_output=True,
            )
            if result.returncode != 0:
                logger.error("ffmpeg error: %s", result.stderr.decode())
                await update.message.reply_text("Ошибка конвертации аудио.")
                return

            logger.info("Converted to WAV: %d bytes", os.path.getsize(wav_path))

            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio = recognizer.record(source)

            try:
                text = recognizer.recognize_google(audio, language="ru-RU")
            except sr.UnknownValueError:
                try:
                    text = recognizer.recognize_google(audio, language="en-US")
                except sr.UnknownValueError:
                    await update.message.reply_text("Не удалось распознать речь. Попробуйте говорить чётче.")
                    return

        logger.info("Recognized: %s", text)

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        translation = response.content[0].text
        await update.message.reply_text(f"🎤 {text}\n\n📝 {translation}")

    except Exception as e:
        logger.error("Voice error: %s", e, exc_info=True)
        await update.message.reply_text(f"DEBUG: {type(e).__name__}: {e}")


async def translate_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        photo = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)

        photo_bytes = io.BytesIO()
        await photo_file.download_to_memory(photo_bytes)
        photo_bytes.seek(0)

        base64_image = base64.standard_b64encode(photo_bytes.read()).decode("utf-8")

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64_image,
                            },
                        },
                        {"type": "text", "text": PHOTO_PROMPT},
                    ],
                }
            ],
        )
        translation = response.content[0].text
        await update.message.reply_text(translation)

    except Exception as e:
        logger.error("Photo translation error: %s", e)
        await update.message.reply_text("Ошибка при обработке фото. Попробуйте ещё раз.")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, translate_voice))
    app.add_handler(MessageHandler(filters.PHOTO, translate_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
