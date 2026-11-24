# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)


import asyncio
import os
from pathlib import Path

from aiohttp import web

import power
from app_settings import get_http_port
from database.db import get_media, connect
from server.upnp import (
    handle_content_directory,
    content_directory_description,
    sort_subtitles
)
from server.device import DEVICE_UUID
from server.ssdp import get_local_ip_or_none


HOST = "0.0.0.0"
PORT = get_http_port()

CHUNK_SIZE = 256 * 1024

# The Samsung subtitle URL (/subtitle/ID) keeps its original type.
# The URL with a file name (/subtitle/ID/es.srt) is the one that
# standard clients get, and is announced as text/srt.
SUBTITLE_MIME_SAMSUNG = "application/x-subrip; charset=utf-8"
SUBTITLE_MIME_STANDARD = "text/srt; charset=utf-8"

DLNA_FLAGS = (
    "DLNA.ORG_OP=01;"
    "DLNA.ORG_CI=0;"
    "DLNA.ORG_FLAGS=01700000000000000000000000000000"
)

SUPPORTED_VIDEO_MIME_TYPES = (
    "video/x-mkv",
    "video/mp4",
    "video/x-msvideo",
    "video/quicktime",
    "video/mpeg",
    "video/mp2t",
    "video/x-ms-wmv",
    "video/webm",
    "video/3gpp",
    "video/3gpp2"
)

SOURCE_PROTOCOL_INFO = ",".join(
    f"http-get:*:{mime_type}:{DLNA_FLAGS}"
    for mime_type in SUPPORTED_VIDEO_MIME_TYPES
)


CONNECTION_MANAGER_SCPD = f"""<?xml version="1.0" encoding="UTF-8"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
    <specVersion>
        <major>1</major>
        <minor>0</minor>
    </specVersion>
    <actionList>
        <action>
            <name>GetProtocolInfo</name>
            <argumentList>
                <argument>
                    <name>Source</name>
                    <direction>out</direction>
                    <relatedStateVariable>SourceProtocolInfo</relatedStateVariable>
                </argument>
                <argument>
                    <name>Sink</name>
                    <direction>out</direction>
                    <relatedStateVariable>SinkProtocolInfo</relatedStateVariable>
                </argument>
            </argumentList>
        </action>
        <action>
            <name>GetCurrentConnectionIDs</name>
            <argumentList>
                <argument>
                    <name>ConnectionIDs</name>
                    <direction>out</direction>
                    <relatedStateVariable>CurrentConnectionIDs</relatedStateVariable>
                </argument>
            </argumentList>
        </action>
        <action>
            <name>GetCurrentConnectionInfo</name>
            <argumentList>
                <argument>
                    <name>ConnectionID</name>
                    <direction>in</direction>
                    <relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable>
                </argument>
            </argumentList>
        </action>
    </actionList>
    <serviceStateTable>
        <stateVariable sendEvents="no">
            <name>A_ARG_TYPE_ProtocolInfo</name>
            <dataType>string</dataType>
        </stateVariable>
        <stateVariable sendEvents="no">
            <name>A_ARG_TYPE_ConnectionStatus</name>
            <dataType>string</dataType>
            <defaultValue>OK</defaultValue>
        </stateVariable>
        <stateVariable sendEvents="no">
            <name>A_ARG_TYPE_AVTransportID</name>
            <dataType>i4</dataType>
            <defaultValue>-1</defaultValue>
        </stateVariable>
        <stateVariable sendEvents="no">
            <name>A_ARG_TYPE_RcsID</name>
            <dataType>i4</dataType>
            <defaultValue>-1</defaultValue>
        </stateVariable>
        <stateVariable sendEvents="no">
            <name>A_ARG_TYPE_ConnectionID</name>
            <dataType>i4</dataType>
            <defaultValue>-1</defaultValue>
        </stateVariable>
        <stateVariable sendEvents="no">
            <name>A_ARG_TYPE_ConnectionManager</name>
            <dataType>string</dataType>
        </stateVariable>
        <stateVariable sendEvents="no">
            <name>A_ARG_TYPE_Direction</name>
            <dataType>string</dataType>
            <defaultValue>Output</defaultValue>
        </stateVariable>
        <stateVariable sendEvents="yes">
            <name>SourceProtocolInfo</name>
            <dataType>string</dataType>
            <defaultValue>{SOURCE_PROTOCOL_INFO}</defaultValue>
        </stateVariable>
        <stateVariable sendEvents="yes">
            <name>SinkProtocolInfo</name>
            <dataType>string</dataType>
            <defaultValue></defaultValue>
        </stateVariable>
        <stateVariable sendEvents="yes">
            <name>CurrentConnectionIDs</name>
            <dataType>string</dataType>
            <defaultValue>0</defaultValue>
        </stateVariable>
    </serviceStateTable>
</scpd>
"""


