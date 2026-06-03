import asyncio
import datetime
import logging
from abc import ABC, abstractmethod

import requests

from .parser import ProxyParser
from .protocols import get_protocols

try:
    from telethon import TelegramClient
    HAS_TELETHON = True
except ImportError:
    TelegramClient = None  # type: ignore[assignment, misc]
    HAS_TELETHON = False

logger = logging.getLogger(__name__)


class ConfigSource(ABC):
    """
    Abstract base for anything that supplies raw proxy URIs.

    Implementing a new source (RSS feed, public API, etc.) requires only
    subclassing this and implementing fetch() — no other code changes needed.
    """

    @abstractmethod
    async def fetch(self) -> dict[str, set[str]]:
        """Return raw proxy link strings grouped by protocol name."""
        ...


class MirrorSource(ConfigSource):
    """Fetches proxy URIs from a list of plain-text HTTP mirror URLs in parallel."""

    def __init__(self, urls: list[str], parser: ProxyParser):
        self.urls = urls
        self.parser = parser

    async def fetch(self) -> dict[str, set[str]]:
        collected: dict[str, set[str]] = {p: set() for p in get_protocols()}

        async def fetch_one(url: str) -> None:
            try:
                resp = await asyncio.to_thread(requests.get, url, timeout=10)
                resp.raise_for_status()
                for link, proto in self.parser.extract_from_text(resp.text):
                    collected[proto].add(link)
                logger.info("Fetched mirror %s", url)
            except requests.RequestException as exc:
                logger.warning("Mirror fetch failed for %s: %s", url, exc)

        await asyncio.gather(*[fetch_one(url) for url in self.urls])
        return collected


class TelegramSource(ConfigSource):
    """
    Scrapes today's + yesterday's messages from Telegram channels via Telethon.
    Credentials are injected — never hardcoded.
    """

    def __init__(
        self,
        usernames: list[str],
        api_id: int,
        api_hash: str,
        parser: ProxyParser,
        session_path: str = 'session_name',
    ):
        self.usernames = usernames
        self.api_id = api_id
        self.api_hash = api_hash
        self.parser = parser
        self.session_path = session_path

    async def fetch(self) -> dict[str, set[str]]:
        return await self.fetch_async()

    async def fetch_async(self) -> dict[str, set[str]]:
        if not HAS_TELETHON:
            raise ImportError("telethon is required for Telegram fetching: pip install telethon")

        collected: dict[str, set[str]] = {p: set() for p in get_protocols()}
        if not self.usernames:
            return collected

        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        recent_dates = {today, yesterday}

        async with TelegramClient(self.session_path, self.api_id, self.api_hash) as client:
            logger.info("Connected to Telegram")
            for username in self.usernames:
                try:
                    entity = await client.get_entity(username)
                except Exception as exc:
                    logger.warning("Cannot access channel %s: %s", username, exc)
                    continue

                logger.info("Scraping channel: %s", username)
                async for message in client.iter_messages(entity, limit=500):
                    if message.date.date() not in recent_dates:
                        continue
                    if message.text:
                        for link, proto in self.parser.extract_from_text(message.text):
                            collected[proto].add(link)

        return collected
