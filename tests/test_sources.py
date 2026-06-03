import asyncio
from unittest.mock import MagicMock, patch

import pytest
import requests

from scanner.parser import ProxyParser
from scanner.sources import MirrorSource, TelegramSource


@pytest.fixture
def parser() -> ProxyParser:
    return ProxyParser()


# ── MirrorSource ──────────────────────────────────────────────────────────────

class TestMirrorSource:
    def test_collects_links_from_mirror_text(self, parser):
        resp = MagicMock(text='vless://uuid@1.2.3.4:443?type=tcp\nvmess://abc123')
        resp.raise_for_status = MagicMock()

        with patch('scanner.sources.requests.get', return_value=resp) as mock_get:
            result = asyncio.run(MirrorSource(['http://mirror'], parser).fetch())

        mock_get.assert_called_once_with('http://mirror', timeout=10)
        assert any('vless://' in link for link in result['vless'])
        assert any('vmess://' in link for link in result['vmess'])

    def test_request_exception_is_swallowed(self, parser):
        with patch('scanner.sources.requests.get', side_effect=requests.RequestException('boom')):
            result = asyncio.run(MirrorSource(['http://mirror'], parser).fetch())
        assert all(len(links) == 0 for links in result.values())

    def test_http_error_is_swallowed(self, parser):
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError('404')
        with patch('scanner.sources.requests.get', return_value=resp):
            result = asyncio.run(MirrorSource(['http://mirror'], parser).fetch())
        assert all(len(links) == 0 for links in result.values())

    def test_all_urls_are_fetched(self, parser):
        resp = MagicMock(text='', raise_for_status=MagicMock())
        with patch('scanner.sources.requests.get', return_value=resp) as mock_get:
            asyncio.run(MirrorSource(['http://a', 'http://b'], parser).fetch())
        assert mock_get.call_count == 2


# ── TelegramSource guard branches ─────────────────────────────────────────────

class TestTelegramSourceGuards:
    def test_empty_usernames_short_circuits(self, parser):
        with patch('scanner.sources.HAS_TELETHON', True):
            result = asyncio.run(TelegramSource([], 123, 'hash', parser).fetch())
        assert all(len(links) == 0 for links in result.values())

    def test_raises_import_error_without_telethon(self, parser):
        with patch('scanner.sources.HAS_TELETHON', False):
            with pytest.raises(ImportError, match='telethon'):
                asyncio.run(TelegramSource(['@chan'], 123, 'hash', parser).fetch())

    def test_fetch_async_is_equivalent_to_fetch(self, parser):
        with patch('scanner.sources.HAS_TELETHON', True):
            result = asyncio.run(TelegramSource([], 1, 'h', parser).fetch_async())
        assert isinstance(result, dict)