def parse_range(range_header, file_size):

    if not range_header:
        return None

    if not range_header.startswith("bytes="):
        raise ValueError("Invalid Range unit")

    value = range_header[6:].strip()

    if "," in value:
        raise ValueError("Multiple ranges are not supported")

    if "-" not in value:
        raise ValueError("Invalid range")

    start_text, end_text = value.split("-", 1)

    if not start_text:
        try:
            suffix_length = int(end_text)
        except ValueError:
            raise ValueError("Invalid range")

        if suffix_length <= 0 or file_size == 0:
            raise ValueError("Invalid range")

        start = max(0, file_size - suffix_length)
        end = file_size - 1

    else:
        try:
            start = int(start_text)
        except ValueError:
            raise ValueError("Invalid range")

        if end_text:
            try:
                end = int(end_text)
            except ValueError:
                raise ValueError("Invalid range")
        else:
            end = file_size - 1

        if start < 0 or end < start or start >= file_size:
            raise ValueError("Range out of bounds")

        end = min(end, file_size - 1)

    if file_size == 0:
        raise ValueError("Empty file")

    return start, end


async def stream_file(request, file_path, content_type,
                      extra_headers=None, keep_awake=False):

    # keep_awake: while the file is being sent, ask Windows not to go to
    # sleep (see power.py). Used for media files, not for small subtitles.

    file_path = Path(file_path)

    if not file_path.is_file():
        raise web.HTTPNotFound(text="File not found")

    file_size = file_path.stat().st_size

    headers = {
        "Content-Type": content_type,
        "Accept-Ranges": "bytes",
        "Connection": "keep-alive",
        "transferMode.dlna.org": "Streaming"
    }

    if extra_headers:
        headers.update(extra_headers)

    range_header = request.headers.get("Range")

    try:
        byte_range = parse_range(range_header, file_size)
    except ValueError:
        return web.Response(
            status=416,
            headers={
                "Content-Range": f"bytes */{file_size}",
                "Accept-Ranges": "bytes"
            }
        )

    if byte_range:
        start, end = byte_range
        content_length = end - start + 1

        headers["Content-Range"] = (
            f"bytes {start}-{end}/{file_size}"
        )

        status = 206

    else:
        start = 0
        content_length = file_size
        status = 200

    headers["Content-Length"] = str(content_length)

    response = web.StreamResponse(
        status=status,
        headers=headers
    )

    if request.method == "HEAD":
        try:
            await response.prepare(request)
            await response.write_eof()
        except (ConnectionResetError, BrokenPipeError,
                ConnectionError, OSError):
            pass

        return response

    file_handle = None

    if keep_awake:
        power.stream_started()

    try:
        file_handle = await asyncio.to_thread(
            open,
            file_path,
            "rb"
        )

        if start:
            await asyncio.to_thread(
                file_handle.seek,
                start
            )

        await response.prepare(request)

        remaining = content_length

        while remaining > 0:

            block_size = min(CHUNK_SIZE, remaining)

            chunk = await asyncio.to_thread(
                file_handle.read,
                block_size
            )

            if not chunk:
                break

            await response.write(chunk)

            remaining -= len(chunk)

        await response.write_eof()

    except (ConnectionResetError, BrokenPipeError,
            ConnectionError, OSError):

        pass

    finally:
        # Always report the end of the transfer, even if the client
        # disconnects or the request is cancelled.
        if keep_awake:
            power.stream_finished()

        if file_handle is not None:
            await asyncio.to_thread(file_handle.close)

    return response


def get_first_subtitle_id(media_id, video_path):

    conn = connect()

    try:
        rows = conn.execute(
            """
            SELECT id, subtitle_path
            FROM subtitles
            WHERE media_id = ?
            """,
            (media_id,)
        ).fetchall()

    finally:
        conn.close()

    for subtitle_id, subtitle_path in sort_subtitles(
        video_path,
        rows
    ):
        if Path(subtitle_path).is_file():
            return subtitle_id

    return None


