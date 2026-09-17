import json
import os
import secrets
import threading
import time
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Configuration & Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
SETUP_KEY = os.environ.get("SETUP_KEY", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
SHRINKME_API = os.environ.get("SHRINKME_API", "")
BOT_USERNAME = "Matultra96Bot"

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
POSTS_FILE = "posts.json"

# In-Memory Storage & Thread Safety
posts_lock = threading.Lock()
ram_lock = threading.Lock()

# Persistent state mapping: post_num (int) -> message_id (int)
posts_data = {
    "next_post_num": 1,
    "mapping": {}
}

# RAM caches
# tokens: token -> {"chat_id": int, "message_id": int, "expires_at": float}
tokens = {}

# thumbnails: post_num (str/int) -> {"file_id": str, "timestamp": float}
thumbnails = {}

# scheduled_deletions: list of {"chat_id": int, "message_id": int, "delete_at": float}
scheduled_deletions = []

# Persistent Storage Helpers
def load_posts():
    global posts_data
    with posts_lock:
        if os.path.exists(POSTS_FILE):
            try:
                with open(POSTS_FILE, "r") as f:
                    posts_data = json.load(f)
                    # Convert mapping keys back to int if needed
                    posts_data["mapping"] = {int(k): int(v) for k, v in posts_data.get("mapping", {}).items()}
                    if "next_post_num" not in posts_data:
                        posts_data["next_post_num"] = max(posts_data["mapping"].keys(), default=0) + 1
            except Exception as e:
                print(f"Error loading {POSTS_FILE}: {e}")

def save_posts():
    with posts_lock:
        try:
            with open(POSTS_FILE, "w") as f:
                json.dump(posts_data, f)
        except Exception as e:
            print(f"Error saving {POSTS_FILE}: {e}")

load_posts()

# Telegram API Wrapper Helper
def call_telegram(method, payload):
    try:
        url = f"{TELEGRAM_API_URL}/{method}"
        resp = requests.post(url, json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        print(f"Telegram API request failed ({method}): {e}")
        return None

# Background Daemon Tasks
def ram_cleanup_loop():
    while True:
        time.sleep(600)  # Runs every 10 minutes
        now = time.time()
        with ram_lock:
            # Clean expired tokens (> 30 min)
            expired_tokens = [t for t, data in tokens.items() if data.get("expires_at", 0) < now]
            for t in expired_tokens:
                del tokens[t]

            # Clean expired thumbnails (> 7 days)
            seven_days_sec = 7 * 24 * 3600
            expired_thumbs = [p for p, data in thumbnails.items() if now - data.get("timestamp", 0) > seven_days_sec]
            for p in expired_thumbs:
                del thumbnails[p]

            # Enforce max 300 thumbnails
            if len(thumbnails) > 300:
                sorted_thumbs = sorted(thumbnails.items(), key=lambda x: x[1].get("timestamp", 0))
                to_remove = len(thumbnails) - 300
                for i in range(to_remove):
                    del thumbnails[sorted_thumbs[i][0]]

def message_deletion_loop():
    while True:
        time.sleep(10)
        now = time.time()
        with ram_lock:
            to_delete = [item for item in scheduled_deletions if item["delete_at"] <= now]
            for item in to_delete:
                scheduled_deletions.remove(item)
                call_telegram("deleteMessage", {
                    "chat_id": item["chat_id"],
                    "message_id": item["message_id"]
                })

# Start background threads
t_cleanup = threading.Thread(target=ram_cleanup_loop, daemon=True)
t_cleanup.start()

t_deletion = threading.Thread(target=message_deletion_loop, daemon=True)
t_deletion.start()

# Helper Functions
def shorten_url(url):
    try:
        api_url = "https://shrinkme.io/api"
        params = {
            "api": SHRINKME_API,
            "url": url
        }
        resp = requests.get(api_url, params=params, timeout=10)
        res_json = resp.json()
        if res_json.get("status") == "success":
            return res_json.get("shortenedUrl")
        else:
            print(f"ShrinkMe API error response: {res_json}")
            return None
    except Exception as e:
        print(f"ShrinkMe request exception: {e}")
        return None

# Flask Routes
@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"

@app.route("/setup", methods=["GET"])
def setup():
    key = request.args.get("key", "")
    if key != SETUP_KEY or not SETUP_KEY:
        return "Unauthorized", 401

    host_url = request.host_url.rstrip("/")
    if host_url.startswith("http://"):
        host_url = host_url.replace("http://", "https://", 1)

    webhook_url = f"{host_url}/telegram/webhook"

    payload = {
        "url": webhook_url,
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message", "callback_query", "channel_post"]
    }

    res = call_telegram("setWebhook", payload)
    return jsonify(res)

@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret_header != WEBHOOK_SECRET:
        return "Forbidden", 403

    update = request.get_json(silent=True)
    if not update:
        return "OK", 200

    try:
        handle_update(update)
    except Exception as e:
        print(f"Error handling update: {e}")

    return "OK", 200

def handle_update(update):
    if "channel_post" in update:
        handle_channel_post(update["channel_post"])
    elif "message" in update:
        handle_user_message(update["message"])
    elif "callback_query" in update:
        handle_callback_query(update["callback_query"])

def handle_channel_post(post):
    chat_id = str(post.get("chat", {}).get("id", ""))
    expected_channel_id = str(CHANNEL_ID)

    if chat_id != expected_channel_id:
        return

    if "video" in post:
        msg_id = post["message_id"]
        with posts_lock:
            assigned_num = posts_data["next_post_num"]
            posts_data["mapping"][assigned_num] = msg_id
            posts_data["next_post_num"] = assigned_num + 1
            save_posts()

        print(f"NEW VIDEO -> Post No. {assigned_num} -> Telegram ID {msg_id}")

        # Capture thumbnail if present
        video = post["video"]
        if "thumb" in video and "file_id" in video["thumb"]:
            thumb_id = video["thumb"]["file_id"]
            with ram_lock:
                thumbnails[assigned_num] = {
                    "file_id": thumb_id,
                    "timestamp": time.time()
                }

def handle_user_message(msg):
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()

    if not chat_id or not text:
        return

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            token = parts[1].strip()
            process_start_token(chat_id, token)
        else:
            welcome_msg = (
                "👋 Welcome!\n\n"
                "Send the Post No. to get your video.\n"
                "Example: 25"
            )
            call_telegram("sendMessage", {"chat_id": chat_id, "text": welcome_msg})
        return

    if text.isdigit():
        post_num = int(text)
        with posts_lock:
            msg_id = posts_data["mapping"].get(post_num)

        if not msg_id:
            call_telegram("sendMessage", {
                "chat_id": chat_id,
                "text": "❌ Post No. not found."
            })
            return

        with ram_lock:
            thumb_info = thumbnails.get(post_num)

        keyboard = {
            "inline_keyboard": [[
                {"text": "🔓 Get Video", "callback_data": f"get_video:{post_num}"}
            ]]
        }

        if thumb_info:
            call_telegram("sendPhoto", {
                "chat_id": chat_id,
                "photo": thumb_info["file_id"],
                "caption": f"Post No. {post_num}",
                "reply_markup": keyboard
            })
        else:
            call_telegram("sendMessage", {
                "chat_id": chat_id,
                "text": f"Post No. {post_num}",
                "reply_markup": keyboard
            })
        return

def handle_callback_query(cb):
    query_id = cb.get("id")
    chat_id = cb.get("message", {}).get("chat", {}).get("id")
    data = cb.get("data", "")

    call_telegram("answerCallbackQuery", {"callback_query_id": query_id})

    if not chat_id:
        return

    if data.startswith("get_video:"):
        post_num_str = data.split(":")[1]
        if not post_num_str.isdigit():
            return
        post_num = int(post_num_str)

        with posts_lock:
            msg_id = posts_data["mapping"].get(post_num)

        if not msg_id:
            call_telegram("sendMessage", {"chat_id": chat_id, "text": "❌ Post No. not found."})
            return

        token = secrets.token_urlsafe(16)
        expires_at = time.time() + (30 * 60)  # 30 mins

        with ram_lock:
            tokens[token] = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "expires_at": expires_at
            }

        destination_url = f"https://t.me/{BOT_USERNAME}?start={token}"
        shortened = shorten_url(destination_url)

        if not shortened:
            call_telegram("sendMessage", {
                "chat_id": chat_id,
                "text": "⚠️ Temporary error generating link. Please try again."
            })
            return

        keyboard = {
            "inline_keyboard": [[
                {"text": "🔗 Open Link", "url": shortened}
            ]]
        }

        call_telegram("sendMessage", {
            "chat_id": chat_id,
            "text": "Click the link below to access your video:",
            "reply_markup": keyboard
        })

def process_start_token(chat_id, token):
    now = time.time()
    token_data = None

    with ram_lock:
        if token in tokens:
            token_data = tokens.pop(token)

    if not token_data:
        call_telegram("sendMessage", {
            "chat_id": chat_id,
            "text": "❌ Invalid or expired token."
        })
        return

    if token_data["expires_at"] < now:
        call_telegram("sendMessage", {
            "chat_id": chat_id,
            "text": "❌ Token has expired."
        })
        return

    if token_data["chat_id"] != chat_id:
        call_telegram("sendMessage", {
            "chat_id": chat_id,
            "text": "❌ Unauthorized token usage."
        })
        return

    # Copy video from private source channel
    res = call_telegram("copyMessage", {
        "chat_id": chat_id,
        "from_chat_id": CHANNEL_ID,
        "message_id": token_data["message_id"]
    })

    if res and res.get("ok"):
        sent_msg_id = res["result"]["message_id"]
        
        # Schedule message deletion after 24 hours
        delete_at = time.time() + (24 * 3600)
        with ram_lock:
            scheduled_deletions.append({
                "chat_id": chat_id,
                "message_id": sent_msg_id,
                "delete_at": delete_at
            })

        confirm_msg = (
            "✅ Video sent!\n\n"
            "It will automatically disappear after 24 hours."
        )

        # Show control buttons
        keyboard = {
            "keyboard": [
                [{"text": "🎬 New Video"}, {"text": "🔄 Again"}]
            ],
            "resize_keyboard": True
        }

        call_telegram("sendMessage", {
            "chat_id": chat_id,
            "text": confirm_msg,
            "reply_markup": keyboard
        })
    else:
        call_telegram("sendMessage", {
            "chat_id": chat_id,
            "text": "⚠️ Error retrieving video. Please contact administrator."
        })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
