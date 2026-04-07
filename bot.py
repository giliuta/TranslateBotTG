import os
import io
import base64
import logging
import tempfile
import wave
import struct
from collections import defaultdict

import av
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic
import speech_recognition as sr

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MAX_CONTEXT_MESSAGES = 10

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Context memory: stores last messages per user
user_context: dict[int, list[dict]] = defaultdict(list)

SYSTEM_PROMPT = (
    "You are a translator. Detect the language of the user's message. "
    "If the text is in Russian — translate it into natural, fluent English. "
    "If the text is in English — translate it into natural, fluent Russian. "
    "If the text is in any other language — translate it into English. "
    "Reply with ONLY the translation — no explanations, no notes, "
    "no extra text. Just the translation. "
    "Use the conversation history for context to produce more accurate translations "
    "(e.g. resolving pronouns, continuing topics)."
)

PHOTO_PROMPT = (
    "You are a translator. Look at this image and find any text in it. "
    "If the text is in Russian — translate it into English. "
    "If the text is in English — translate it into Russian. "
    "If the text is in any other language — translate it into English. "
    "Reply with ONLY the translation — no explanations, no notes. "
    "If there is no text in the image, reply: 'No text found in the image.'"
)

DOC_PROMPT = (
    "You are a translator. Translate the following document. "
    "Detect the language: if Russian — translate to English, "
    "if English — translate to Russian, otherwise translate to English. "
    "Preserve the original formatting (paragraphs, lists, etc.). "
    "Reply with ONLY the translation."
)


def add_to_context(user_id: int, role: str, text: str) -> None:
    """Add a message to user's context history."""
    user_context[user_id].append({"role": role, "content": text})
    # Keep only last N messages
    if len(user_context[user_id]) > MAX_CONTEXT_MESSAGES:
        user_context[user_id] = user_context[user_id][-MAX_CONTEXT_MESSAGES:]


def get_context_messages(user_id: int, current_text: str) -> list[dict]:
    """Get conversation history + current message for Claude API."""
    messages = list(user_context[user_id])
    messages.append({"role": "user", "content": current_text})
    return messages


def ogg_to_wav(ogg_path: str, wav_path: str) -> bool:
    """Convert OGG Opus to WAV using PyAV (no system ffmpeg needed)."""
    try:
        container = av.open(ogg_path)
        stream = container.streams.audio[0]

        resampler = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=16000,
        )

        samples = []
        for frame in container.decode(stream):
            resampled = resampler.resample(frame)
            for r in resampled:
                arr = r.to_ndarray()
                samples.extend(arr.flatten().tolist())

        container.close()

        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

        return True
    except Exception as e:
        logger.error("OGG to WAV error: %s", e, exc_info=True)
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я переводчик с ИИ. Отправь мне:\n\n"
        "📝 Текст — переведу RU↔EN автоматически\n"
        "🎤 Голосовое — распознаю и переведу\n"
        "📷 Фото с текстом — прочитаю и переведу\n"
        "📄 Документ (.txt, .pdf) — переведу весь файл\n\n"
        "Я помню контекст последних сообщений для точного перевода.\n"
        "/clear — очистить контекст"
    )


async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_context[user_id].clear()
    await update.message.reply_text("Контекст очищен.")


async def translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    if not user_text:
        return

    user_id = update.effective_user.id

    try:
        messages = get_context_messages(user_id, user_text)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        translation = response.content[0].text

        # Save to context
        add_to_context(user_id, "user", user_text)
        add_to_context(user_id, "assistant", translation)

        await update.message.reply_text(translation)
    except Exception as e:
        logger.error("Translation error: %s", e)
        await update.message.reply_text(f"DEBUG: {type(e).__name__}: {e}")


async def translate_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        voice = update.message.voice or update.message.audio
        if not voice:
            return
        voice_file = await context.bot.get_file(voice.file_id)
        user_id = update.effective_user.id

        with tempfile.TemporaryDirectory() as tmpdir:
            ogg_path = os.path.join(tmpdir, "voice.ogg")
            wav_path = os.path.join(tmpdir, "voice.wav")

            await voice_file.download_to_drive(ogg_path)
            logger.info("Downloaded voice: %d bytes", os.path.getsize(ogg_path))

            if not ogg_to_wav(ogg_path, wav_path):
                await update.message.reply_text("Ошибка конвертации аудио.")
                return

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

        messages = get_context_messages(user_id, text)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        translation = response.content[0].text

        add_to_context(user_id, "user", text)
        add_to_context(user_id, "assistant", translation)

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
        await update.message.reply_text(f"DEBUG photo: {type(e).__name__}: {e}")


async def translate_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        doc = update.message.document
        if not doc:
            return

        file_name = doc.file_name or "file"
        file_ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

        if file_ext not in ("txt", "pdf", "md", "csv", "html", "json"):
            await update.message.reply_text(
                "Поддерживаемые форматы: .txt, .pdf, .md, .csv, .html, .json"
            )
            return

        if doc.file_size > 1_000_000:  # 1MB limit
            await update.message.reply_text("Файл слишком большой. Максимум 1MB.")
            return

        doc_file = await context.bot.get_file(doc.file_id)
        file_bytes = io.BytesIO()
        await doc_file.download_to_memory(file_bytes)
        file_bytes.seek(0)

        if file_ext == "pdf":
            # Read PDF text
            try:
                import fitz  # PyMuPDF
                pdf_doc = fitz.open(stream=file_bytes.read(), filetype="pdf")
                text_parts = []
                for page in pdf_doc:
                    text_parts.append(page.get_text())
                pdf_doc.close()
                doc_text = "\n".join(text_parts)
            except ImportError:
                await update.message.reply_text("PDF поддержка не установлена.")
                return
        else:
            doc_text = file_bytes.read().decode("utf-8", errors="replace")

        if not doc_text.strip():
            await update.message.reply_text("Файл пустой или не содержит текст.")
            return

        # Truncate if too long for API
        if len(doc_text) > 15000:
            doc_text = doc_text[:15000] + "\n\n[... документ обрезан ...]"

        logger.info("Document: %s, %d chars", file_name, len(doc_text))

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=DOC_PROMPT,
            messages=[{"role": "user", "content": doc_text}],
        )
        translation = response.content[0].text

        # If translation is long, send as document
        if len(translation) > 4000:
            out_bytes = io.BytesIO(translation.encode("utf-8"))
            out_name = f"translated_{file_name.rsplit('.', 1)[0]}.txt"
            out_bytes.name = out_name
            await update.message.reply_document(
                document=out_bytes,
                filename=out_name,
                caption="Перевод документа",
            )
        else:
            await update.message.reply_text(f"📄 Перевод ({file_name}):\n\n{translation}")

    except Exception as e:
        logger.error("Document error: %s", e, exc_info=True)
        await update.message.reply_text(f"DEBUG doc: {type(e).__name__}: {e}")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear_context))
    app.add_handler(MessageHandler(filters.VOICE, translate_voice))
    app.add_handler(MessageHandler(filters.PHOTO, translate_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, translate_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translate))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
