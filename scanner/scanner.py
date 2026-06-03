import asyncio
import logging

from .domain import ParsedConfig, ProbeResult
from .parser import ProxyParser
from .probe import TcpProbe, XraySpeedTester
from .protocols import get_protocols
from .sources import ConfigSource

logger = logging.getLogger(__name__)


class Scanner:
    """
    Orchestrates the full proxy scanning pipeline:
    collect → parse → TCP filter → speed test.

    All dependencies are injected — swap any component without touching
    this class (Open/Closed, Dependency Inversion).

    No ORM calls here. Callers are responsible for passing pre-fetched
    data in and persisting results out.
    """

    def __init__(
        self,
        sources: list[ConfigSource],
        parser: ProxyParser,
        tcp_probe: TcpProbe,
        speed_tester: XraySpeedTester,
        max_concurrent: int = 5,
    ):
        self._sources = sources
        self._parser = parser
        self._tcp_probe = tcp_probe
        self._speed_tester = speed_tester
        self._max_concurrent = max_concurrent

    async def run(self) -> dict[str, ProbeResult]:
        """Run the full pipeline. Returns working results keyed by dedup key."""
        raw_links = await self._collect()
        configs = self._parse(raw_links)
        passing = await self._filter_reachable(configs)
        return await self._run_speed_tests(passing)

    async def retest(self, nodes: list) -> tuple[list, list[int]]:
        """
        TCP probe a pre-fetched list of DB nodes in parallel.
        Returns (nodes_to_update, pks_to_delete).
        Accepts a plain list — no ORM calls inside.
        """
        from django.utils import timezone

        if not nodes:
            return [], []

        logger.info("Retesting %d existing nodes...", len(nodes))
        reachable = await asyncio.gather(
            *[self._tcp_probe.is_reachable(n.host, n.port) for n in nodes]
        )
        to_update, to_delete = [], []
        for node, ok in zip(nodes, reachable):
            if ok:
                node.last_checked = timezone.now()
                node.is_working = True
                to_update.append(node)
            else:
                to_delete.append(node.pk)
        return to_update, to_delete

    async def _collect(self) -> dict[str, set[str]]:
        """Fetch from all sources in parallel using the thread pool."""
        results = await asyncio.gather(
            *[s.fetch() for s in self._sources]
        )
        merged: dict[str, set[str]] = {p: set() for p in get_protocols()}
        for result in results:
            for proto, links in result.items():
                merged[proto].update(links)
        return merged

    def _parse(self, raw_links: dict[str, set[str]]) -> list[ParsedConfig]:
        """Parse all raw URIs and deduplicate by (protocol, host, port, user_id)."""
        configs: list[ParsedConfig] = []
        seen: set[str] = set()
        for proto, links in raw_links.items():
            for raw_link in links:
                config = self._parser.parse(raw_link, proto)
                if config is None:
                    continue
                key = f'{config.protocol}-{config.host}-{config.port}-{config.user_id}'
                if key not in seen:
                    seen.add(key)
                    configs.append(config)
        return configs

    async def _filter_reachable(self, configs: list[ParsedConfig]) -> list[ParsedConfig]:
        """TCP probe all configs in parallel. Returns only reachable ones."""
        if not configs:
            return []
        logger.info("TCP probing %d configs in parallel...", len(configs))
        reachable = await asyncio.gather(
            *[self._tcp_probe.is_reachable(c.host, c.port) for c in configs]
        )
        passing = [c for c, ok in zip(configs, reachable) if ok]
        logger.info("TCP probe: %d/%d passed", len(passing), len(configs))
        return passing

    async def _run_speed_tests(self, configs: list[ParsedConfig]) -> dict[str, ProbeResult]:
        """Run xray speed tests with bounded concurrency via Semaphore."""
        if not configs:
            return {}

        logger.info(
            "xray speed tests — %d configs, %d concurrent",
            len(configs), self._max_concurrent,
        )
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def test_one(config: ParsedConfig) -> ProbeResult:
            async with semaphore:
                return await self._speed_tester.test(config)

        results_list = await asyncio.gather(*[test_one(c) for c in configs])

        results: dict[str, ProbeResult] = {}
        for result in results_list:
            if result.is_working:
                c = result.config
                key = f'{c.protocol}-{c.host}-{c.port}-{c.user_id}'
                results[key] = result

        logger.info("xray tests done — %d working configs", len(results))
        return results
