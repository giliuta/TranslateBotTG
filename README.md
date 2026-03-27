# Telegram AI Translator Bot

Телеграм-бот, который переводит сообщения с русского на английский с помощью Claude AI.

## Установка

1. Клонируйте репозиторий и установите зависимости:

```bash
pip install -r requirements.txt
```

2. Создайте файл `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

3. Заполните `.env`:
   - `TELEGRAM_BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather)
   - `ANTHROPIC_API_KEY` — API-ключ от [Anthropic](https://console.anthropic.com/)

## Запуск

```bash
python bot.py
```

## Использование

Отправьте боту сообщение на русском — он ответит переводом на английский.
