import base64
import json
import re
from abc import ABC, abstractmethod
from typing import Optional

from .domain import ParsedConfig


class ProtocolHandler(ABC):
    """
    Strategy interface for a single proxy protocol.

    To add support for a new protocol (e.g. Hysteria2):
      1. Subclass ProtocolHandler and implement all three methods.
      2. Call register(Hysteria2Handler()) — no other code changes needed.
    """

    protocol: str         # e.g. 'vless'
    pattern: re.Pattern   # regex that matches raw URIs of this protocol

    @abstractmethod
    def extract_host_port(self, link: str) -> tuple[Optional[str], Optional[int]]:
        """Parse (host, port) from the URI. Return (None, None) on failure."""
        ...

    @abstractmethod
    def extract_user_id(self, link: str) -> tuple[Optional[str], Optional[str]]:
        """
        Parse (user_id, method) from the URI.
        method is non-None only for Shadowsocks (cipher name).
        Return (None, None) on failure.
        """
        ...

    @abstractmethod
    def build_outbound(self, config: ParsedConfig, stream_settings: dict) -> dict:
        """Build the xray-core outbound JSON object for this protocol."""
        ...


class AddressBasedHandler(ProtocolHandler):
    """
    Shared parsing logic for protocols using the user@host:port URI format.
    Subclass this for any new address-based protocol (e.g. VLESS, Trojan).
    """

    def extract_host_port(self, link: str) -> tuple[Optional[str], Optional[int]]:
        try:
            part = link.split('://')[1].split('@')[-1]
            hostport = part.split('?')[0].split('#')[0]
            host, port_str = hostport.rsplit(':', 1)
            return host, int(port_str)
        except (ValueError, IndexError):
            return None, None

    def extract_user_id(self, link: str) -> tuple[Optional[str], Optional[str]]:
        try:
            return link.split('://')[1].split('@')[0], None
        except (ValueError, IndexError):
            return None, None


class VlessHandler(AddressBasedHandler):
    protocol = 'vless'
    pattern = re.compile(r'vless://[^\s]+')

    def build_outbound(self, config: ParsedConfig, stream_settings: dict) -> dict:
        return {
            'protocol': 'vless',
            'settings': {
                'vnext': [{
                    'address': config.host,
                    'port': config.port,
                    'users': [{'id': config.user_id, 'encryption': 'none'}],
                }],
            },
            'streamSettings': stream_settings,
        }


class VmessHandler(ProtocolHandler):
    protocol = 'vmess'
    pattern = re.compile(r'vmess://[^\s]+')

    def extract_host_port(self, link: str) -> tuple[Optional[str], Optional[int]]:
        try:
            b64 = link.split('vmess://')[1].split('#')[0]
            data: dict = json.loads(base64.b64decode(b64 + '=' * (-len(b64) % 4)))
            return data['add'], int(data['port'])
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return None, None

    def extract_user_id(self, link: str) -> tuple[Optional[str], Optional[str]]:
        try:
            b64 = link.split('vmess://')[1].split('#')[0]
            data: dict = json.loads(base64.b64decode(b64 + '=' * (-len(b64) % 4)))
            return data.get('id'), None
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return None, None

    def build_outbound(self, config: ParsedConfig, stream_settings: dict) -> dict:
        return {
            'protocol': 'vmess',
            'settings': {
                'vnext': [{
                    'address': config.host,
                    'port': config.port,
                    'users': [{'id': config.user_id, 'encryption': 'auto'}],
                }],
            },
            'streamSettings': stream_settings,
        }


class TrojanHandler(AddressBasedHandler):
    protocol = 'trojan'
    pattern = re.compile(r'trojan://[^\s]+')

    def build_outbound(self, config: ParsedConfig, stream_settings: dict) -> dict:
        return {
            'protocol': 'trojan',
            'settings': {
                'servers': [{
                    'address': config.host,
                    'port': config.port,
                    'password': config.user_id,
                }],
            },
            'streamSettings': stream_settings,
        }


class ShadowsocksHandler(ProtocolHandler):
    protocol = 'ss'
    pattern = re.compile(r'ss://[^\s]+')

    def extract_host_port(self, link: str) -> tuple[Optional[str], Optional[int]]:
        try:
            ss_part = link.split('ss://')[1].split('#')[0]
            if '@' in ss_part:
                hostport = ss_part.split('@')[-1]
            else:
                decoded = base64.b64decode(ss_part + '=' * (-len(ss_part) % 4)).decode()
                _, hostport = decoded.rsplit('@', 1)
            host, port_str = hostport.rsplit(':', 1)
            return host, int(port_str)
        except (ValueError, IndexError, UnicodeDecodeError):
            return None, None

    def extract_user_id(self, link: str) -> tuple[Optional[str], Optional[str]]:
        try:
            ss_part = link.split('ss://')[1].split('#')[0]
            if '@' in ss_part:
                userpass_b64 = ss_part.split('@')[0]
                decoded = base64.b64decode(userpass_b64 + '=' * (-len(userpass_b64) % 4)).decode()
                method, password = decoded.split(':', 1)
            else:
                decoded = base64.b64decode(ss_part + '=' * (-len(ss_part) % 4)).decode()
                method, rest = decoded.split(':', 1)
                password, _ = rest.rsplit('@', 1)
            return password, method
        except (ValueError, IndexError, UnicodeDecodeError):
            return None, None

    def build_outbound(self, config: ParsedConfig, stream_settings: dict) -> dict:
        return {
            'protocol': 'shadowsocks',
            'settings': {'servers': [{
                'address': config.host,
                'port': config.port,
                'password': config.user_id,
                'method': config.method,
            }]},
            'streamSettings': stream_settings,
        }


# ── Registry ──────────────────────────────────────────────────────────────────

_registry: dict[str, ProtocolHandler] = {}


def register(handler: ProtocolHandler) -> None:
    """Register a handler. Call once at startup to add protocol support."""
    _registry[handler.protocol] = handler


def get_handler(protocol: str) -> ProtocolHandler:
    """Return the handler for a protocol. Raises ValueError for unknown protocols."""
    try:
        return _registry[protocol]
    except KeyError:
        raise ValueError(f"No handler registered for protocol: {protocol!r}")


def get_patterns() -> dict[str, re.Pattern]:
    """Regex patterns for all registered protocols."""
    return {proto: h.pattern for proto, h in _registry.items()}


def get_protocols() -> tuple[str, ...]:
    """Names of all registered protocols."""
    return tuple(_registry.keys())


# Register built-in handlers
register(VlessHandler())
register(VmessHandler())
register(TrojanHandler())
register(ShadowsocksHandler())
