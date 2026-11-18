# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)


import asyncio
import errno
import socket
import sys
import threading
import time

from app_settings import get_http_port
from server.device import DEVICE_UUID

SSDP_ADDRESS = "239.255.255.250"
SSDP_PORT = 1900

DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaServer:1"

CONTENT_DIRECTORY = (
    "urn:schemas-upnp-org:service:ContentDirectory:1"
)

SERVER_NAME = "Windows/10 UPnP/1.1 Easy DLNA Server/0.3"
HTTP_PORT = get_http_port()

# Seconds between checks of the network interfaces. The server does not need
# the network to be ready when it starts: it keeps checking, and starts
# announcing itself as soon as an interface is available (or changes).
NETWORK_CHECK_INTERVAL = 10

# Seconds between the periodic NOTIFY announcements.
NOTIFY_INTERVAL = 900

# Seconds during which a detected list of addresses is reused.
IP_CACHE_SECONDS = 5

# "Address already in use" when joining a multicast group that is already joined.
ALREADY_MEMBER_ERRORS = {errno.EADDRINUSE, 10048}

_cache_lock = threading.Lock()
_cached_ips = ()
_cached_at = 0.0


# Virtual and tunnel interfaces that must not be announced (Linux).
IGNORED_INTERFACE_PREFIXES = (
    "lo", "docker", "veth", "br-", "virbr", "vboxnet", "vmnet",
    "tun", "tap", "wg", "zt", "tailscale"
)


def linux_interface_ips():
    """
    IPv4 addresses of the real network interfaces (Linux only), so a computer
    with several network cards announces itself on all of them.
    """

    if not sys.platform.startswith("linux"):
        return []

    try:
        import fcntl
        import struct

        names = [name for _, name in socket.if_nameindex()]

    except (ImportError, OSError):
        return []

    SIOCGIFADDR = 0x8915

    addresses = []

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:

        for name in names:

            if name.startswith(IGNORED_INTERFACE_PREFIXES):
                continue

            try:
                packed = fcntl.ioctl(
                    probe.fileno(),
                    SIOCGIFADDR,
                    struct.pack("256s", name[:15].encode())
                )
            except OSError:
                continue

            ip = socket.inet_ntoa(packed[20:24])

            if ip.startswith("127.") or ip.startswith("169.254."):
                continue

            addresses.append(ip)

    finally:
        probe.close()

    return addresses


def detect_local_ips():
    """
    Detect the usable local IPv4 addresses right now.
    """

    addresses = set(linux_interface_ips())

    try:
        hostname = socket.gethostname()

        results = socket.getaddrinfo(
            hostname,
            None,
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        for result in results:
            ip = result[4][0]

            if ip.startswith("127."):
                continue

            if ip.startswith("169.254."):
                continue

            addresses.add(ip)

    except OSError:
        pass

    # Detect the primary interface using the default network route.
    try:
        probe = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        try:
            probe.connect(("192.0.2.1", 80))
            primary_ip = probe.getsockname()[0]

            if (
                primary_ip
                and not primary_ip.startswith("127.")
                and not primary_ip.startswith("169.254.")
            ):
                addresses.add(primary_ip)

        finally:
            probe.close()

    except OSError:
        pass

    # Keep only addresses that can really be used to send traffic.
    usable_addresses = []

    for ip in sorted(addresses):

        try:
            test_socket = socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM
            )

            try:
                test_socket.bind((ip, 0))
                test_socket.connect(("192.0.2.1", 80))

                if test_socket.getsockname()[0] == ip:
                    usable_addresses.append(ip)

            finally:
                test_socket.close()

        except OSError:
            pass

    return tuple(usable_addresses)


def get_local_ips(max_age=IP_CACHE_SECONDS):
    """
    Return the usable local IPv4 addresses. The result is reused for
    max_age seconds; use max_age=0 to force a new detection.
    """

    global _cached_ips, _cached_at

    with _cache_lock:

        if (
            max_age > 0
            and _cached_at
            and time.monotonic() - _cached_at < max_age
        ):
            return _cached_ips

    addresses = detect_local_ips()

    with _cache_lock:
        _cached_ips = addresses
        _cached_at = time.monotonic()

    return addresses


