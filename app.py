import os
import time
import secrets
import threading
import requests

from flask import Flask, request, jsonify

app = Flask(__name__)

# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
SETUP_KEY = os.environ["SETUP_KEY"]

CHANNEL_ID = int(os.environ["CHANNEL_ID"])
SHRINKME_API = os.environ["SHRINKME_API"]

BOT_USERNAME = "Matultra96Bot"

# =========================================================
# RAM LIMITS
# =========================================================

MAX_THUMBNAILS = 300
THUMBNAIL_TTL = 7 * 24 * 60 * 60

TOKEN_TTL = 30 * 60

VIDEO_DELETE_TIME = 24 * 60 * 60

# message_id -> thumbnail file_id + timestamp
THUMBNAILS = {}

# token -> access information
TOKENS = {}


# =========================================================
# TELEGRAM API
# =========================================================

def telegram(method, data):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:

        response = requests.post(
            url,
            json=data,
            timeout=20
        )

        return response.json()

    except Exception:

        return {
            "ok": False,
            "description": "Telegram request failed"
        }


# =========================================================
# BASIC TELEGRAM FUNCTIONS
# =========================================================

def send_message(chat_id, text, keyboard=None):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:

        data["reply_markup"] = {
            "inline_keyboard": keyboard
        }

    return telegram(
        "sendMessage",
        data
    )


def send_photo(chat_id, photo, caption, keyboard=None):

    data = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption
    }

    if keyboard:

        data["reply_markup"] = {
            "inline_keyboard": keyboard
        }

    return telegram(
        "sendPhoto",
        data
    )


def answer_callback(callback_id):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
    )


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