async def stream_media(request):

    try:
        media_id = int(request.match_info["media_id"])
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid media ID")

    media = await asyncio.to_thread(get_media, media_id)

    if not media:
        raise web.HTTPNotFound(
            text="Media file not found"
        )

    file_path = Path(media[2])

    extra_headers = {
        "contentFeatures.dlna.org": DLNA_FLAGS
    }

    # Vendor DLNA extension: some TVs send "getCaptionInfo.sec: 1" to ask
    # for the URL of an external subtitle, which is returned in the
    # "CaptionInfo.sec" response header.
    if request.headers.get("getCaptionInfo.sec", "").strip() == "1":

        subtitle_id = await asyncio.to_thread(
            get_first_subtitle_id,
            media_id,
            file_path
        )

        if subtitle_id is not None:

            # The address the client used to reach this server.
            subtitle_url = (
                f"http://{request.url.host}:{PORT}"
                f"/subtitle/{subtitle_id}"
            )

            extra_headers["CaptionInfo.sec"] = subtitle_url

            print(
                f"[SUBTITLE] Client requested subtitle for "
                f"media {media_id}: {subtitle_url}"
            )

    return await stream_file(
        request,
        file_path,
        media[5] or "application/octet-stream",
        extra_headers,
        keep_awake=True
    )


async def stream_subtitle(request):

    try:
        subtitle_id = int(
            request.match_info["subtitle_id"]
        )
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid subtitle ID")

    def query_subtitle():

        conn = connect()

        try:
            return conn.execute(
                """
                SELECT subtitle_path
                FROM subtitles
                WHERE id = ?
                """,
                (subtitle_id,)
            ).fetchone()

        finally:
            conn.close()

    subtitle = await asyncio.to_thread(query_subtitle)

    if not subtitle:
        raise web.HTTPNotFound(
            text="Subtitle not found"
        )

    file_path = Path(subtitle[0])

    if "name" in request.match_info:
        subtitle_mime = SUBTITLE_MIME_STANDARD
    else:
        subtitle_mime = SUBTITLE_MIME_SAMSUNG

    return await stream_file(
        request,
        file_path,
        subtitle_mime,
        {
            "Content-Disposition": "inline"
        }
    )


async def device_description(request):

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"
      xmlns:dlna="urn:schemas-dlna-org:device-1-0">
    <specVersion>
        <major>1</major>
        <minor>0</minor>
    </specVersion>
    <device>
        <dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>
        <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
        <friendlyName>Easy DLNA Server</friendlyName>
        <manufacturer>devaser</manufacturer>
        <modelName>Easy DLNA Server</modelName>
        <modelNumber>0.3</modelNumber>
        <UDN>{DEVICE_UUID}</UDN>
        <serviceList>
            <service>
                <serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>
                <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
                <SCPDURL>/ContentDirectory.xml</SCPDURL>
                <controlURL>/upnp/contentdirectory</controlURL>
                <eventSubURL>/upnp/events/contentdirectory</eventSubURL>
            </service>
            <service>
                <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
                <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
                <SCPDURL>/ConnectionManager.xml</SCPDURL>
                <controlURL>/upnp/connectionmanager</controlURL>
                <eventSubURL>/upnp/events/connectionmanager</eventSubURL>
            </service>
        </serviceList>
    </device>
</root>
"""

    return web.Response(
        text=xml,
        content_type="application/xml",
        charset="utf-8"
    )


async def connection_manager_description(request):

    return web.Response(
        text=CONNECTION_MANAGER_SCPD,
        content_type="text/xml",
        charset="utf-8"
    )


async def connection_manager_control(request):

    body = await request.text()

    if "GetProtocolInfo" in body:
        response_name = "GetProtocolInfoResponse"
        result = f"""
            <Source>{SOURCE_PROTOCOL_INFO}</Source>
            <Sink></Sink>
        """

    elif "GetCurrentConnectionIDs" in body:
        response_name = "GetCurrentConnectionIDsResponse"
        result = "<ConnectionIDs>0</ConnectionIDs>"

    elif "GetCurrentConnectionInfo" in body:
        response_name = "GetCurrentConnectionInfoResponse"
        result = f"""
            <RcsID>-1</RcsID>
            <AVTransportID>-1</AVTransportID>
            <ProtocolInfo>http-get:*:video/x-mkv:{DLNA_FLAGS}</ProtocolInfo>
            <PeerConnectionManager></PeerConnectionManager>
            <PeerConnectionID>-1</PeerConnectionID>
            <Direction>Output</Direction>
            <Status>OK</Status>
        """

    else:
        raise web.HTTPBadRequest(
            text="Unsupported ConnectionManager action"
        )

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
            s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
    <s:Body>
        <u:{response_name} xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">
            {result}
        </u:{response_name}>
    </s:Body>
</s:Envelope>
"""

    return web.Response(
        text=xml,
        content_type="text/xml",
        headers={
            "EXT": "",
            "Server": "Python/3 UPnP/1.0 EasyDLNA/0.3",
            "Content-Length": str(len(xml.encode("utf-8")))
        }
    )


