import os
import time
import json
import secrets
import threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-this-secret")
SETUP_KEY = os.environ.get("SETUP_KEY", "change-this-setup-key")
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
SHRINKME_API = os.environ["SHRINKME_API"]
BOT_USERNAME = "Matultra96Bot"

DATA_FILE = "posts.json"
POSTS = {}
TOKENS = {}
THUMBS = {}
LOCK = threading.Lock()

TOKEN_TTL = 30 * 60
DELETE_AFTER = 24 * 60 * 60


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


load_posts()


def tg(method, data):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=data,
            timeout=20
        )
        return r.json()
    except Exception as e:
        print("Telegram error:", e)
        return {"ok": False}


def msg(chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": text}
    if keyboard:
        data["reply_markup"] = keyboard
    return tg("sendMessage", data)


def photo(chat_id, file_id, caption, keyboard=None):
    data = {
        "chat_id": chat_id,
        "photo": file_id,
        "caption": caption
    }
    if keyboard:
        data["reply_markup"] = keyboard
    return tg("sendPhoto", data)


def copy_video(chat_id, message_id):
    return tg("copyMessage", {
        "chat_id": chat_id,
        "from_chat_id": CHANNEL_ID,
        "message_id": message_id,
        "caption": "🎬 Your requested video"
    })


def delete_later(chat_id, message_id):
    def worker():
        time.sleep(DELETE_AFTER)
        tg("deleteMessage", {
            "chat_id": chat_id,
            "message_id": message_id
        })
    threading.Thread(target=worker, daemon=True).start()


def shorten(destination):
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
            for token in list(TOKENS):
                if TOKENS[token]["expires"] < now:
                    del TOKENS[token]
            for n in list(THUMBS):
                if THUMBS[n]["time"] + 7 * 86400 < now:
                    del THUMBS[n]


threading.Thread(target=cleanup, daemon=True).start()


@app.route("/")
def home():
    return "Bot is running!"


@app.route("/telegram/webhook", methods=["POST"])
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}

    # A new video was uploaded to the private source channel.
    cp = data.get("channel_post")
    if cp:
        if cp.get("chat", {}).get("id") != CHANNEL_ID:
            return "OK", 200

        video = cp.get("video")
        if not video:
            return "OK", 200

        telegram_id = cp.get("message_id")

        with LOCK:
            nums = [int(x) for x in POSTS if str(x).isdigit()]
            post_no = max(nums) + 1 if nums else 1
            POSTS[str(post_no)] = telegram_id
            save_posts()

        thumb = video.get("thumbnail") or video.get("thumb")
        if thumb:
            THUMBS[str(post_no)] = {
                "file_id": thumb["file_id"],
                "time": time.time()
            }

        # Bot tells you the assigned number in the private channel.
        msg(
            CHANNEL_ID,
            f"✅ Post No. {post_no}\n"
            f"Telegram message ID: {telegram_id}"
        )
        print(f"NEW VIDEO -> Post {post_no} -> Telegram ID {telegram_id}")
        return "OK", 200

    # User messages.
    m = data.get("message")
    if m:
        chat_id = m.get("chat", {}).get("id")
        text = m.get("text", "")

        if not chat_id:
            return "OK", 200

        if text.startswith("/start"):
            parts = text.split(maxsplit=1)

            if len(parts) == 1:
                kb = {
                    "inline_keyboard": [
                        [{"text": "🎬 New Video", "callback_data": "new"}],
                        [{"text": "ℹ️ Help", "callback_data": "help"}]
                    ]
                }
                msg(
                    chat_id,
                    "👋 Welcome!\n\nSend the Post No.\nExample: 1",
                    kb
                )
                return "OK", 200

            token = parts[1]

            with LOCK:
                item = TOKENS.get(token)

            if not item or item["expires"] < time.time():
                with LOCK:
                    TOKENS.pop(token, None)
                msg(chat_id, "❌ Invalid or expired link.")
                return "OK", 200

            if item["chat_id"] != chat_id:
                msg(chat_id, "❌ This link belongs to another user.")
                return "OK", 200

            with LOCK:
                TOKENS.pop(token, None)

            result = copy_video(chat_id, item["source_id"])

            if not result.get("ok"):
                msg(chat_id, "❌ Video could not be sent. Try again.")
                return "OK", 200

            sent_id = result.get("result", {}).get("message_id")
            if sent_id:
                delete_later(chat_id, sent_id)

            kb = {
                "inline_keyboard": [
                    [{"text": "🎬 New Video", "callback_data": "new"}],
                    [{"text": "🔄 Again", "callback_data": "again"}]
                ]
            }
            msg(
                chat_id,
                "✅ Video sent!\n\nIt will be deleted after 24 hours.",
                kb
            )
            return "OK", 200

        if text.startswith("/help"):
            msg(
                chat_id,
                "Send the Post No. of the video.\nExample: 1"
            )
            return "OK", 200

        if text.isdigit():
            post_no = text

            with LOCK:
                source_id = POSTS.get(post_no)

            if not source_id:
                msg(chat_id, "❌ Post No. not found.")
                return "OK", 200

            kb = {
                "inline_keyboard": [
                    [{"text": "🔓 Get Video", "callback_data": f"get_{post_no}"}]
                ]
            }

            thumb = THUMBS.get(post_no)
            if thumb:
                result = photo(
                    chat_id,
                    thumb["file_id"],
                    f"🎬 Post No. {post_no}",
                    kb
                )
                if result.get("ok"):
                    return "OK", 200

            msg(
                chat_id,
                f"🎬 Post No. {post_no}\n\nYour video is ready.",
                kb
            )
            return "OK", 200

    # Button presses.
    cb = data.get("callback_query")
    if cb:
        cb_id = cb.get("id")
        cb_data = cb.get("data")
        chat_id = cb.get("message", {}).get("chat", {}).get("id")

        tg("answerCallbackQuery", {"callback_query_id": cb_id})

        if cb_data.startswith("get_"):
            post_no = cb_data[4:]

            with LOCK:
                source_id = POSTS.get(post_no)

            if not source_id:
                msg(chat_id, "❌ Post No. not found.")
                return "OK", 200

            token = secrets.token_urlsafe(18)

            with LOCK:
                TOKENS[token] = {
                    "chat_id": chat_id,
                    "source_id": source_id,
                    "expires": time.time() + TOKEN_TTL
                }

            destination = f"https://t.me/{BOT_USERNAME}?start={token}"
            short_url = shorten(destination)

            if not short_url:
                with LOCK:
                    TOKENS.pop(token, None)
                msg(chat_id, "❌ Link service unavailable. Try again.")
                return "OK", 200

            kb = {
                "inline_keyboard": [
                    [{"text": "🔗 Open Link", "url": short_url}]
                ]
            }
            msg(
                chat_id,
                "🔐 Open the link first.\n\n"
                "After the link process, you will return to the bot.",
                kb
            )
            return "OK", 200

        if cb_data == "new" or cb_data == "again":
            msg(chat_id, "🎬 Send the Post No.\nExample: 1")
            return "OK", 200

        if cb_data == "help":
            msg(
                chat_id,
                "1. Send Post No.\n"
                "2. Press Get Video.\n"
                "3. Open the link.\n"
                "4. Return to Telegram.\n"
                "5. Video will be sent."
            )
            return "OK", 200

    return "OK", 200


