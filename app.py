import os, time, json, secrets, threading, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-this-secret")
SETUP_KEY = os.environ.get("SETUP_KEY", "change-this-setup-key")
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
SHRINKME_API = os.environ["SHRINKME_API"]
BOT_USERNAME = "Matultra96Bot"

DATA_FILE = "posts.json"
POSTS, TOKENS, THUMBNAILS = {}, {}, {}
LOCK = threading.Lock()
TOKEN_TTL = 30 * 60
DELETE_AFTER = 24 * 60 * 60
THUMBNAIL_TTL = 7 * 24 * 60 * 60
MAX_THUMBNAILS = 300


def load_posts():
    global POSTS
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            POSTS = json.load(f)
    except Exception:
        POSTS = {}


def save_posts():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(POSTS, f)
    os.replace(tmp, DATA_FILE)


def telegram(method, data):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=data, timeout=20
        )
        return r.json()
    except Exception as e:
        print("Telegram error:", e)
        return {"ok": False}


def send_message(chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": text}
    if keyboard:
        data["reply_markup"] = keyboard
    return telegram("sendMessage", data)


def send_photo(chat_id, photo, caption, keyboard=None):
    data = {"chat_id": chat_id, "photo": photo, "caption": caption}
    if keyboard:
        data["reply_markup"] = keyboard
    return telegram("sendPhoto", data)


def copy_video(chat_id, message_id):
    return telegram("copyMessage", {
        "chat_id": chat_id,
        "from_chat_id": CHANNEL_ID,
        "message_id": message_id,
        "caption": "🎬 Your requested video"
    })


def delete_message(chat_id, message_id):
    return telegram("deleteMessage", {
        "chat_id": chat_id, "message_id": message_id
    })


def delete_later(chat_id, message_id):
    def worker():
        time.sleep(DELETE_AFTER)
        delete_message(chat_id, message_id)
    threading.Thread(target=worker, daemon=True).start()


def make_shrink_link(destination):
    try:
        r = requests.get(
            "https://shrinkme.io/api",
            params={"api": SHRINKME_API, "url": destination},
            timeout=20
        )
        data = r.json()
        print("ShrinkMe:", data)
        return (
            data.get("shortenedUrl")
            or data.get("shortenedURL")
            or data.get("shorturl")
            or data.get("short")
        )
    except Exception as e:
        print("ShrinkMe error:", e)
        return None


def cleanup():
    while True:
        time.sleep(600)
        now = time.time()
        with LOCK:
            for t in list(TOKENS):
                if TOKENS[t]["expires"] < now:
                    del TOKENS[t]
            for n in list(THUMBNAILS):
                if THUMBNAILS[n]["time"] + THUMBNAIL_TTL < now:
                    del THUMBNAILS[n]
            if len(THUMBNAILS) > MAX_THUMBNAILS:
                items = sorted(
                    THUMBNAILS.items(),
                    key=lambda x: x[1]["time"]
                )
                for n, _ in items[:-MAX_THUMBNAILS]:
                    del THUMBNAILS[n]


load_posts()
threading.Thread(target=cleanup, daemon=True).start()


@app.route("/")
def home():
    return "Bot is running!"


@app.route("/telegram/webhook", methods=["POST"])
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}

    # Automatically number every new video in the private channel.
    channel_post = data.get("channel_post")
    if channel_post:
        chat_id = channel_post.get("chat", {}).get("id")
        if chat_id != CHANNEL_ID:
            return "OK", 200

        video = channel_post.get("video")
        if not video:
            return "OK", 200

        message_id = channel_post.get("message_id")

        with LOCK:
            nums = [int(x) for x in POSTS if str(x).isdigit()]
            number = max(nums) + 1 if nums else 1
            POSTS[str(number)] = message_id
            save_posts()

        thumb = video.get("thumbnail") or video.get("thumb")
        if thumb:
            THUMBNAILS[str(number)] = {
                "file_id": thumb["file_id"],
                "time": time.time()
            }

        # This message appears in the private source channel.
        send_message(CHANNEL_ID, f"✅ Post No. {number}")
        print(f"NEW VIDEO -> Post No. {number} -> Telegram ID {message_id}")
        return "OK", 200

    message = data.get("message")
    if message:
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        if not chat_id:
            return "OK", 200

        if text.startswith("/start"):
            parts = text.split(maxsplit=1)

            if len(parts) == 1:
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "🎬 New Video", "callback_data": "new"}],
                        [{"text": "ℹ️ Help", "callback_data": "help"}]
                    ]
                }
                send_message(
                    chat_id,
                    "👋 Welcome!\n\nSend the Post No. to get your video.\nExample: 1",
                    keyboard
                )
                return "OK", 200

            token = parts[1].strip()
            with LOCK:
                td = TOKENS.get(token)

            if not td or td["expires"] < time.time():
                with LOCK:
                    TOKENS.pop(token, None)
                send_message(chat_id, "❌ Invalid or expired link.")
                return "OK", 200

            if td["chat_id"] != chat_id:
                send_message(chat_id, "❌ This link belongs to another user.")
                return "OK", 200

            with LOCK:
                TOKENS.pop(token, None)

            result = copy_video(chat_id, td["source_message_id"])
            if not result.get("ok"):
                send_message(chat_id, "❌ Video could not be sent.")
                return "OK", 200

            copied_id = result.get("result", {}).get("message_id")
            if copied_id:
                delete_later(chat_id, copied_id)

            keyboard = {
                "inline_keyboard": [
                    [{"text": "🎬 New Video", "callback_data": "new"}],
                    [{"text": "🔄 Again", "callback_data": "again"}]
                ]
            }
            send_message(
                chat_id,
                "✅ Video sent!\n\nIt will automatically disappear after 24 hours.",
                keyboard
            )
            return "OK", 200

        if text.startswith("/help"):
            send_message(
                chat_id,
                "📖 Send the Post No., press Get Video, then open the link."
            )
            return "OK", 200

        if text.isdigit():
            number = text.strip()
            with LOCK:
                message_id = POSTS.get(number)

            if not message_id:
                send_message(chat_id, "❌ Post No. not found.")
                return "OK", 200

            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔓 Get Video", "callback_data": f"get_{number}"}]
                ]
            }
            thumb = THUMBNAILS.get(number)

            if thumb:
                result = send_photo(
                    chat_id, thumb["file_id"],
                    f"🎬 Post No. {number}", keyboard
                )
                if not result.get("ok"):
                    send_message(
                        chat_id,
                        f"🎬 Post No. {number}\n\nYour video is ready.",
                        keyboard
                    )
            else:
                send_message(
                    chat_id,
                    f"🎬 Post No. {number}\n\nYour video is ready.",
                    keyboard
                )
            return "OK", 200

    callback = data.get("callback_query")
    if callback:
        callback_id = callback.get("id")
        action = callback.get("data", "")
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        telegram("answerCallbackQuery", {"callback_query_id": callback_id})

        if action.startswith("get_"):
            number = action[4:]
            with LOCK:
                message_id = POSTS.get(number)

            if not message_id:
                send_message(chat_id, "❌ Post No. not found.")
                return "OK", 200

            token = secrets.token_urlsafe(18)
            with LOCK:
                TOKENS[token] = {
                    "chat_id": chat_id,
                    "source_message_id": message_id,
                    "expires": time.time() + TOKEN_TTL
                }

            destination = f"https://t.me/{BOT_USERNAME}?start={token}"
            short_url = make_shrink_link(destination)

            if not short_url:
                with LOCK:
                    TOKENS.pop(token, None)
                send_message(chat_id, "❌ Link service is temporarily unavailable.")
                return "OK", 200

            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔗 Open Link", "url": short_url}]
                ]
            }
            send_message(
                chat_id,
                "🔐 Open the link first. After the link process, you will return to the bot.",
                keyboard
            )
            return "OK", 200

        if action == "new":
            send_message(chat_id, "🎬 Send the Post No.\n\nExample: 25")
            return "OK", 200

        if action == "again":
            send_message(chat_id, "🔄 Send the Post No. again.")
            return "OK", 200

        if action == "help":
            send_message(
                chat_id,
                "📖 Send the Post No. → Get Video → Open Link → Return to Telegram."
            )
            return "OK", 200

    return "OK", 200


@app.route("/setup")
def setup():
    if request.args.get("key") != SETUP_KEY:
        return "Forbidden", 403

    webhook_url = request.url_root.rstrip("/") + "/telegram/webhook"
    result = telegram("setWebhook", {
        "url": webhook_url,
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message", "callback_query", "channel_post"]
    })
    return jsonify(result)t_cleanup = threading.Thread(target=ram_cleanup_loop, daemon=True)
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