def get_local_ip():
    """
    Return the primary usable local IPv4 address.
    """

    addresses = get_local_ips()

    if not addresses:
        raise OSError(
            "No usable local IPv4 address was detected."
        )

    return addresses[0]


def get_local_ip_or_none():
    """
    Return the primary local IPv4 address, or None if there is none yet.
    """

    try:
        return get_local_ip()
    except OSError:
        return None


def remove_local_ip(local_ips, ip, reason=None):
    """
    Remove a failed interface from the active list. If it is still detected
    on the next network check, it is added again.
    """

    if ip not in local_ips:
        return

    local_ips.remove(ip)

    if reason:
        print(
            f"[SSDP] Interface {ip} removed from active list: {reason}"
        )
    else:
        print(
            f"[SSDP] Interface {ip} removed from active list."
        )


class SSDPProtocol(asyncio.DatagramProtocol):

    def __init__(self, local_ips):
        # Shared list: the network monitor adds and removes interfaces.
        self.local_ips = local_ips
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        print("[SSDP] Listening for UPnP requests")

    def datagram_received(self, data, addr):

        message = data.decode(
            "utf-8",
            errors="ignore"
        )

        if not message.upper().startswith("M-SEARCH"):
            return

        headers = {}

        for line in message.split("\r\n")[1:]:

            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().upper()] = value.strip()

        target = headers.get("ST", "").lower()

        responses = {
            "upnp:rootdevice": (
                "upnp:rootdevice",
                f"{DEVICE_UUID}::upnp:rootdevice"
            ),
            DEVICE_UUID.lower(): (
                DEVICE_UUID,
                DEVICE_UUID
            ),
            DEVICE_TYPE.lower(): (
                DEVICE_TYPE,
                f"{DEVICE_UUID}::{DEVICE_TYPE}"
            ),
            CONTENT_DIRECTORY.lower(): (
                CONTENT_DIRECTORY,
                f"{DEVICE_UUID}::{CONTENT_DIRECTORY}"
            )
        }

        if target == "ssdp:all":
            selected = list(responses.values())

        elif target in responses:
            selected = [responses[target]]

        else:
            selected = []

        # Respond only through active network interfaces.
        for st, usn in selected:

            for interface_ip in list(self.local_ips):

                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "CACHE-CONTROL: max-age=1800\r\n"
                    "EXT:\r\n"
                    f"LOCATION: http://{interface_ip}:{HTTP_PORT}/device.xml\r\n"
                    f"SERVER: {SERVER_NAME}\r\n"
                    f"ST: {st}\r\n"
                    f"USN: {usn}\r\n"
                    "\r\n"
                )

                try:
                    response_socket = socket.socket(
                        socket.AF_INET,
                        socket.SOCK_DGRAM
                    )

                    try:
                        response_socket.bind(
                            (interface_ip, 0)
                        )

                        response_socket.sendto(
                            response.encode("utf-8"),
                            addr
                        )

                    finally:
                        response_socket.close()

                except OSError as error:
                    remove_local_ip(
                        self.local_ips,
                        interface_ip,
                        str(error)
                    )


def send_notify_sync(sock, local_ips):

    targets = [
        (
            "upnp:rootdevice",
            f"{DEVICE_UUID}::upnp:rootdevice"
        ),
        (DEVICE_UUID, DEVICE_UUID),
        (
            DEVICE_TYPE,
            f"{DEVICE_UUID}::{DEVICE_TYPE}"
        ),
        (
            CONTENT_DIRECTORY,
            f"{DEVICE_UUID}::{CONTENT_DIRECTORY}"
        )
    ]

    for local_ip in list(local_ips):

        try:

            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_IF,
                socket.inet_aton(local_ip)
            )

            for nt, usn in targets:

                message = (
                    "NOTIFY * HTTP/1.1\r\n"
                    f"HOST: {SSDP_ADDRESS}:{SSDP_PORT}\r\n"
                    "CACHE-CONTROL: max-age=1800\r\n"
                    f"LOCATION: http://{local_ip}:{HTTP_PORT}/device.xml\r\n"
                    f"NT: {nt}\r\n"
                    "NTS: ssdp:alive\r\n"
                    f"SERVER: {SERVER_NAME}\r\n"
                    f"USN: {usn}\r\n"
                    "\r\n"
                )

                sock.sendto(
                    message.encode("utf-8"),
                    (SSDP_ADDRESS, SSDP_PORT)
                )

            print(
                f"[SSDP] NOTIFY sent through {local_ip}"
            )

        except OSError as error:
            remove_local_ip(
                local_ips,
                local_ip,
                str(error)
            )