@app.route("/setup")
def setup():
    if request.args.get("key") != SETUP_KEY:
        return "Forbidden", 403

    webhook_url = request.url_root.rstrip("/") + "/telegram/webhook"

    return jsonify(tg("setWebhook", {
        "url": webhook_url,
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": [
            "message",
            "callback_query",
            "channel_post"
        ]
    })) 
    except Exception as e:
        print("ShrinkMe error:", e)
        return None


def cleanup_memory():
    while True:
        time.sleep(600)
        now = time.time()

        with LOCK:
            for token in list(TOKENS):
                if TOKENS[token]["expires"] < now:
                    del TOKENS[token]

            for post_no in list(THUMBNAILS):
                if THUMBNAILS[post_no]["time"] + THUMBNAIL_TTL < now:
                    del THUMBNAILS[post_no]

            if len(THUMBNAILS) > MAX_THUMBNAILS:
                items = sorted(
                    THUMBNAILS.items(),
                    key=lambda x: x[1]["time"]
                )
                for post_no, _ in items[:-MAX_THUMBNAILS]:
                    del THUMBNAILS[post_no]


threading.Thread(target=cleanup_memory, daemon=True).start()


@app.route("/")
def home():
    return "Bot is running!"


@app.route("/telegram/webhook", methods=["POST"])
def webhook():

    if request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    ) != WEBHOOK_SECRET:
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}

    # New video uploaded to private channel
    channel_post = data.get("channel_post")

    if channel_post:
        chat = channel_post.get("chat", {})

        if chat.get("id") != CHANNEL_ID:
            return "OK", 200

        video = channel_post.get("video")

        if not video:
            return "OK", 200

        telegram_message_id = channel_post.get("message_id")

        with LOCK:
            numbers = [
                int(x) for x in POSTS.keys()
                if str(x).isdigit()
            ]
            next_number = max(numbers) + 1 if numbers else 1

            POSTS[str(next_number)] = telegram_message_id
            save_posts()

        thumbnail = video.get("thumbnail") or video.get("thumb")

        if thumbnail:
            THUMBNAILS[str(next_number)] = {
                "file_id": thumbnail["file_id"],
                "time": time.time()
            }

        print(
            f"NEW VIDEO -> Post No. {next_number} "
            f"-> Telegram ID {telegram_message_id}"
        )

        return "OK", 200

    # Normal user message
    message = data.get("message")

    if message:
        chat = message.get("chat", {})
        chat_id = chat.get("id")
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
                    "👋 Welcome!\n\n"
                    "Send the Post No. to get your video.\n\n"
                    "Example: 25",
                    keyboard
                )
                return "OK", 200

            token = parts[1].strip()

            with LOCK:
                token_data = TOKENS.get(token)

            if not token_data:
                send_message(
                    chat_id,
                    "❌ Invalid or expired link.\n\n"
                    "Please request the video again."
                )
                return "OK", 200

            if token_data["expires"] < time.time():
                with LOCK:
                    TOKENS.pop(token, None)
                send_message(
                    chat_id,
                    "❌ This link has expired.\n\n"
                    "Please request the video again."
                )
                return "OK", 200

            if token_data["chat_id"] != chat_id:
                send_message(chat_id, "❌ This link belongs to another user.")
                return "OK", 200

            source_message_id = token_data["source_message_id"]

            with LOCK:
                TOKENS.pop(token, None)

            result = copy_video(chat_id, source_message_id)

            if not result.get("ok"):
                send_message(
                    chat_id,
                    "❌ Video could not be sent.\n\n"
                    "Please try again."
                )
                return "OK", 200

            copied_message = result.get("result", {})
            copied_message_id = copied_message.get("message_id")

            if copied_message_id:
                schedule_video_delete(chat_id, copied_message_id)

            keyboard = {
                "inline_keyboard": [
                    [{"text": "🎬 New Video", "callback_data": "new"}],
                    [{"text": "🔄 Again", "callback_data": "again"}]
                ]
            }

            send_message(
                chat_id,
                "✅ Video sent!\n\n"
                "It will automatically disappear after 24 hours.",
                keyboard
            )
            return "OK", 200

        if text.startswith("/help"):
            send_message(
                chat_id,
                "📖 How to use:\n\n"
                "Send the Post No.\n"
                "Example: 1"
            )
            return "OK", 200

        if text.isdigit():
            post_number = text.strip()

            with LOCK:
                source_message_id = POSTS.get(post_number)

            if not source_message_id:
                send_message(chat_id, "❌ Post No. not found.")
                return "OK", 200

            keyboard = {
                "inline_keyboard": [
                    [{
                        "text": "🔓 Get Video",
                        "callback_data": f"get_{post_number}"
                    }]
                ]
            }

            thumbnail_data = THUMBNAILS.get(post_number)

            if thumbnail_data:
                result = send_photo(
                    chat_id,
                    thumbnail_data["file_id"],
                    f"🎬 Post No. {post_number}",
                    keyboard
                )

                if not result.get("ok"):
                    send_message(
                        chat_id,
                        f"🎬 Post No. {post_number}\n\n"
                        "Your video is ready.",
                        keyboard
                    )
            else:
                send_message(
                    chat_id,
                    f"🎬 Post No. {post_number}\n\n"
                    "Your video is ready.",
                    keyboard
                )

            return "OK", 200

    # Button clicks
    callback = data.get("callback_query")

    if callback:
        callback_id = callback.get("id")
        callback_data = callback.get("data")
        chat_id = callback.get("message", {}).get("chat", {}).get("id")

        answer_callback(callback_id)

        if callback_data.startswith("get_"):
            post_number = callback_data.replace("get_", "", 1)

            with LOCK:
                source_message_id = POSTS.get(post_number)

            if not source_message_id:
                send_message(chat_id, "❌ Post No. not found.")
                return "OK", 200

            token = secrets.token_urlsafe(18)

            with LOCK:
                TOKENS[token] = {
                    "chat_id": chat_id,
                    "source_message_id": source_message_id,
                    "expires": time.time() + TOKEN_TTL
                }

            destination = f"https://t.me/{BOT_USERNAME}?start={token}"
            short_url = create_shrink_link(destination)

            if not short_url:
                with LOCK:
                    TOKENS.pop(token, None)

                send_message(
                    chat_id,
                    "❌ Link service is temporarily unavailable."
                )
                return "OK", 200

            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔗 Open Link", "url": short_url}]
                ]
            }

            send_message(
                chat_id,
                "🔐 Open the link first.\n\n"
                "After the link process, you will return to the bot.",
                keyboard
            )
            return "OK", 200

        if callback_data == "new":
            send_message(
                chat_id,
                "🎬 Send the Post No.\n\nExample: 25"
            )
            return "OK", 200

        if callback_data == "again":
            send_message(
                chat_id,
                "🔄 Send the Post No. again."
            )
            return "OK", 200

        if callback_data == "help":
            send_message(
                chat_id,
                "📖 How to use:\n\n"
                "1. Send the Post No.\n"
                "2. Press Get Video.\n"
                "3. Open the link.\n"
                "4. Return to Telegram.\n"
                "5. Video will be sent."
            )
            return "OK", 200

    return "OK", 200


