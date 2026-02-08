# util/file_properties.py

from pyrogram import Client
from typing import Any
from pyrogram.types import Message
from pyrogram.file_id import FileId

class FileIdError(Exception):
    pass

def get_media_from_message(message: "Message") -> Any:
    media_types = (
        "audio",
        "document",
        "photo",
        "sticker",
        "animation",
        "video",
        "voice",
        "video_note",
    )
    for attr in media_types:
        media = getattr(message, attr, None)
        if media:
            return media
    return None

# --- FINAL SAFE VERSION (LOGIC UNCHANGED, ISSUE FIXED) ---
async def get_message_with_properties(client: Client, message_id: int) -> Message | None:
    """
    Fetches the message from the storage channel and returns the full Message object.

    IMPORTANT:
    - No exception is raised for missing/deleted messages
    - Prevents /stream 500 errors
    - Prevents MXPlayer/VLC infinite retry loop
    """

    stream_channel = client.stream_channel_id or client.owner_db_channel
    if not stream_channel:
        return None

    try:
        message = await client.get_messages(
            chat_id=stream_channel,
            message_ids=message_id
        )
    except Exception:
        return None

    # Pyrogram safety checks
    if (
        not message
        or getattr(message, "empty", False)
        or not message.media
    ):
        return None

    return message
