import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-this-secret")
SETUP_KEY = os.environ.get("SETUP_KEY", "change-this-setup-key")


def telegram(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = requests.post(url, json=data, timeout=20)
    return response.json()


def send_message(chat_id, text):
    return telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


@app.route("/")
def home():
    return "Bot is running!"


@app.route("/telegram/webhook", methods=["POST"])
def webhook():

    # Security check
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}

    message = data.get("message", {})
    chat = message.get("chat", {})
    text = message.get("text", "")

    chat_id = chat.get("id")

    if not chat_id:
        return "OK", 200

    # /start command
    if text.startswith("/start"):

        parts = text.split(maxsplit=1)

        if len(parts) > 1:
            token = parts[1].strip()

            # Demo verification
            if token == "demo":
                send_message(
                    chat_id,
                    "✅ Verification successful!\n\n"
                    "Your material is ready:\n"
                    "https://example.com"
                )
            else:
                send_message(
                    chat_id,
                    "❌ Invalid or expired link.\n\n"
                    "Please use your access link again."
                )

        else:
            send_message(
                chat_id,
                "Please use your access link first."
            )

    # /help command
    elif text.startswith("/help"):
        send_message(
            chat_id,
            "Use your access link to get the material."
        )

    return "OK", 200


# This activates Telegram webhook
@app.route("/setup")
def setup():

    key = request.args.get("key")

    if key != SETUP_KEY:
        return "Forbidden", 403

    webhook_url = request.url_root.rstrip("/") + "/telegram/webhook"

    result = telegram(
        "setWebhook",
        {
            "url": webhook_url,
            "secret_token": WEBHOOK_SECRET
        }
    )

    return jsonify(result)
