import os
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        token = context.args[0]

        if token == "demo":
            await update.message.reply_text(
                "✅ Verification successful!\n\n"
                "Your material:\n"
                "https://example.com"
            )
        else:
            await update.message.reply_text(
                "❌ Invalid or expired link.\n"
                "Please use the access link again."
            )
    else:
        await update.message.reply_text(
            "Please use your access link first."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Use your access link to get the material."
    )


telegram_app = Application.builder().token(BOT_TOKEN).build()

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("help", help_command))


@app.route("/")
def home():
    return "Bot is running!"


async def main():
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