@app.route("/setup")
def setup():

    key = request.args.get("key")

    if key != SETUP_KEY:
        return "Forbidden", 403

    webhook_url = (
        request.url_root.rstrip("/")
        + "/telegram/webhook"
    )

    result = telegram(
        "setWebhook",
        {
            "url": webhook_url,
            "secret_token": WEBHOOK_SECRET,
            "allowed_updates": [
                "message",
                "callback_query",
                "channel_post"
            ]
        }
    )

    return jsonify(result)        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    


# -----------------------------
# Copy video from private channel
# -----------------------------

def copy_video(chat_id, source_message_id):

    result = telegram(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": CHANNEL_ID,
            "message_id": source_message_id,
            "caption": "🎬 Your requested video"
        }
    )

    return result


# -----------------------------
# Auto delete after 24 hours
# -----------------------------

def schedule_video_delete(chat_id, message_id):

    def worker():
        time.sleep(VIDEO_DELETE_TIME)

        try:
            delete_message(chat_id, message_id)
            print("Deleted:", chat_id, message_id)
        except Exception as e:
            print("Delete error:", e)

    threading.Thread(
        target=worker,
        daemon=True
    ).start()


# -----------------------------
# ShrinkMe
# -----------------------------

def create_shrink_link(destination):

    try:
        response = requests.get(
            "https://shrinkme.io/api",
            params={
                "api": SHRINKME_API,
                "url": destination
            },
            timeout=20
        )

        data = response.json()

        print("ShrinkMe:", data)

        short_url = (
            data.get("shortenedUrl")
            or data.get("shortenedURL")
            or data.get("shorturl")
            or data.get("short")
        )

        return short_url

    except Exception as e:
        print("ShrinkMe error:", e)
        return None


