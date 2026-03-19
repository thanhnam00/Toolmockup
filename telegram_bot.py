"""
Telegram Bot for Google Flow Image Generation
Receives image + prompt from Telegram, calls flow_server, returns generated images.
Includes "Save to Drive" button for each generated image.
"""

import asyncio
import base64
import io
import json
import logging
import os
import time
import uuid

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8799245561:AAHFVQeUXAfUEYWL22oTu2wB-Z4QsR3k1yM")
FLOW_SERVER_URL = os.environ.get("FLOW_SERVER_URL", "http://127.0.0.1:5000")
ALLOWED_USERS = os.environ.get("ALLOWED_USERS", "")  # comma-separated user IDs, empty = allow all

# Google Drive config
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")  # Google Drive folder ID to save images
GDRIVE_CREDENTIALS_FILE = os.environ.get("GDRIVE_CREDENTIALS_FILE", "/root/gdrive_credentials.json")

# Local save directory (fallback if Google Drive not configured)
SAVE_DIR = "/root/saved_images"
os.makedirs(SAVE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("telegram_bot")

# ---------------------------------------------------------------------------
# In-memory store for image data (keyed by callback ID)
# ---------------------------------------------------------------------------
image_cache = {}  # {callback_id: {"data": bytes, "prompt": str, "timestamp": float}}

def cleanup_cache():
    """Remove cached images older than 30 minutes."""
    now = time.time()
    expired = [k for k, v in image_cache.items() if now - v["timestamp"] > 1800]
    for k in expired:
        del image_cache[k]


# ---------------------------------------------------------------------------
# Google Drive upload
# ---------------------------------------------------------------------------
async def upload_to_gdrive(image_data: bytes, filename: str) -> str:
    """Upload image to Google Drive. Returns the file URL."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaInMemoryUpload

        creds = service_account.Credentials.from_service_account_file(
            GDRIVE_CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        service = build("drive", "v3", credentials=creds)

        file_metadata = {"name": filename}
        if GDRIVE_FOLDER_ID:
            file_metadata["parents"] = [GDRIVE_FOLDER_ID]

        media = MediaInMemoryUpload(image_data, mimetype="image/png")

        # Run in executor since Google API client is sync
        loop = asyncio.get_event_loop()
        file = await loop.run_in_executor(
            None,
            lambda: service.files().create(
                body=file_metadata, media_body=media, fields="id,webViewLink"
            ).execute()
        )

        file_id = file.get("id")
        # Make file viewable by anyone with the link
        await loop.run_in_executor(
            None,
            lambda: service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"}
            ).execute()
        )

        web_link = file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
        log.info(f"Uploaded to Google Drive: {web_link}")
        return web_link

    except ImportError:
        log.warning("Google Drive API not installed. Saving locally instead.")
        return None
    except FileNotFoundError:
        log.warning(f"Google Drive credentials not found at {GDRIVE_CREDENTIALS_FILE}. Saving locally.")
        return None
    except Exception as e:
        log.error(f"Google Drive upload failed: {e}")
        return None


async def save_image_local(image_data: bytes, filename: str) -> str:
    """Save image locally on server. Returns the file path."""
    filepath = os.path.join(SAVE_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(image_data)
    log.info(f"Image saved locally: {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_allowed(user_id: int) -> bool:
    """Check if user is allowed to use the bot."""
    if not ALLOWED_USERS:
        return True
    allowed = [int(x.strip()) for x in ALLOWED_USERS.split(",") if x.strip()]
    return user_id in allowed


async def call_flow_server(prompt: str, image_base64: str = None) -> dict:
    """Call the flow_server /generate endpoint."""
    payload = {"prompt": prompt}
    if image_base64:
        payload["image_base64"] = image_base64

    async with httpx.AsyncClient(timeout=200.0) as client:
        resp = await client.post(f"{FLOW_SERVER_URL}/generate", json=payload)
        resp.raise_for_status()
        return resp.json()


async def download_image(url: str) -> bytes:
    """Download image from flow_server proxy."""
    if url.startswith("/api/"):
        url = f"{FLOW_SERVER_URL}/{url[5:]}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        "Xin chao! Toi la bot tao anh Google Flow.\n\n"
        "Cach su dung:\n"
        "1. Gui anh mau kem caption (mo ta) de tao anh moi\n"
        "2. Gui text de tao anh khong can anh mau\n\n"
        "Vi du: Gui anh con ga + caption 'doi mau sang do'\n\n"
        "Moi anh ket qua se co nut 'Luu ve Drive' de luu anh.\n\n"
        "Lenh:\n"
        "/start - Huong dan\n"
        "/status - Kiem tra server\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check flow_server health."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{FLOW_SERVER_URL}/health")
            data = resp.json()
            status = "OK" if data.get("browser_ready") else "Chua san sang"

            # Check Google Drive
            gdrive_status = "Chua cau hinh"
            if os.path.exists(GDRIVE_CREDENTIALS_FILE) and GDRIVE_FOLDER_ID:
                gdrive_status = "Da cau hinh"

            await update.message.reply_text(
                f"Flow Server: {status}\n"
                f"Browser: {'Ready' if data.get('browser_ready') else 'Not ready'}\n"
                f"Google Drive: {gdrive_status}\n"
                f"Cached images: {len(image_cache)}"
            )
    except Exception as e:
        await update.message.reply_text(f"Server khong phan hoi: {e}")


# ---------------------------------------------------------------------------
# Send image with Save button
# ---------------------------------------------------------------------------
async def send_image_with_save_button(
    update: Update, img_data: bytes, index: int, total: int, prompt: str
):
    """Send a photo with an inline 'Save to Drive' button."""
    # Generate unique callback ID and cache the image data
    callback_id = str(uuid.uuid4())[:8]
    image_cache[callback_id] = {
        "data": img_data,
        "prompt": prompt,
        "timestamp": time.time(),
    }
    cleanup_cache()

    # Create inline keyboard with Save button
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💾 Luu ve Drive",
                callback_data=f"save:{callback_id}"
            ),
            InlineKeyboardButton(
                "📥 Tai ve",
                callback_data=f"download:{callback_id}"
            ),
        ]
    ])

    await update.message.reply_photo(
        photo=img_data,
        caption=f"Ket qua {index}/{total} - {prompt[:100]}",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Callback handler for inline buttons
# ---------------------------------------------------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses (Save to Drive, Download)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data:
        return

    action, callback_id = data.split(":", 1)
    cached = image_cache.get(callback_id)

    if not cached:
        await query.edit_message_caption(
            caption=query.message.caption + "\n\n⚠ Anh da het han. Vui long tao lai."
        )
        return

    img_data = cached["data"]
    prompt = cached["prompt"]
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"flow_{timestamp}_{callback_id}.png"

    if action == "save":
        # Try Google Drive first, fallback to local
        await query.edit_message_caption(
            caption=query.message.caption + "\n\n⏳ Dang luu ve Drive..."
        )

        drive_url = await upload_to_gdrive(img_data, filename)

        if drive_url:
            # Update caption with Drive link
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Mo trong Drive", url=drive_url)],
                [InlineKeyboardButton("📥 Tai ve", callback_data=f"download:{callback_id}")],
            ])
            await query.edit_message_caption(
                caption=query.message.caption.split("\n\n⏳")[0] + f"\n\n✅ Da luu vao Drive!",
                reply_markup=keyboard,
            )
        else:
            # Fallback: save locally and send as document
            filepath = await save_image_local(img_data, filename)
            await query.edit_message_caption(
                caption=query.message.caption.split("\n\n⏳")[0] + f"\n\n✅ Da luu: {filepath}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📥 Tai ve", callback_data=f"download:{callback_id}")],
                ]),
            )

    elif action == "download":
        # Send as document (full quality, downloadable)
        await query.message.reply_document(
            document=img_data,
            filename=filename,
            caption=f"📥 {filename}",
        )


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages (with optional caption as prompt)."""
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("Ban khong co quyen su dung bot nay.")
        return

    prompt = update.message.caption or ""
    if not prompt.strip():
        await update.message.reply_text(
            "Vui long gui anh kem caption (mo ta).\n"
            "Vi du: Gui anh + caption 'doi mau sang do'"
        )
        return

    photo = update.message.photo[-1]
    status_msg = await update.message.reply_text(
        f"⏳ Dang tao anh voi Google Flow...\n📝 Prompt: {prompt}\n⏱ Vui long doi 30-90 giay..."
    )

    try:
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        image_base64 = base64.b64encode(bytes(photo_bytes)).decode()

        log.info(f"User {user.id} ({user.username}): photo + prompt='{prompt[:50]}...'")

        start_time = time.time()
        result = await call_flow_server(prompt, image_base64)
        elapsed = time.time() - start_time

        images = result.get("images", [])
        if not images:
            await status_msg.edit_text("❌ Khong nhan duoc anh tu Google Flow. Thu lai sau.")
            return

        await status_msg.edit_text(
            f"✅ Da tao {len(images)} anh trong {result.get('elapsed_seconds', elapsed):.0f}s. Dang gui..."
        )

        for i, img_url in enumerate(images):
            try:
                img_data = await download_image(img_url)
                await send_image_with_save_button(
                    update, img_data, i + 1, len(images), prompt
                )
            except Exception as e:
                log.error(f"Failed to send image {i+1}: {e}")
                await update.message.reply_text(f"Loi gui anh {i+1}: {e}")

        await status_msg.delete()

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text[:200] if e.response else str(e)
        log.error(f"Flow server error: {error_detail}")
        await status_msg.edit_text(f"❌ Loi server: {error_detail}")
    except Exception as e:
        log.error(f"Generation error: {e}")
        await status_msg.edit_text(f"❌ Loi: {str(e)[:200]}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text-only messages (generate without reference image)."""
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("Ban khong co quyen su dung bot nay.")
        return

    prompt = update.message.text.strip()
    if not prompt:
        return

    if prompt.startswith("/"):
        return

    status_msg = await update.message.reply_text(
        f"⏳ Dang tao anh voi Google Flow...\n📝 Prompt: {prompt}\n⏱ Vui long doi 30-90 giay..."
    )

    try:
        log.info(f"User {user.id} ({user.username}): text prompt='{prompt[:50]}...'")

        start_time = time.time()
        result = await call_flow_server(prompt)
        elapsed = time.time() - start_time

        images = result.get("images", [])
        if not images:
            await status_msg.edit_text("❌ Khong nhan duoc anh tu Google Flow. Thu lai sau.")
            return

        await status_msg.edit_text(
            f"✅ Da tao {len(images)} anh trong {result.get('elapsed_seconds', elapsed):.0f}s. Dang gui..."
        )

        for i, img_url in enumerate(images):
            try:
                img_data = await download_image(img_url)
                await send_image_with_save_button(
                    update, img_data, i + 1, len(images), prompt
                )
            except Exception as e:
                log.error(f"Failed to send image {i+1}: {e}")
                await update.message.reply_text(f"Loi gui anh {i+1}: {e}")

        await status_msg.delete()

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text[:200] if e.response else str(e)
        log.error(f"Flow server error: {error_detail}")
        await status_msg.edit_text(f"❌ Loi server: {error_detail}")
    except Exception as e:
        log.error(f"Generation error: {e}")
        await status_msg.edit_text(f"❌ Loi: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        log.error("Please set TELEGRAM_BOT_TOKEN environment variable!")
        return

    log.info("Starting Telegram bot...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
