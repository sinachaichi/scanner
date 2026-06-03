import random
import re
from typing import Optional

from .domain import ParsedConfig
from .protocols import get_handler, get_patterns


class ProxyParser:
    """
    Parses raw proxy URIs into ParsedConfig objects.
    Contains no protocol-specific knowledge — all parsing is delegated
    to the protocol handler registry (Dependency Inversion).
    """

    def parse(self, raw_link: str, proto: str) -> Optional[ParsedConfig]:
        """Parse a raw proxy URI. Returns None if any required field cannot be extracted."""
        handler = get_handler(proto)
        remark = f'freedom-{random.randint(1000, 9999)}'
        link = self.apply_remark(raw_link, proto, remark)
        host, port = handler.extract_host_port(link)
        if host is None or port is None:
            return None
        user_id, method = handler.extract_user_id(link)
        return ParsedConfig(
            protocol=proto,
            raw_link=link,
            host=host,
            port=port,
            user_id=user_id,
            method=method,
            remark=remark,
        )

    def extract_from_text(self, text: str) -> list[tuple[str, str]]:
        """Scan arbitrary text and return all (raw_link, proto) matches."""
        results: list[tuple[str, str]] = []
        for proto, pattern in get_patterns().items():
            for match in pattern.findall(text):
                results.append((match.strip(), proto))
        return results

    @staticmethod
    def parse_query_params(link: str) -> dict[str, str]:
        """Extract URL query parameters from a proxy URI."""
        try:
            query = link.split('?', 1)[1].split('#')[0]
            return dict(pair.split('=', 1) for pair in query.split('&') if '=' in pair)
        except (IndexError, ValueError):
            return {}

    def apply_remark(self, link: str, proto: str, remark: str) -> str:
        """Replace or append a remark fragment to the URI."""
        if '#' in link:
            base, _ = link.split('#', 1)
            return f'{base}#{remark}'
        if proto in ('vless', 'vmess') and 'remark=' in link:
            return re.sub(r'(remark=)[^&]+', rf'\1{remark}', link)
        return f'{link}#{remark}'