# -----------------------------
# Memory cleanup
# -----------------------------

def cleanup_memory():

    while True:

        time.sleep(600)

        now = time.time()

        # Remove expired tokens
        with LOCK:
            expired_tokens = [
                token
                for token, data in TOKENS.items()
                if data["expires"] < now
            ]

            for token in expired_tokens:
                del TOKENS[token]

            # Remove old thumbnails
            expired_thumbs = [
                post_no
                for post_no, data in THUMBNAILS.items()
                if data["time"] + THUMBNAIL_TTL < now
            ]

            for post_no in expired_thumbs:
                del THUMBNAILS[post_no]

            # Keep only latest thumbnails
            if len(THUMBNAILS) > MAX_THUMBNAILS:

                sorted_items = sorted(
                    THUMBNAILS.items(),
                    key=lambda x: x[1]["time"]
                )

                remove_count = len(THUMBNAILS) - MAX_THUMBNAILS

                for post_no, _ in sorted_items[:remove_count]:
                    del THUMBNAILS[post_no]

        print("Memory cleanup completed.")


threading.Thread(
    target=cleanup_memory,
    daemon=True
).start()


# -----------------------------
# Home
# -----------------------------

@app.route("/")
def home():
    return "Bot is running!"


# -----------------------------
# Webhook
# -----------------------------

