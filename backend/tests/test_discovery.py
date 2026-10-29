import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.discovery_service import (
    DiscoveryDatagramProtocol,
    DISCOVERY_MAGIC_REQUEST,
    get_lan_ip_addresses,
)

class MockTransport:
    def __init__(self):
        self.sent_messages = []
        self.closed = False

    def sendto(self, data: bytes, addr):
        self.sent_messages.append((data, addr))

    def close(self):
        self.closed = True

def test_get_lan_ip_addresses():
    ips = get_lan_ip_addresses()
    assert isinstance(ips, list)
    for ip in ips:
        assert not ip.startswith("127.")

def test_discovery_protocol_response():
    protocol = DiscoveryDatagramProtocol(http_port=8005, project_name="T-Music")
    mock_transport = MockTransport()
    protocol.connection_made(mock_transport)

    # Ping with magic string
    client_addr = ("192.168.1.50", 54321)
    protocol.datagram_received(DISCOVERY_MAGIC_REQUEST.encode("utf-8"), client_addr)

    assert len(mock_transport.sent_messages) == 1
    data, addr = mock_transport.sent_messages[0]
    assert addr == client_addr

    payload = json.loads(data.decode("utf-8"))
    assert payload["app"] == "tmusic"
    assert payload["project"] == "T-Music"
    assert payload["port"] == 8005
    assert payload["status"] == "ok"
    assert "ips" in payload

def test_discovery_protocol_ignores_irrelevant_traffic():
    protocol = DiscoveryDatagramProtocol(http_port=8005, project_name="T-Music")
    mock_transport = MockTransport()
    protocol.connection_made(mock_transport)

    # Random noise packet
    protocol.datagram_received(b"RANDOM_NOISE", ("192.168.1.50", 54321))
    assert len(mock_transport.sent_messages) == 0
