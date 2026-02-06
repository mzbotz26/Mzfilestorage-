import logging
import re
import time
from pyrogram import Client, filters, enums
from pyrogram.errors import UserNotParticipant, MessageNotModified, MessageDeleteForbidden, UserIsBlocked
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import Config
from database.db import add_user, get_file_by_unique_id, get_user, update_user, record_daily_view
from utils.helpers import get_main_menu
from features.shortener import get_shortlink

logger = logging.getLogger(__name__)

# ================= VERIFY HELPERS =================

VERIFY_TIME = 86400  # 24 hours

async def is_verified_24h(user):
    return user and user.get("verified_until", 0) > int(time.time())

async def set_verified_24h(user_id):
    expires = int(time.time()) + VERIFY_TIME
    await update_user(user_id, "verified_until", expires)

# =================================================


# ================= PRIVATE UPLOAD =================

@Client.on_message(filters.private & ~filters.command("start") & (filters.document | filters.video | filters.audio))
async def handle_private_file(client, message):
    if not client.owner_db_channel:
        return await message.reply_text("Bot is not configured yet.")

    if not Config.APP_URL:
        return await message.reply_text("Streaming service not configured.")

    processing = await message.reply_text("⏳ Processing your file...")

    try:
        media = getattr(message, message.media.value, None)
        if not media:
            return await processing.edit_text("No media found.")

        copied = await message.copy(client.owner_db_channel)

        from database.db import save_file_data
        await save_file_data(message.from_user.id, message, copied, copied)

        buttons = [
            [InlineKeyboardButton("📺 Stream / Download", url=f"{Config.APP_URL.rstrip('/')}/watch/{copied.id}")]
        ]

        file_name = getattr(media, "file_name", "file")

        await client.send_cached_media(
            chat_id=message.chat.id,
            file_id=media.file_id,
            caption=f"`{file_name}`",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        await processing.delete()

    except UserIsBlocked:
        await processing.delete()
    except Exception as e:
        await processing.edit_text(f"Error: {e}")

# =================================================


@Client.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    if message.from_user.is_bot:
        return

    requester_id = message.from_user.id
    await add_user(requester_id)

    if len(message.command) > 1:
        payload = message.command[1]

        # -------- VERIFY CALLBACK --------
        if payload == "verify":
            await set_verified_24h(requester_id)
            return await message.reply_text(
                "✅ **Verification Successful!**\n\nYou now have access for **24 hours**.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎬 How To Download", url=Config.TUTORIAL_URL)]
                ])
            )

        try:
            if payload.startswith("get_"):
                await handle_public_file_request(client, message, requester_id, payload)

            elif payload.startswith("ownerget_"):
                _, owner_id_str, file_unique_id = payload.split("_", 2)
                owner_id = int(owner_id_str)
                if requester_id == owner_id:
                    await send_file(client, requester_id, owner_id, file_unique_id)
                else:
                    await message.reply_text("This is a special link for the file owner only.")

        except Exception:
            await message.reply_text("Something went wrong or the link is invalid.")

    else:
        text = (
            f"Hello {message.from_user.mention}! 👋\n\n"
            "Welcome to your **Movie File Bot**.\n\n"
            "Verify once & enjoy files for 24 hours."
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Let's Go 🚀", callback_data=f"go_back_{requester_id}"),
                InlineKeyboardButton("Tutorial 🎬", url=Config.TUTORIAL_URL)
            ]
        ])

        await message.reply_text(text, reply_markup=keyboard)


# ================= PUBLIC FILE HANDLER =================

async def handle_public_file_request(client, message, requester_id, payload):
    try:
        _, owner_id_str, file_unique_id = payload.split("_", 2)
        owner_id = int(owner_id_str)
    except:
        return await message.reply_text("Invalid link.")

    owner_settings = await get_user(owner_id)
    requester = await get_user(requester_id)

    # OWNER BYPASS
    if requester_id != owner_id:
        if not await is_verified_24h(requester):
            verify_link = await get_shortlink(f"https://t.me/{client.me.username}?start=verify", requester_id)

            return await message.reply_text(
                "🔐 **Verification Required**\n\nVerify once to unlock files for **24 hours**.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Verify Now", url=verify_link)],
                    [InlineKeyboardButton("🎬 How To Download", url=Config.TUTORIAL_URL)]
                ])
            )

    file_data = await get_file_by_unique_id(owner_id, file_unique_id)
    if not file_data:
        return await message.reply_text("File not found.")

    # ---------- FSUB ----------
    fsub_channel = owner_settings.get("fsub_channel") if owner_settings else None
    if fsub_channel:
        try:
            await client.get_chat_member(fsub_channel, requester_id)
        except UserNotParticipant:
            invite = await client.export_chat_invite_link(fsub_channel)
            return await message.reply_text(
                "📢 Join channel first:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Join Channel", url=invite)],
                    [InlineKeyboardButton("Retry", callback_data=f"retry_{payload}")]
                ])
            )

    await send_file(client, requester_id, owner_id, file_unique_id)


# ================= SEND FILE =================

async def send_file(client, requester_id, owner_id, file_unique_id):
    file_data = await get_file_by_unique_id(owner_id, file_unique_id)
    if not file_data:
        return

    await record_daily_view(owner_id, requester_id)

    buttons = [
        [InlineKeyboardButton("📺 Stream / Download", url=f"{Config.APP_URL.rstrip('/')}/watch/{file_data['stream_id']}")]
    ]

    await client.copy_message(
        chat_id=requester_id,
        from_chat_id=client.owner_db_channel,
        message_id=file_data["file_id"],
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN
    )


# ================= CALLBACKS =================

@Client.on_callback_query(filters.regex(r"^retry_"))
async def retry_handler(client, query):
    try:
        await query.message.delete()
    except (MessageDeleteForbidden, MessageNotModified):
        pass
    await handle_public_file_request(client, query.message, query.from_user.id, query.data.split("_", 1)[1])


@Client.on_callback_query(filters.regex(r"go_back_"))
async def go_back_callback(client, query):
    user_id = int(query.data.split("_")[-1])
    menu_text, menu_markup = await get_main_menu(user_id)
    await query.message.edit_text(menu_text, reply_markup=menu_markup, parse_mode=enums.ParseMode.MARKDOWN)