@app.route("/telegram/webhook", methods=["POST"])
def webhook():

    # Security check
    if request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    ) != WEBHOOK_SECRET:

        return "Forbidden", 403

    data = request.get_json(silent=True) or {}

    # ---------------------------------
    # CHANNEL POST
    # ---------------------------------

    if "channel_post" in data:

        channel_post = data["channel_post"]

        chat = channel_post.get("chat", {})
        channel_id = chat.get("id")

        if channel_id != CHANNEL_ID:
            return "OK", 200

        # Only process videos
        video = channel_post.get("video")

        if not video:
            return "OK", 200

        telegram_message_id = channel_post.get("message_id")

        # Find next sequence number
        with LOCK:

            if POSTS:
                numbers = [
                    int(x)
                    for x in POSTS.keys()
                    if str(x).isdigit()
                ]

                next_number = max(numbers) + 1 if numbers else 1

            else:
                next_number = 1

            POSTS[str(next_number)] = telegram_message_id

            save_posts()

        # Save thumbnail if available
        thumbnail = (
            video.get("thumbnail")
            or video.get("thumb")
        )

        if thumbnail:

            THUMBNAILS[str(next_number)] = {
                "file_id": thumbnail["file_id"],
                "time": time.time()
            }

        # Tell admin/channel where sequence reached
        print(
            f"NEW VIDEO → Post No. {next_number} "
            f"→ Telegram ID {telegram_message_id}"
        )

        return "OK", 200


    # ---------------------------------
    # NORMAL MESSAGE
    # ---------------------------------

    message = data.get("message")

    if message:

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text", "")

        if not chat_id:
            return "OK", 200

        # /start
        if text.startswith("/start"):

            parts = text.split(maxsplit=1)

            # Normal start
            if len(parts) == 1:

                keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "🎬 New Video",
                                "callback_data": "new"
                            }
                        ],
                        [
                            {
                                "text": "ℹ️ Help",
                                "callback_data": "help"
                            }
                        ]
                    ]
                }

                send_message(
                    chat_id,
                    "👋 Welcome!\n\n"
                    "Send the Post No. to get your video.\n\n"
                    "Example: 25",
                    keyboard
                )

                return "OK", 200

            # Return from ShrinkMe
            token = parts[1].strip()

            with LOCK:
                token_data = TOKENS.get(token)

            if not token_data:
                send_message(
                    chat_id,
                    "❌ Invalid or expired link.\n\n"
                    "Please request the video again."
                )

                return "OK", 200

            if token_data["expires"] < time.time():

                with LOCK:
                    TOKENS.pop(token, None)

                send_message(
                    chat_id,
                    "❌ This link has expired.\n\n"
                    "Please request the video again."
                )

                return "OK", 200

            if token_data["chat_id"] != chat_id:

                send_message(
                    chat_id,
                    "❌ This link belongs to another user."
                )

                return "OK", 200

            source_message_id = token_data["source_message_id"]

            with LOCK:
                TOKENS.pop(token, None)

            result = copy_video(
                chat_id,
                source_message_id
            )

            if not result.get("ok"):

                send_message(
                    chat_id,
                    "❌ Video could not be sent.\n\n"
                    "Please try again."
                )

                return "OK", 200

            copied_message = result.get("result", {})
            copied_message_id = copied_message.get("message_id")

            if copied_message_id:
                schedule_video_delete(
                    chat_id,
                    copied_message_id
                )

            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "🎬 New Video",
                            "callback_data": "new"
                        }
                    ],
                    [
                        {
                            "text": "🔄 Again",
                            "callback_data": "again"
                        }
                    ]
                ]
            }

            send_message(
                chat_id,
                "✅ Video sent!\n\n"
                "It will automatically disappear after 24 hours.",
                keyboard
            )

            return "OK", 200


        # /help
        if text.startswith("/help"):

            send_message(
                chat_id,
                "📖 How to use:\n\n"
                "Send the Post No. of the video.\n"
                "Example: 1\n\n"
                "You will receive a link first."
            )

            return "OK", 200


        # ---------------------------------
        # POST NUMBER
        # ---------------------------------

        if text.isdigit():

            post_number = text.strip()

            with LOCK:
                source_message_id = POSTS.get(post_number)

            if not source_message_id:

                send_message(
                    chat_id,
                    "❌ Post No. not found."
                )

                return "OK", 200

            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "🔓 Get Video",
                            "callback_data": f"get_{post_number}"
                        }
                    ]
                ]
            }

            thumbnail_data = THUMBNAILS.get(post_number)

            if thumbnail_data:

                result = send_photo(
                    chat_id,
                    thumbnail_data["file_id"],
                    f"🎬 Post No. {post_number}",
                    keyboard
                )

                # Fallback if thumbnail fails
                if not result.get("ok"):

                    send_message(
                        chat_id,
                        f"🎬 Post No. {post_number}\n\n"
                        "Your video is ready.",
                        keyboard
                    )

            else:

                send_message(
                    chat_id,
                    f"🎬 Post No. {post_number}\n\n"
                    "Your video is ready.",
                    keyboard
                )

            return "OK", 200


    # ---------------------------------
    # CALLBACK QUERY
    # ---------------------------------

    callback = data.get("callback_query")

    if callback:

        callback_id = callback.get("id")
        callback_data = callback.get("data")

        callback_message = callback.get("message", {})
        chat = callback_message.get("chat", {})
        chat_id = chat.get("id")

        answer_callback(callback_id)

        # Get video
        if callback_data.startswith("get_"):

            post_number = callback_data.replace(
                "get_",
                "",
                1
            )

            with LOCK:
                source_message_id = POSTS.get(post_number)

            if not source_message_id:

                send_message(
                    chat_id,
                    "❌ Post No. not found."
                )

                return "OK", 200

            # Create fresh token
            token = secrets.token_urlsafe(18)

            with LOCK:
                TOKENS[token] = {
                    "chat_id": chat_id,
                    "source_message_id": source_message_id,
                    "expires": time.time() + TOKEN_TTL
                }

            destination = (
                f"https://t.me/{BOT_USERNAME}"
                f"?start={token}"
            )

            short_url = create_shrink_link(
                destination
            )

            if not short_url:

                with LOCK:
                    TOKENS.pop(token, None)

                send_message(
                    chat_id,
                    "❌ Link service is temporarily unavailable.\n"
                    "Please try again later."
                )

                return "OK", 200

            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "🔗 Open Link",
                            "url": short_url
                        }
                    ]
                ]
            }

            send_message(
                chat_id,
                "🔐 Complete the link process first.\n\n"
                "Then Telegram will automatically return you "
                "to the bot and your video will be sent.",
                keyboard
            )

            return "OK", 200


        # New Video
        if callback_data == "new":

            send_message(
                chat_id,
                "🎬 Send the Post No.\n\n"
                "Example: 25"
            )

            return "OK", 200


        # Again
        if callback_data == "again":

            send_message(
                chat_id,
                "🔄 Send the Post No. again."
            )

            return "OK", 200


        # Help
        if callback_data == "help":

            send_message(
                chat_id,
                "📖 How to use:\n\n"
                "1. Send the Post No.\n"
                "2. Press Get Video.\n"
                "3. Open the link.\n"
                "4. You will return to the bot.\n"
                "5. The video will be sent automatically."
            )

            return "OK", 200

    return "OK", 200


