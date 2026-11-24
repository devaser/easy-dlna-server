# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)


import asyncio
import base64
import re
import xml.etree.ElementTree as ET

from pathlib import Path
from aiohttp import web

from database.db import (
    connect,
    get_shared_folders,
    get_playback_bookmark,
    save_playback_bookmark
)
from app_settings import get_http_port
from server.ssdp import get_local_ip


SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
CD_NS = "urn:schemas-upnp-org:service:ContentDirectory:1"
DIDL_NS = "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
DC_NS = "http://purl.org/dc/elements/1.1/"
UPNP_NS = "urn:schemas-upnp-org:metadata-1-0/upnp/"
SEC_NS = "http://www.sec.co.kr/dlna"

PORT = get_http_port()

VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".m4v",
    ".avi",
    ".mov",
    ".mpg",
    ".mpeg",
    ".mpe",
    ".m2v",
    ".vob",
    ".ts",
    ".mts",
    ".m2ts",
    ".wmv",
    ".webm",
    ".3gp",
    ".3g2"
}

MINIMUM_BOOKMARK_SECONDS = 30

# Standard (non-Samsung) clients read external subtitles from an extra
# <res> element of the video item.
SUBTITLE_PROTOCOL_INFO = "http-get:*:text/srt:*"

ET.register_namespace("s", SOAP_NS)
ET.register_namespace("u", CD_NS)
ET.register_namespace("", DIDL_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("upnp", UPNP_NS)
ET.register_namespace("sec", SEC_NS)


def qname(namespace, name):
    return f"{{{namespace}}}{name}"


def encode_path(path):

    encoded = base64.urlsafe_b64encode(
        str(path.resolve()).encode("utf-8")
    ).decode("ascii")

    return "d:" + encoded.rstrip("=")


def decode_path(object_id):

    if not object_id.startswith("d:"):
        raise ValueError("Invalid folder identifier")

    encoded = object_id[2:]

    padding = "=" * (-len(encoded) % 4)

    decoded = base64.urlsafe_b64decode(
        encoded + padding
    ).decode("utf-8")

    return Path(decoded).resolve()


def is_shared_folder(path, roots):

    path = path.resolve()

    for root in roots:

        root = root.resolve()

        try:
            path.relative_to(root)
            return True

        except ValueError:
            continue

    return False


def get_media_index(conn):

    rows = conn.execute(
        """
        SELECT id, title, file_path, file_size, mime_type
        FROM media
        """
    ).fetchall()

    result = {}

    for row in rows:

        media_id, title, file_path, size, mime_type = row

        normalized = str(
            Path(file_path).resolve()
        ).casefold()

        result[normalized] = {
            "id": media_id,
            "title": title,
            "path": file_path,
            "size": size,
            "mime": mime_type,
            "bookmark": get_playback_bookmark(media_id)
        }

    return result


def subtitle_suffix(video_path, subtitle_path):
    """
    Returns what follows the video name in a subtitle file name.
    "Movie.es.srt" -> "es", "Movie.srt" -> "".
    """

    video_stem = Path(video_path).stem
    subtitle_stem = Path(subtitle_path).stem

    if subtitle_stem.casefold().startswith(video_stem.casefold() + "."):
        return subtitle_stem[len(video_stem) + 1:]

    return ""


def sort_subtitles(video_path, rows):
    """
    rows: list of (subtitle_id, subtitle_path).
    The subtitle without a language suffix comes first, then the rest by
    name, so the "first" subtitle is always the same one.
    """

    def sort_key(row):
        suffix = subtitle_suffix(video_path, row[1])

        return (
            suffix != "",
            suffix.casefold(),
            Path(row[1]).name.casefold()
        )

    return sorted(rows, key=sort_key)


def subtitle_file_name(video_path, subtitle_path):
    """
    File name shown in the subtitle URL, with the language when the file
    has one: "es.srt", "en.forced.srt" or "subtitle.srt".
    """

    suffix = subtitle_suffix(video_path, subtitle_path)

    safe = re.sub(
        r"[^A-Za-z0-9_.-]",
        "_",
        suffix
    ).strip("._")[:40]

    return (safe or "subtitle") + ".srt"


def get_subtitle_entries(conn, media_id, video_path):

    rows = conn.execute(
        """
        SELECT id, subtitle_path
        FROM subtitles
        WHERE media_id = ?
        """,
        (media_id,)
    ).fetchall()

    return [
        (
            subtitle_id,
            subtitle_file_name(video_path, subtitle_path)
        )
        for subtitle_id, subtitle_path
        in sort_subtitles(video_path, rows)
    ]


def get_library(object_id, starting_index, requested_count):

    conn = connect()

    try:

        roots = get_shared_folders()

        if object_id == "0":

            entries = [{
                "id": "1",
                "parent_id": "0",
                "title": "Shared Folders",
                "type": "container"
            }]

        elif object_id == "1":

            entries = []

            for root in roots:

                entries.append({
                    "id": encode_path(root),
                    "parent_id": "1",
                    "title": root.name,
                    "type": "container"
                })

        elif object_id.startswith("d:"):

            folder = decode_path(object_id)

            if not is_shared_folder(folder, roots):
                raise ValueError(
                    "Folder is outside the shared directories"
                )

            if not folder.is_dir():
                raise ValueError("Folder does not exist")

            media_index = get_media_index(conn)

            entries = []

            try:

                children = sorted(
                    folder.iterdir(),
                    key=lambda item: (
                        not item.is_dir(),
                        item.name.casefold()
                    )
                )

            except OSError as error:
                raise ValueError(
                    f"Unable to read folder: {error}"
                )

            for child in children:

                if child.is_symlink():
                    continue

                if child.is_dir():

                    entries.append({
                        "id": encode_path(child),
                        "parent_id": object_id,
                        "title": child.name,
                        "type": "container"
                    })

                    continue

                if not child.is_file():
                    continue

                if child.suffix.casefold() not in VIDEO_EXTENSIONS:
                    continue

                normalized = str(
                    child.resolve()
                ).casefold()

                video = media_index.get(normalized)

                if video is None:
                    continue

                entries.append({
                    "id": str(video["id"]),
                    "parent_id": object_id,
                    "title": child.stem,
                    "type": "video",
                    "size": video["size"],
                    "mime": video["mime"],
                    "bookmark": video["bookmark"],
                    "subtitles": get_subtitle_entries(
                        conn,
                        video["id"],
                        video["path"]
                    )
                })

        else:
            raise ValueError("Object not found")

        total = len(entries)

        if requested_count == 0:
            selected = entries[starting_index:]
        else:
            selected = entries[
                starting_index:starting_index + requested_count
            ]

        return {
            "items": selected,
            "total": total
        }

    finally:
        conn.close()


def get_metadata(object_id):

    conn = connect()

    try:

        roots = get_shared_folders()

        if object_id == "0":

            return {
                "id": "0",
                "parent_id": "-1",
                "title": "Easy DLNA Server",
                "type": "container",
                "child_count": 1
            }

        if object_id == "1":

            return {
                "id": "1",
                "parent_id": "0",
                "title": "Shared Folders",
                "type": "container",
                "child_count": len(roots)
            }

        if object_id.startswith("d:"):

            folder = decode_path(object_id)

            if not is_shared_folder(folder, roots):
                raise ValueError(
                    "Folder is outside the shared directories"
                )

            if not folder.is_dir():
                raise ValueError("Folder does not exist")

            child_count = sum(
                1 for child in folder.iterdir()
                if not child.is_symlink()
                and (
                    child.is_dir()
                    or (
                        child.is_file()
                        and child.suffix.casefold() in VIDEO_EXTENSIONS
                    )
                )
            )

            parent = folder.parent

            if folder in roots:
                parent_id = "1"
            else:
                parent_id = encode_path(parent)

            return {
                "id": object_id,
                "parent_id": parent_id,
                "title": folder.name,
                "type": "container",
                "child_count": child_count
            }

        if object_id.isdigit():

            video = conn.execute(
                """
                SELECT id, file_path, file_size, mime_type
                FROM media
                WHERE id = ?
                """,
                (int(object_id),)
            ).fetchone()

            if video is None:
                raise ValueError("Video not found")

            media_id, file_path, size, mime_type = video

            path = Path(file_path).resolve()

            if path.suffix.casefold() not in VIDEO_EXTENSIONS:
                raise ValueError("Unsupported file format")

            if not is_shared_folder(path, roots):
                raise ValueError(
                    "Video is outside the shared directories"
                )

            return {
                "id": str(media_id),
                "parent_id": encode_path(path.parent),
                "title": path.stem,
                "type": "video",
                "size": size,
                "mime": mime_type,
                "bookmark": get_playback_bookmark(media_id),
                "subtitles": get_subtitle_entries(
                    conn,
                    media_id,
                    file_path
                )
            }

        raise ValueError("Object not found")

    finally:
        conn.close()


def create_didl(data, host):

    root = ET.Element(
        qname(DIDL_NS, "DIDL-Lite")
    )

    entries = data.get("items", [])

    if "type" in data:
        entries = [data]

    for entry in entries:

        if entry["type"] == "container":

            container = ET.SubElement(
                root,
                qname(DIDL_NS, "container"),
                {
                    "id": str(entry["id"]),
                    "parentID": str(entry["parent_id"]),
                    "restricted": "1",
                    "childCount": str(
                        entry.get("child_count", 0)
                    )
                }
            )

            ET.SubElement(
                container,
                qname(DC_NS, "title")
            ).text = entry["title"]

            ET.SubElement(
                container,
                qname(UPNP_NS, "class")
            ).text = "object.container.storageFolder"

        else:

            item = ET.SubElement(
                root,
                qname(DIDL_NS, "item"),
                {
                    "id": str(entry["id"]),
                    "parentID": str(entry["parent_id"]),
                    "restricted": "1"
                }
            )

            ET.SubElement(
                item,
                qname(DC_NS, "title")
            ).text = entry["title"]

            ET.SubElement(
                item,
                qname(UPNP_NS, "class")
            ).text = "object.item.videoItem"

            ET.SubElement(
                item,
                qname(DIDL_NS, "res"),
                {
                    "protocolInfo": (
                        f"http-get:*:{entry['mime']}:"
                        "DLNA.ORG_OP=01;"
                        "DLNA.ORG_CI=0;"
                        "DLNA.ORG_FLAGS=01700000000000000000000000000000"
                    ),
                    "size": str(entry["size"])
                }
            ).text = (
                f"http://{host}:{PORT}/media/{entry['id']}"
            )

            bookmark = int(entry.get("bookmark", 0))

            ET.SubElement(
                item,
                qname(SEC_NS, "dcmInfo")
            ).text = (
                f"CREATIONDATE=0,"
                f"FOLDER={entry['title']},"
                f"BM={bookmark}"
            )

            subtitles = entry.get("subtitles", [])

            # Samsung TVs read a single external subtitle: the first one.
            if subtitles:

                ET.SubElement(
                    item,
                    qname(SEC_NS, "CaptionInfoEx"),
                    {
                        qname(SEC_NS, "type"): "srt"
                    }
                ).text = (
                    f"http://{host}:{PORT}/subtitle/{subtitles[0][0]}"
                )

            # Standard clients (LG, VLC, Kodi...) get every subtitle.
            for subtitle_id, file_name in subtitles:

                ET.SubElement(
                    item,
                    qname(DIDL_NS, "res"),
                    {
                        "protocolInfo": SUBTITLE_PROTOCOL_INFO
                    }
                ).text = (
                    f"http://{host}:{PORT}"
                    f"/subtitle/{subtitle_id}/{file_name}"
                )

    return ET.tostring(
        root,
        encoding="unicode"
    )


def create_soap_response(action, arguments):

    envelope = ET.Element(
        qname(SOAP_NS, "Envelope"),
        {
            qname(
                SOAP_NS,
                "encodingStyle"
            ): "http://schemas.xmlsoap.org/soap/encoding/"
        }
    )

    body = ET.SubElement(
        envelope,
        qname(SOAP_NS, "Body")
    )

    response = ET.SubElement(
        body,
        qname(CD_NS, action + "Response")
    )

    for name, value in arguments.items():

        ET.SubElement(
            response,
            name
        ).text = str(value)

    return ET.tostring(
        envelope,
        encoding="utf-8",
        xml_declaration=True
    )


def create_soap_error(code, description):

    envelope = ET.Element(
        qname(SOAP_NS, "Envelope")
    )

    body = ET.SubElement(
        envelope,
        qname(SOAP_NS, "Body")
    )

    fault = ET.SubElement(
        body,
        "Fault"
    )

    ET.SubElement(
        fault,
        "faultcode"
    ).text = "s:Client"

    ET.SubElement(
        fault,
        "faultstring"
    ).text = "UPnPError"

    detail = ET.SubElement(
        fault,
        "detail"
    )

    error = ET.SubElement(
        detail,
        "{urn:schemas-upnp-org:control-1-0}UPnPError"
    )

    ET.SubElement(
        error,
        "errorCode"
    ).text = str(code)

    ET.SubElement(
        error,
        "errorDescription"
    ).text = description

    return ET.tostring(
        envelope,
        encoding="utf-8",
        xml_declaration=True
    )


def process_action(xml_data, host):

    root = ET.fromstring(xml_data)

    body = root.find(
        qname(SOAP_NS, "Body")
    )

    if body is None or len(body) == 0:
        raise ValueError("SOAP Body is empty")

    action = body[0]

    action_name = action.tag.split("}")[-1]

    params = {}

    for child in action:
        params[child.tag.split("}")[-1]] = child.text or ""

    if action_name == "GetSystemUpdateID":

        return create_soap_response(
            action_name,
            {"Id": 1}
        )

    if action_name == "GetSearchCapabilities":

        return create_soap_response(
            action_name,
            {"SearchCaps": ""}
        )

    if action_name == "GetSortCapabilities":

        return create_soap_response(
            action_name,
            {"SortCaps": ""}
        )

    if action_name == "X_SetBookmark":

        object_id = params.get("ObjectID", "").strip()
        position_value = params.get("PosSecond", "").strip()

        if not object_id or not position_value:
            raise ValueError(
                "X_SetBookmark requires ObjectID and PosSecond"
            )

        try:
            media_id = int(object_id)
            position_seconds = int(position_value)

        except ValueError:
            raise ValueError(
                "Invalid X_SetBookmark parameters"
            )

        if media_id <= 0 or position_seconds < 0:
            raise ValueError(
                "Invalid X_SetBookmark parameters"
            )

        conn = connect()

        try:

            media = conn.execute(
                """
                SELECT id
                FROM media
                WHERE id = ?
                """,
                (media_id,)
            ).fetchone()

            if media is None:
                raise ValueError(
                    "Bookmark media item not found"
                )

        finally:
            conn.close()

        if position_seconds < MINIMUM_BOOKMARK_SECONDS:
            position_seconds = 0

        save_playback_bookmark(
            media_id,
            position_seconds
        )

        print(
            f"[BOOKMARK] Saved position {position_seconds}s "
            f"for media {media_id}"
        )

        return create_soap_response(
            action_name,
            {}
        )

    if action_name != "Browse":

        print(
            f"[UPNP] Unsupported action requested: {action_name}"
        )

        raise ValueError("Unsupported action")

    object_id = params.get("ObjectID", "0")

    browse_flag = params.get(
        "BrowseFlag",
        "BrowseDirectChildren"
    )

    if browse_flag == "BrowseMetadata":

        result = get_metadata(object_id)

        didl = create_didl(
            result,
            host
        )

        number_returned = 1
        total_matches = 1

    elif browse_flag == "BrowseDirectChildren":

        try:

            start = int(
                params.get("StartingIndex", "0")
            )

            count = int(
                params.get("RequestedCount", "0")
            )

        except ValueError:
            raise ValueError(
                "Invalid pagination parameters"
            )

        if start < 0 or count < 0:
            raise ValueError(
                "Invalid pagination parameters"
            )

        result = get_library(
            object_id,
            start,
            count
        )

        didl = create_didl(
            result,
            host
        )

        number_returned = len(result["items"])
        total_matches = result["total"]

    else:
        raise ValueError("Invalid BrowseFlag")

    return create_soap_response(
        "Browse",
        {
            "Result": didl,
            "NumberReturned": number_returned,
            "TotalMatches": total_matches,
            "UpdateID": 1
        }
    )


async def handle_content_directory(request):

    try:

        xml_data = await request.read()

        if len(xml_data) > 1024 * 1024:

            raise web.HTTPRequestEntityTooLarge(
                max_size=1024 * 1024,
                actual_size=len(xml_data)
            )

        soap_action = request.headers.get(
            "SOAPACTION",
            ""
        )

        if "ContentDirectory:1" not in soap_action:
            raise ValueError("Invalid SOAPACTION header")

        # The address the client used to reach this server, so the media
        # URLs always work for that client.
        host = request.url.host or get_local_ip()

        response = await asyncio.to_thread(
            process_action,
            xml_data,
            host
        )

        return web.Response(
            body=response,
            content_type="text/xml",
            charset="utf-8"
        )

    except web.HTTPException:
        raise

    except Exception as error:

        print(f"[UPNP] Error: {error}")

        return web.Response(
            body=create_soap_error(
                402,
                "Invalid Args"
            ),
            status=500,
            content_type="text/xml"
        )


CONTENT_DIRECTORY_SCPD = """<?xml version="1.0" encoding="UTF-8"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
    <specVersion>
        <major>1</major>
        <minor>0</minor>
    </specVersion>
    <actionList>
        <action>
            <name>Browse</name>
            <argumentList>
                <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
                <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
                <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
                <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
                <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
                <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
                <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
                <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
                <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
                <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
            </argumentList>
        </action>
        <action>
            <name>GetSystemUpdateID</name>
            <argumentList>
                <argument><name>Id</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
            </argumentList>
        </action>
        <action>
            <name>GetSearchCapabilities</name>
            <argumentList>
                <argument><name>SearchCaps</name><direction>out</direction><relatedStateVariable>SearchCapabilities</relatedStateVariable></argument>
            </argumentList>
        </action>
        <action>
            <name>GetSortCapabilities</name>
            <argumentList>
                <argument><name>SortCaps</name><direction>out</direction><relatedStateVariable>SortCapabilities</relatedStateVariable></argument>
            </argumentList>
        </action>
        <action>
            <name>X_SetBookmark</name>
            <argumentList>
                <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
                <argument><name>PosSecond</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_PosSecond</relatedStateVariable></argument>
            </argumentList>
        </action>
    </actionList>
    <serviceStateTable>
        <stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>
        <stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType></stateVariable>
        <stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>
        <stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>
        <stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>
        <stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>
        <stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>
        <stateVariable sendEvents="no"><name>A_ARG_TYPE_PosSecond</name><dataType>ui4</dataType></stateVariable>
        <stateVariable sendEvents="yes"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>
        <stateVariable sendEvents="no"><name>SearchCapabilities</name><dataType>string</dataType></stateVariable>
        <stateVariable sendEvents="no"><name>SortCapabilities</name><dataType>string</dataType></stateVariable>
    </serviceStateTable>
</scpd>
"""


async def content_directory_description(request):

    return web.Response(
        text=CONTENT_DIRECTORY_SCPD,
        content_type="text/xml",
        charset="utf-8"
    )
