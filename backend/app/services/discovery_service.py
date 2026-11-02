"""
T-Music LAN Discovery Service
Listens on UDP port (default 8211) for client broadcast pings and responds
with server metadata (project name, HTTP port, status) so mobile clients
on the same Wi-Fi network can discover the server automatically.

2026-11-02 audit (M-3) hardening:
* disabled by default — enable with DISCOVERY_ENABLED=true (music.env);
* answers only private / loopback / link-local sources, never the Internet
  (an exposed port could otherwise be used for reconnaissance/reflection);
* per-source rate limit and a short TTL cache for the LAN address lookup,
  so a packet flood does not trigger a blocking getaddrinfo per datagram;
* per-packet logging is DEBUG, not INFO (no log flooding).
"""
import asyncio
import ipaddress
import json
import logging
import socket
import time
from typing import Optional

logger = logging.getLogger("tmusic.discovery")

DISCOVERY_MAGIC_REQUEST = "TMUSIC_DISCOVER"
DEFAULT_DISCOVERY_PORT = 8211
MAX_DATAGRAM_BYTES = 512
LAN_IP_CACHE_TTL = 60.0
REPLIES_PER_SOURCE_PER_MINUTE = 10
_MAX_TRACKED_SOURCES = 1024

_lan_ip_cache: dict = {"value": None, "at": 0.0}


def get_lan_ip_addresses() -> list[str]:
    """Retrieve non-loopback local IPv4 addresses of the host machine."""
    ips: list[str] = []
    try:
        # Connect to a dummy external address (doesn't send traffic) to find primary routable IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            primary_ip = s.getsockname()[0]
            if primary_ip and not primary_ip.startswith("127."):
                ips.append(primary_ip)
    except Exception:
        logger.debug("Primary IP detection failed", exc_info=True)

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        logger.debug("LAN IP enumeration failed", exc_info=True)

    return ips


def cached_lan_ip_addresses() -> list[str]:
    now = time.monotonic()
    if _lan_ip_cache["value"] is None or now - _lan_ip_cache["at"] > LAN_IP_CACHE_TTL:
        _lan_ip_cache["value"] = get_lan_ip_addresses()
        _lan_ip_cache["at"] = now
    return list(_lan_ip_cache["value"])


def is_lan_source(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local:
        return True
    # is_private also covers documentation/reserved ranges; exclude the
    # unspecified/multicast oddities explicitly.
    return ip.is_private and not (ip.is_unspecified or ip.is_multicast)


class DiscoveryDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, http_port: int, project_name: str, discovery_port: int = DEFAULT_DISCOVERY_PORT):
        self.http_port = http_port
        self.project_name = project_name
        self.discovery_port = discovery_port
        self.transport: Optional[asyncio.DatagramTransport] = None
        self._replies: dict[str, list[float]] = {}

    def connection_made(self, transport: asyncio.DatagramTransport):
        self.transport = transport
        logger.info("UDP Discovery Service listening on port %d (LAN sources only)", self.discovery_port)

    def _allow(self, host: str) -> bool:
        now = time.monotonic()
        if len(self._replies) > _MAX_TRACKED_SOURCES:
            self._replies = {k: v for k, v in self._replies.items() if v and now - v[-1] < 60}
        recent = [t for t in self._replies.get(host, []) if now - t < 60]
        if len(recent) >= REPLIES_PER_SOURCE_PER_MINUTE:
            self._replies[host] = recent
            return False
        recent.append(now)
        self._replies[host] = recent
        return True

    def datagram_received(self, data: bytes, addr: tuple):
        host = addr[0] if addr else ""
        try:
            if len(data) > MAX_DATAGRAM_BYTES:
                return
            msg = data.decode("utf-8", errors="ignore").strip()
            if DISCOVERY_MAGIC_REQUEST not in msg:
                return
            if not is_lan_source(host):
                logger.debug("Ignored discovery ping from non-LAN source %s", host)
                return
            if not self._allow(host):
                logger.debug("Discovery ping rate-limited for %s", host)
                return
            lan_ips = cached_lan_ip_addresses()
            response = {
                "app": "tmusic",
                "project": self.project_name,
                "port": self.http_port,
                "status": "ok",
                "ips": lan_ips,
                "primary_url": f"http://{lan_ips[0]}:{self.http_port}" if lan_ips else None
            }
            payload = json.dumps(response).encode("utf-8")
            if self.transport:
                self.transport.sendto(payload, addr)
            logger.debug("Responded to discovery ping from %s:%d", host, addr[1])
        except Exception as exc:
            logger.debug("Error processing discovery packet from %s: %s", host, exc)


class DiscoveryService:
    def __init__(self):
        self._transport: Optional[asyncio.DatagramTransport] = None

    async def start(self, http_port: int, project_name: str, discovery_port: int = DEFAULT_DISCOVERY_PORT):
        loop = asyncio.get_running_loop()
        try:
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: DiscoveryDatagramProtocol(http_port, project_name, discovery_port),
                local_addr=("0.0.0.0", discovery_port),
                allow_broadcast=True
            )
        except Exception as exc:
            logger.warning("Could not bind UDP discovery service on port %d: %s", discovery_port, exc)

    async def stop(self):
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                logger.debug("UDP discovery transport close failed", exc_info=True)
            self._transport = None
            logger.info("UDP Discovery Service stopped.")


discovery_service = DiscoveryService()