# -----------------------------
# Setup webhook
# -----------------------------

@app.route("/setup")
def setup():

    key = request.args.get("key")

    if key != SETUP_KEY:
        return "Forbidden", 403

    webhook_url = (
        request.url_root.rstrip("/")
        + "/telegram/webhook"
    )

    result = telegram(
        "setWebhook",
        {
            "url": webhook_url,
            "secret_token": WEBHOOK_SECRET,
            "allowed_updates": [
                "message",
                "callback_query",
                "channel_post"
            ]
        }
    )

    return jsonify(result)        {
            "callback_query_id": callback_id
        }
    


def delete_message(chat_id, message_id):

    return telegram(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


# =========================================================
# COPY VIDEO DIRECTLY FROM PRIVATE CHANNEL
# =========================================================

def copy_video(chat_id, source_message_id):

    return telegram(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": CHANNEL_ID,
            "message_id": source_message_id,

            # Original channel caption won't be copied.
            "caption": "🎬 Your requested video"
        }
    )


# =========================================================
# DELETE USER'S VIDEO AFTER 24 HOURS
# =========================================================

def schedule_video_delete(chat_id, message_id):

    def delete_job():

        time.sleep(VIDEO_DELETE_TIME)

        delete_message(
            chat_id,
            message_id
        )

    thread = threading.Thread(
        target=delete_job,
        daemon=True
    )

    thread.start()


# =========================================================
# SHRINKME
# =========================================================

def create_shrink_link(destination):

    try:

        params = {
            "api": SHRINKME_API,
            "url": destination
        }

        response = requests.get(
            "https://shrinkme.io/api",
            params=params,
            timeout=20
        )

        data = response.json()

        short_url = (
            data.get("shortenedUrl")
            or data.get("shortened")
            or data.get("shorturl")
            or data.get("short_url")
        )

        return short_url

    except Exception:

        return None


# =========================================================
# CLEAN OLD RAM DATA
# =========================================================

def cleanup_memory():

    while True:

        try:

            now = time.time()

            # ---------------------------------------------
            # Remove expired tokens
            # ---------------------------------------------

            expired_tokens = []

            for token, info in TOKENS.items():

                if now > info["expires"]:

                    expired_tokens.append(token)

            for token in expired_tokens:

                TOKENS.pop(token, None)

            # ---------------------------------------------
            # Remove old thumbnails
            # ---------------------------------------------

            expired_thumbnails = []

            for message_id, info in THUMBNAILS.items():

                if now - info["time"] > THUMBNAIL_TTL:

                    expired_thumbnails.append(message_id)

            for message_id in expired_thumbnails:

                THUMBNAILS.pop(
                    message_id,
                    None
                )

            # ---------------------------------------------
            # Hard RAM limit
            # ---------------------------------------------

            if len(THUMBNAILS) > MAX_THUMBNAILS:

                sorted_items = sorted(
                    THUMBNAILS.items(),
                    key=lambda x: x[1]["time"]
                )

                remove_count = (
                    len(THUMBNAILS)
                    - MAX_THUMBNAILS
                )

                for message_id, _ in sorted_items[:remove_count]:

                    THUMBNAILS.pop(
                        message_id,
                        None
                    )

        except Exception:

            pass

        # Run cleanup every 10 minutes
        time.sleep(600)


