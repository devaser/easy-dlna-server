# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)


import xml.etree.ElementTree as ET
from urllib.parse import quote

DIDL_NS = "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
DC_NS = "http://purl.org/dc/elements/1.1/"
UPNP_NS = "urn:schemas-upnp-org:metadata-1-0/upnp/"

# Vendor DLNA extension used by some TVs to find external subtitles.
SEC_NS = "http://www.sec.co.kr/dlna"

ET.register_namespace("", DIDL_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("upnp", UPNP_NS)
ET.register_namespace("sec", SEC_NS)


def qname(namespace, name):
    return f"{{{namespace}}}{name}"


def create_video_item(media, host, port, subtitles=None):
    """
    Builds a DIDL-Lite item for a movie.

    media:
        Tuple read from SQLite:
        (id, title, path, size, duration, mime_type)

    subtitles:
        List of (subtitle_id, subtitle_path)
    """

    if subtitles is None:
        subtitles = []

    media_id = media[0]
    title = media[1]
    file_size = media[3]
    mime_type = media[5]

    root = ET.Element(
        qname(DIDL_NS, "DIDL-Lite")
    )

    item = ET.SubElement(
        root,
        qname(DIDL_NS, "item"),
        {
            "id": str(media_id),
            "parentID": "0",
            "restricted": "1"
        }
    )

    title_element = ET.SubElement(
        item,
        qname(DC_NS, "title")
    )
    title_element.text = title

    class_element = ET.SubElement(
        item,
        qname(UPNP_NS, "class")
    )
    class_element.text = "object.item.videoItem"

    media_url = (
        f"http://{host}:{port}/media/{media_id}"
    )

    resource = ET.SubElement(
        item,
        qname(DIDL_NS, "res"),
        {
            "protocolInfo": (
                f"http-get:*:{mime_type}:*"
            ),
            "size": str(file_size)
        }
    )

    resource.text = media_url

    # External SRT subtitles
    for subtitle_id, subtitle_path in subtitles:

        subtitle_url = (
            f"http://{host}:{port}/subtitle/{subtitle_id}"
        )

        caption = ET.SubElement(
            item,
            qname(SEC_NS, "CaptionInfoEx"),
            {
                qname(SEC_NS, "type"): "srt"
            }
        )

        caption.text = subtitle_url

    return ET.tostring(
        root,
        encoding="unicode",
        xml_declaration=False
    )