async def health(request):

    return web.json_response({
        "server": "Easy DLNA Server",
        "status": "running",
        "version": "0.3"
    })


# Debugging aid, off by default: set EASYDLNA_DEBUG_HTTP=1 to log every
# HTTP request (path, Range, User-Agent and the Browse exchange), which
# shows what a TV asks for.
DEBUG_HTTP = os.environ.get("EASYDLNA_DEBUG_HTTP") == "1"


@web.middleware
async def log_requests(request, handler):

    if request.path == "/health":
        return await handler(request)

    agent = request.headers.get("User-Agent", "-")
    byte_range = request.headers.get("Range", "-")

    print(
        f"[HTTP] {request.remote} {request.method} "
        f"{request.path_qs} range={byte_range} agent={agent}",
        flush=True
    )

    is_browse = request.path == "/upnp/contentdirectory"

    if is_browse:
        try:
            body = await request.text()
            print(
                "[HTTP]   SOAPAction="
                f"{request.headers.get('SOAPAction', '-')}",
                flush=True
            )
            print(f"[HTTP]   request body: {body[:1200]}", flush=True)
        except Exception as error:
            print(f"[HTTP]   cannot read body: {error}", flush=True)

    try:
        response = await handler(request)

    except web.HTTPException as error:
        print(
            f"[HTTP]   -> {error.status} for {request.path}",
            flush=True
        )
        raise

    except Exception as error:
        print(
            f"[HTTP]   -> ERROR for {request.path}: {error!r}",
            flush=True
        )
        raise

    print(
        f"[HTTP]   -> {response.status} "
        f"{response.content_type} for {request.path}",
        flush=True
    )

    if is_browse:
        response_text = getattr(response, "text", None)

        if response_text and "/subtitle/" in response_text:
            print(
                f"[HTTP]   response with subtitles: "
                f"{response_text[:5000]}",
                flush=True
            )

    return response


def create_app():

    middlewares = [log_requests] if DEBUG_HTTP else []

    app = web.Application(middlewares=middlewares)

    app.router.add_get("/health", health)
    app.router.add_get("/device.xml", device_description)

    app.router.add_get(
        "/ContentDirectory.xml",
        content_directory_description
    )

    app.router.add_get(
        "/ConnectionManager.xml",
        connection_manager_description
    )

    app.router.add_post(
        "/upnp/contentdirectory",
        handle_content_directory
    )

    app.router.add_post(
        "/upnp/connectionmanager",
        connection_manager_control
    )

    app.router.add_get(
        "/media/{media_id}",
        stream_media
    )

    app.router.add_get(
        "/subtitle/{subtitle_id}",
        stream_subtitle
    )

    app.router.add_get(
        "/subtitle/{subtitle_id}/{name}",
        stream_subtitle
    )

    return app


async def start_http_server():

    loop = asyncio.get_running_loop()
    previous_exception_handler = loop.get_exception_handler()

    def handle_connection_exception(loop, context):

        exception = context.get("exception")
        message = context.get("message", "")
        handle = repr(context.get("handle", ""))

        # Windows may report a normal remote disconnect here.
        # Ignore only this specific Proactor callback error.
        if (
            isinstance(exception, ConnectionResetError)
            and "_call_connection_lost" in f"{message} {handle}"
        ):
            return

        if previous_exception_handler is not None:
            previous_exception_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handle_connection_exception)

    app = create_app()

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        HOST,
        PORT
    )

    await site.start()

    print(
        f"[HTTP] Server started on port {PORT}"
    )

    local_address = get_local_ip_or_none()

    print(
        f"[HTTP] Local address: "
        f"{local_address or 'not available yet (waiting for the network)'}"
    )

    return runner