# Start cleanup worker
cleanup_thread = threading.Thread(
    target=cleanup_memory,
    daemon=True
)

cleanup_thread.start()


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return "Bot is running!"


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def webhook():

    # -----------------------------------------------------
    # SECURITY
    # -----------------------------------------------------

    if request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    ) != WEBHOOK_SECRET:

        return "Forbidden", 403

    data = request.get_json(
        silent=True
    ) or {}

    # =====================================================
    # CHANNEL POST
    # =====================================================

    channel_post = data.get(
        "channel_post"
    )

    if channel_post:

        chat_id = channel_post.get(
            "chat",
            {}
        ).get("id")

        if chat_id != CHANNEL_ID:

            return "OK", 200

        message_id = channel_post.get(
            "message_id"
        )

        video = channel_post.get(
            "video"
        )

        if video and message_id:

            # ---------------------------------------------
            # Save ONLY thumbnail information in RAM
            # ---------------------------------------------

            thumbnail = (
                video.get("thumbnail")
                or video.get("thumb")
            )

            if thumbnail:

                THUMBNAILS[str(message_id)] = {
                    "file_id": thumbnail["file_id"],
                    "time": time.time()
                }

        return "OK", 200

    # =====================================================
    # CALLBACK QUERY
    # =====================================================

    callback = data.get(
        "callback_query"
    )

    if callback:

        callback_id = callback.get("id")

        answer_callback(
            callback_id
        )

        callback_data = callback.get(
            "data",
            ""
        )

        message = callback.get(
            "message",
            {}
        )

        chat_id = message.get(
            "chat",
            {}
        ).get("id")

        # -------------------------------------------------
        # GET VIDEO
        # -------------------------------------------------

        if callback_data.startswith("get_"):

            post_number = callback_data.replace(
                "get_",
                "",
                1
            )

            if not post_number.isdigit():

                return "OK", 200

            source_message_id = int(
                post_number
            )

            # ---------------------------------------------
            # NEW TOKEN EVERY REQUEST
            # ---------------------------------------------

            token = secrets.token_urlsafe(
                18
            )

            TOKENS[token] = {

                "chat_id": chat_id,

                "source_message_id":
                    source_message_id,

                "expires":
                    time.time() + TOKEN_TTL
            }

            # ---------------------------------------------
            # Telegram return/deep link
            # ---------------------------------------------

            destination = (
                f"https://t.me/"
                f"{BOT_USERNAME}"
                f"?start={token}"
            )

            # ---------------------------------------------
            # ShrinkMe
            # ---------------------------------------------

            short_url = create_shrink_link(
                destination
            )

            if not short_url:

                TOKENS.pop(
                    token,
                    None
                )

                send_message(
                    chat_id,
                    "❌ Short link create nahi ho saka.\n"
                    "Please try again."
                )

                return "OK", 200

            keyboard = [[
                {
                    "text": "🔗 Open Link",
                    "url": short_url
                }
            ]]

            send_message(
                chat_id,

                "🔗 Your link is ready.\n\n"
                "Open the link to continue.",

                keyboard
            )

            return "OK", 200

        # -------------------------------------------------
        # NEW VIDEO
        # -------------------------------------------------

        if callback_data == "new":

            send_message(
                chat_id,
                "🎬 Send the Post Number."
            )

            return "OK", 200

        # -------------------------------------------------
        # AGAIN
        # -------------------------------------------------

        if callback_data == "again":

            send_message(
                chat_id,
                "🔄 Send another Post Number."
            )

            return "OK", 200

        # -------------------------------------------------
        # HELP
        # -------------------------------------------------

        if callback_data == "help":

            send_message(
                chat_id,

                "ℹ️ How to use:\n\n"
                "1. Send the Post Number.\n"
                "2. Open the generated link.\n"
                "3. Return to the bot.\n"
                "4. Your requested video will be sent."
            )

            return "OK", 200

        return "OK", 200

    # =====================================================
    # USER MESSAGE
    # =====================================================

    message = data.get(
        "message",
        {}
    )

    chat_id = message.get(
        "chat",
        {}
    ).get("id")

    text = message.get(
        "text",
        ""
    ).strip()

    if not chat_id:

        return "OK", 200

    # =====================================================
    # /START
    # =====================================================

    if text.startswith("/start"):

        parts = text.split(
            maxsplit=1
        )

        # -------------------------------------------------
        # Normal /start
        # -------------------------------------------------

        if len(parts) == 1:

            keyboard = [

                [
                    {
                        "text": "🎬 New Video",
                        "callback_data": "new"
                    }
                ],

                [
                    {
                        "text": "ℹ️ Help",
                        "callback_data": "help"
                    }
                ]

            ]

            send_message(
                chat_id,

                "👋 Welcome!\n\n"
                "Send the Post Number of the video.",

                keyboard
            )

            return "OK", 200

        # -------------------------------------------------
        # RETURN TOKEN
        # -------------------------------------------------

        token = parts[1].strip()

        access = TOKENS.get(
            token
        )

        if not access:

            send_message(
                chat_id,
                "❌ Link invalid ya expire ho gaya."
            )

            return "OK", 200

        # -------------------------------------------------
        # Check expiry
        # -------------------------------------------------

        if time.time() > access["expires"]:

            TOKENS.pop(
                token,
                None
            )

            send_message(
                chat_id,
                "❌ Link expire ho gaya.\n\n"
                "Please request again."
            )

            return "OK", 200

        # -------------------------------------------------
        # Check same user
        # -------------------------------------------------

        if access["chat_id"] != chat_id:

            send_message(
                chat_id,
                "❌ Ye access link kisi aur user ka hai."
            )

            return "OK", 200

        # -------------------------------------------------
        # One-time token
        # -------------------------------------------------

        TOKENS.pop(
            token,
            None
        )

        source_message_id = access[
            "source_message_id"
        ]

        # -------------------------------------------------
        # Copy directly from private channel
        # -------------------------------------------------

        result = copy_video(
            chat_id,
            source_message_id
        )

        if not result.get("ok"):

            send_message(
                chat_id,

                "❌ Video nahi mil saka.\n"
                "Post Number check karo."
            )

            return "OK", 200

        copied_message = result.get(
            "result",
            {}
        )

        copied_message_id = copied_message.get(
            "message_id"
        )

        # -------------------------------------------------
        # Delete user's copy after 24 hours
        # -------------------------------------------------

        if copied_message_id:

            schedule_video_delete(
                chat_id,
                copied_message_id
            )

        # -------------------------------------------------
        # Buttons
        # -------------------------------------------------

        keyboard = [

            [
                {
                    "text": "🎬 New Video",
                    "callback_data": "new"
                }
            ],

            [
                {
                    "text": "🔄 Again",
                    "callback_data": "again"
                },

                {
                    "text": "ℹ️ Help",
                    "callback_data": "help"
                }
            ]

        ]

        send_message(
            chat_id,

            "✅ Video sent.\n\n"
            "Your copy will automatically be deleted "
            "after 24 hours.",

            keyboard
        )

        return "OK", 200

    # =====================================================
    # /HELP
    # =====================================================

    if text.startswith("/help"):

        send_message(
            chat_id,

            "ℹ️ Send the Post Number of the video."
        )

        return "OK", 200

    # =====================================================
    # POST NUMBER
    # =====================================================

    if text.isdigit():

        post_number = text

        # Since Post Number = Telegram message ID,
        # we don't need to store every video in RAM.

        thumbnail = THUMBNAILS.get(
            post_number
        )

        keyboard = [[
            {
                "text": "🎬 Get Video",
                "callback_data":
                    f"get_{post_number}"
            }
        ]]

        # -------------------------------------------------
        # Thumbnail available
        # -------------------------------------------------

        if thumbnail:

            result = send_photo(
                chat_id,

                thumbnail["file_id"],

                f"🎬 Video #{post_number}",

                keyboard
            )

            # If thumbnail failed, fallback to text
            if not result.get("ok"):

                send_message(
                    chat_id,

                    f"🎬 Video #{post_number}\n\n"
                    "Click below to continue.",

                    keyboard
                )

        # -------------------------------------------------
        # Thumbnail not available
        # -------------------------------------------------

        else:

            send_message(
                chat_id,

                f"🎬 Video #{post_number}\n\n"
                "Click below to continue.",

                keyboard
            )

        return "OK", 200

    # =====================================================
    # UNKNOWN MESSAGE
    # =====================================================

    send_message(
        chat_id,

        "Post Number bhejo.\n\n"
        "Example: 125"
    )

    return "OK", 200


# =========================================================
# SETUP WEBHOOK
# =========================================================

@app.route("/setup")
def setup():

    key = request.args.get(
        "key"
    )

    if key != SETUP_KEY:

        return "Forbidden", 403

    webhook_url = (
        request.url_root.rstrip("/")
        + "/telegram/webhook"
    )

    result = telegram(
        "setWebhook",
        {
            "url": webhook_url,

            "secret_token":
                WEBHOOK_SECRET,

            "allowed_updates": [
                "message",
                "callback_query",
                "channel_post"
            ]
        }
    )

    return jsonify(result)
