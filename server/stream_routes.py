# server/stream_routes.py

import logging
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError
from util.render_template import render_player_page
from util.custom_dl import ByteStreamer
from util.file_properties import get_media_from_message
from pyrogram.errors import RPCError

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response({
        "server_status": "running",
        "bot_status": "connected"
    })


@routes.get("/favicon.ico", allow_head=True)
async def favicon_handler(request):
    return web.Response(status=204)


@routes.get("/watch/{message_id}", allow_head=True)
async def watch_handler(request):
    try:
        message_id = int(request.match_info["message_id"])
        bot = request.app["bot"]

        content = await render_player_page(bot, message_id)
        return web.Response(
            text=content,
            content_type="text/html"
        )

    except Exception as e:
        logger.error(f"Error in watch_handler: {e}", exc_info=True)
        return web.Response(
            text="<h1>500 - Internal Server Error</h1><p>Could not render the page.</p>",
            content_type="text/html",
            status=500
        )


# ================= STREAM =================

@routes.get(r"/stream/{message_id:\d+}")
async def stream_handler(request):
    bot = request.app["bot"]

    try:
        message_id = int(request.match_info["message_id"])
        streamer = ByteStreamer(bot)

        message = await streamer.get_file_properties(message_id)
        if not message:
            return web.Response(status=404, text="File not found.")

        media = get_media_from_message(message)
        if not media:
            return web.Response(status=404, text="Media not available.")

        file_size = media.file_size
        file_name = media.file_name or "video.mp4"
        range_header = request.headers.get("Range")

        start = 0
        end = file_size - 1
        status = 200

        if range_header:
            status = 206
            bytes_range = range_header.replace("bytes=", "").split("-")
            start = int(bytes_range[0])
            if len(bytes_range) > 1 and bytes_range[1]:
                end = int(bytes_range[1])

        headers = {
            "Content-Type": media.mime_type or "video/mp4",
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Disposition": f'inline; filename="{file_name}"'
        }

        resp = web.StreamResponse(status=status, headers=headers)
        await resp.prepare(request)

        async for chunk in bot.stream_media(
            message,
            offset=start,
            limit=1024 * 1024
        ):
            try:
                await resp.write(chunk)
            except (
                ClientConnectionResetError,
                ConnectionResetError,
                BrokenPipeError,
                ConnectionError
            ):
                break

        return resp

    except RPCError:
        return web.Response(status=404, text="Telegram file inaccessible.")

    except Exception as e:
        logger.exception("Stream error")
        return web.Response(status=500, text="Stream failed.")


# ================= DOWNLOAD =================

@routes.get(r"/download/{message_id:\d+}")
async def download_handler(request):
    bot = request.app["bot"]

    try:
        message_id = int(request.match_info["message_id"])
        streamer = ByteStreamer(bot)

        # 1️⃣ Fetch message safely
        message = await streamer.get_file_properties(message_id)
        if not message:
            return web.Response(status=404, text="File not found or expired.")

        media = get_media_from_message(message)
        if not media:
            return web.Response(status=404, text="Media not available.")

        res = web.StreamResponse(
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(media.file_size),
                "Content-Disposition": f'attachment; filename="{media.file_name or "file"}"'
            }
        )
        await res.prepare(request)

        async for chunk in bot.stream_media(message, limit=1024 * 1024):
            try:
                await res.write(chunk)
            except (
                ClientConnectionResetError,
                ConnectionResetError,
                BrokenPipeError,
                ConnectionError
            ):
                logger.info(f"Client disconnected (download) for message_id {message_id}")
                break

        return res

    except RPCError as e:
        logger.error(f"Telegram RPCError in download_handler: {e}", exc_info=True)
        return web.Response(status=404, text="File not accessible on Telegram.")

    except Exception as e:
        logger.error(f"Error in download_handler: {e}", exc_info=True)
        return web.Response(status=500, text="Internal server error.")