def multicast_membership(local_ip):

    return (
        socket.inet_aton(SSDP_ADDRESS)
        + socket.inet_aton(local_ip)
    )


def sync_interfaces(listen_socket, active_ips, detected, failed):
    """
    Make the multicast memberships and the active list match the interfaces
    detected now. Returns the lists of added and removed addresses.
    """

    detected_set = set(detected)
    removed = []

    for ip in list(active_ips):

        if ip in detected_set:
            continue

        try:
            listen_socket.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_DROP_MEMBERSHIP,
                multicast_membership(ip)
            )
        except OSError:
            pass

        active_ips.remove(ip)
        removed.append(ip)

        print(f"[SSDP] Interface {ip} is no longer available.")

    # Forget the failures of interfaces that have disappeared.
    failed.intersection_update(detected_set)

    added = []

    for ip in detected:

        if ip in active_ips:
            continue

        try:
            listen_socket.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                multicast_membership(ip)
            )

        except OSError as error:

            if error.errno not in ALREADY_MEMBER_ERRORS:

                if ip not in failed:
                    failed.add(ip)
                    print(
                        f"[SSDP] Multicast unavailable on "
                        f"{ip}: {error}"
                    )

                continue

        failed.discard(ip)
        active_ips.append(ip)
        added.append(ip)

        print(f"[SSDP] Multicast enabled on {ip}")

    return added, removed


async def monitor_network(
    listen_socket,
    notify_socket,
    protocol,
    interval=NETWORK_CHECK_INTERVAL,
    notify_interval=NOTIFY_INTERVAL
):
    """
    Keeps SSDP working when the network is not ready at start-up or changes
    while the server runs: joins the multicast group on new interfaces,
    leaves it on vanished ones, and announces the server whenever an
    interface appears and every notify_interval seconds.
    """

    failed = set()
    last_notify = None
    waiting_logged = False

    while True:

        try:

            detected = await asyncio.to_thread(
                get_local_ips,
                0
            )

            added, removed = sync_interfaces(
                listen_socket,
                protocol.local_ips,
                detected,
                failed
            )

            if protocol.local_ips:

                waiting_logged = False

                due = (
                    last_notify is None
                    or time.monotonic() - last_notify >= notify_interval
                )

                if added or due:

                    await asyncio.to_thread(
                        send_notify_sync,
                        notify_socket,
                        protocol.local_ips
                    )

                    last_notify = time.monotonic()

            elif not waiting_logged:

                print(
                    "[SSDP] Waiting for a usable network interface..."
                )

                waiting_logged = True

        except asyncio.CancelledError:
            raise

        except Exception as error:
            print(f"[SSDP] Network monitor error: {error}")

        await asyncio.sleep(interval)


async def start_ssdp():

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM,
        socket.IPPROTO_UDP
    )

    sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    # macOS needs SO_REUSEPORT to share the SSDP port with other programs.
    if sys.platform == "darwin" and hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEPORT,
            1
        )

    try:

        sock.bind(("", SSDP_PORT))
        sock.setblocking(False)

        loop = asyncio.get_running_loop()

        transport, protocol = await loop.create_datagram_endpoint(
            lambda: SSDPProtocol([]),
            sock=sock
        )

    except Exception:
        sock.close()
        raise

    notify_socket = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM,
        socket.IPPROTO_UDP
    )

    notify_socket.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_TTL,
        2
    )

    notify_socket.setblocking(True)

    task = asyncio.create_task(
        monitor_network(
            sock,
            notify_socket,
            protocol
        )
    )

    print("[SSDP] Service started")

    return transport, notify_socket, task
