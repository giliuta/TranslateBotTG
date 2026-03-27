import os
import logging

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic

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
    "You are a translator. The user sends you text in Russian. "
    "Your only job is to translate it into natural, fluent English. "
    "Reply with ONLY the English translation — no explanations, no notes, "
    "no extra text. Just the translation."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Отправь мне сообщение на русском, и я переведу его на английский."
    )


async def translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    if not user_text:
        return

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
        translation = response.content[0].text
        await update.message.reply_text(translation)
    except Exception as e:
        logger.error("Translation error: %s", e)
        await update.message.reply_text("Ошибка при переводе. Попробуйте ещё раз.")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
