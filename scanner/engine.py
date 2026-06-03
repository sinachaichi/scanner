import asyncio
import logging
from typing import Optional

from django.conf import settings

from .models import Channel, Mirror
from .parser import ProxyParser
from .probe import TcpProbe, XraySpeedTester
from .ranker import filter_and_rank  # noqa: F401 — re-exported for external callers
from .repository import NodeRepository
from .scanner import Scanner
from .sources import HAS_TELETHON, ConfigSource, MirrorSource, TelegramSource

logger = logging.getLogger(__name__)


class ScanOrchestrator:
    """
    Composition root — wires all components together and runs the full pipeline.

    Keeps all infrastructure concerns (Django settings, DB queries for source
    configuration) in one place, so Scanner stays pure and testable.
    """

    def __init__(
        self,
        channel_ids: Optional[list[int]] = None,
        mirror_ids: Optional[list[int]] = None,
    ):
        self.channel_ids = channel_ids
        self.mirror_ids = mirror_ids

    def run(self) -> None:
        logger.info(
            "Scan started (channel_ids=%s, mirror_ids=%s)",
            self.channel_ids, self.mirror_ids,
        )

        parser = ProxyParser()
        scanner = Scanner(
            sources=self.build_sources(parser),
            parser=parser,
            tcp_probe=TcpProbe(timeout=2.0, threshold_ms=1050),
            speed_tester=XraySpeedTester(xray_path=settings.XRAY_PATH, parser=parser),
            max_concurrent=getattr(settings, 'MAX_CONCURRENT_XRAY', 5),
        )
        repo = NodeRepository()

        new_results = asyncio.run(scanner.run())

        existing_nodes = repo.get_all()
        retested_nodes, pks_to_delete = asyncio.run(scanner.retest(existing_nodes))

        repo.delete_by_pks(pks_to_delete)
        retested_keys = {f'{n.protocol}-{n.host}-{n.port}-{n.user_id}' for n in retested_nodes}
        repo.persist(new_results, retested_nodes, retested_keys)
        repo.remove_duplicates()

        logger.info("Scan finished")

    def build_sources(self, parser: ProxyParser) -> list[ConfigSource]:
        """Instantiate active ConfigSource objects from DB configuration."""
        sources: list[ConfigSource] = []
        both_unset = self.channel_ids is None and self.mirror_ids is None
        do_channels = self.channel_ids is not None or both_unset
        do_mirrors = self.mirror_ids is not None or both_unset

        if do_mirrors:
            qs = Mirror.objects.filter(active=True)
            if self.mirror_ids is not None:
                qs = qs.filter(id__in=self.mirror_ids)
            urls = list(qs.values_list('url', flat=True))
            if urls:
                sources.append(MirrorSource(urls, parser=parser))

        if do_channels:
            qs = Channel.objects.filter(active=True)
            if self.channel_ids is not None:
                qs = qs.filter(id__in=self.channel_ids)
            usernames = list(qs.values_list('username', flat=True))
            api_id = getattr(settings, 'TELEGRAM_API_ID', None)
            api_hash = getattr(settings, 'TELEGRAM_API_HASH', None)
            if usernames and api_id and api_hash and HAS_TELETHON:
                sources.append(TelegramSource(usernames, int(api_id), api_hash, parser=parser))
            elif usernames:
                logger.info("Telegram credentials not configured — skipping channel fetch")

        return sources


def run_full_scan(
    channel_ids: Optional[list[int]] = None,
    mirror_ids: Optional[list[int]] = None,
) -> None:
    """Blocking entry point for the Celery task, management command, and admin."""
    ScanOrchestrator(channel_ids, mirror_ids).run()
