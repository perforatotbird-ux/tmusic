"""
T-Music LAN Discovery Service
Listens on UDP port (default 8211) for client broadcast pings and responds
with server metadata (project name, HTTP port, status) so mobile clients
on the same Wi-Fi network can discover the server automatically.
"""
import asyncio
import json
import logging
import socket
from typing import Optional

logger = logging.getLogger("tmusic.discovery")

DISCOVERY_MAGIC_REQUEST = "TMUSIC_DISCOVER"
DEFAULT_DISCOVERY_PORT = 8211


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
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    return ips


class DiscoveryDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, http_port: int, project_name: str):
        self.http_port = http_port
        self.project_name = project_name
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.DatagramTransport):
        self.transport = transport
        logger.info("UDP Discovery Service listening on port %d", DEFAULT_DISCOVERY_PORT)

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
        try:
            msg = data.decode("utf-8", errors="ignore").strip()
            if DISCOVERY_MAGIC_REQUEST in msg:
                lan_ips = get_lan_ip_addresses()
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
                logger.info("Responded to discovery ping from %s:%d", addr[0], addr[1])
        except Exception as exc:
            logger.warning("Error processing discovery packet from %s: %s", addr, exc)


class DiscoveryService:
    def __init__(self):
        self._transport: Optional[asyncio.DatagramTransport] = None

    async def start(self, http_port: int, project_name: str, discovery_port: int = DEFAULT_DISCOVERY_PORT):
        loop = asyncio.get_running_loop()
        try:
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: DiscoveryDatagramProtocol(http_port, project_name),
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
                pass
            self._transport = None
            logger.info("UDP Discovery Service stopped.")


discovery_service = DiscoveryService()
