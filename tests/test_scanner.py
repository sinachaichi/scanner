import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from scanner.domain import ParsedConfig, ProbeResult
from scanner.parser import ProxyParser
from scanner.probe import TcpProbe, XraySpeedTester
from scanner.scanner import Scanner
from scanner.sources import ConfigSource


@pytest.fixture
def vless_link() -> str:
    return 'vless://test-uuid@1.2.3.4:443?type=tcp'


@pytest.fixture
def vless_config() -> ParsedConfig:
    return ParsedConfig(
        protocol='vless', raw_link='vless://test-uuid@1.2.3.4:443?type=tcp#freedom-1',
        host='1.2.3.4', port=443, user_id='test-uuid', method=None, remark='freedom-1',
    )


@pytest.fixture
def mock_source(vless_link) -> MagicMock:
    source = MagicMock(spec=ConfigSource)
    source.fetch = AsyncMock(return_value={
        'vless': {vless_link}, 'vmess': set(), 'trojan': set(), 'ss': set()
    })
    return source


@pytest.fixture
def passing_tcp_probe() -> MagicMock:
    probe = MagicMock(spec=TcpProbe)
    probe.is_reachable = AsyncMock(return_value=True)
    return probe


@pytest.fixture
def failing_tcp_probe() -> MagicMock:
    probe = MagicMock(spec=TcpProbe)
    probe.is_reachable = AsyncMock(return_value=False)
    return probe


@pytest.fixture
def working_speed_tester(vless_config) -> MagicMock:
    tester = MagicMock(spec=XraySpeedTester)
    tester.test = AsyncMock(return_value=ProbeResult(
        config=vless_config, is_working=True, speed_kbps=500.0,
    ))
    return tester


@pytest.fixture
def parser() -> ProxyParser:
    return ProxyParser()


def _make_scanner(source, tcp_probe, speed_tester) -> Scanner:
    return Scanner(
        sources=[source],
        parser=ProxyParser(),
        tcp_probe=tcp_probe,
        speed_tester=speed_tester,
        max_concurrent=1,
    )


class TestScannerRun:
    def test_returns_working_config_end_to_end(
        self, mock_source, passing_tcp_probe, working_speed_tester,
    ):
        scanner = _make_scanner(mock_source, passing_tcp_probe, working_speed_tester)
        results = asyncio.run(scanner.run())
        assert len(results) == 1
        assert next(iter(results.values())).speed_kbps == 500.0

    def test_empty_sources_returns_empty_dict(self, passing_tcp_probe, working_speed_tester):
        scanner = Scanner(
            sources=[], parser=ProxyParser(),
            tcp_probe=passing_tcp_probe, speed_tester=working_speed_tester,
        )
        assert asyncio.run(scanner.run()) == {}

    def test_unreachable_configs_skip_speed_test(
        self, mock_source, failing_tcp_probe, working_speed_tester,
    ):
        scanner = _make_scanner(mock_source, failing_tcp_probe, working_speed_tester)
        results = asyncio.run(scanner.run())
        assert results == {}
        working_speed_tester.test.assert_not_called()

    def test_failed_speed_test_excluded_from_results(
        self, mock_source, passing_tcp_probe, vless_config,
    ):
        tester = MagicMock(spec=XraySpeedTester)
        tester.test = AsyncMock(return_value=ProbeResult(
            config=vless_config, is_working=False, speed_kbps=0.0,
        ))
        scanner = _make_scanner(mock_source, passing_tcp_probe, tester)
        assert asyncio.run(scanner.run()) == {}

    def test_multiple_sources_are_merged(self, passing_tcp_probe, working_speed_tester):
        source_a = MagicMock(spec=ConfigSource)
        source_a.fetch = AsyncMock(return_value={
            'vless': {'vless://uuid-a@1.1.1.1:443'}, 'vmess': set(), 'trojan': set(), 'ss': set(),
        })
        source_b = MagicMock(spec=ConfigSource)
        source_b.fetch = AsyncMock(return_value={
            'vless': {'vless://uuid-b@2.2.2.2:443'}, 'vmess': set(), 'trojan': set(), 'ss': set(),
        })

        scanner = Scanner(
            sources=[source_a, source_b],
            parser=ProxyParser(),
            tcp_probe=passing_tcp_probe,
            speed_tester=working_speed_tester,
            max_concurrent=2,
        )
        asyncio.run(scanner.run())
        source_a.fetch.assert_called_once()
        source_b.fetch.assert_called_once()


class TestScannerRetest:
    def test_reachable_nodes_are_marked_working(
        self, passing_tcp_probe, working_speed_tester, vless_config,
    ):
        scanner = Scanner(
            sources=[], parser=ProxyParser(),
            tcp_probe=passing_tcp_probe, speed_tester=working_speed_tester,
        )
        node = MagicMock()
        node.host, node.port = '1.2.3.4', 443

        to_update, to_delete = asyncio.run(scanner.retest([node]))

        assert len(to_update) == 1 and to_delete == []
        assert node.is_working is True

    def test_unreachable_nodes_are_marked_for_deletion(
        self, failing_tcp_probe, working_speed_tester,
    ):
        scanner = Scanner(
            sources=[], parser=ProxyParser(),
            tcp_probe=failing_tcp_probe, speed_tester=working_speed_tester,
        )
        node = MagicMock()
        node.host, node.port, node.pk = '1.2.3.4', 443, 42

        to_update, to_delete = asyncio.run(scanner.retest([node]))

        assert to_update == [] and to_delete == [42]

    def test_empty_node_list_returns_empty_tuples(self, passing_tcp_probe, working_speed_tester):
        scanner = Scanner(
            sources=[], parser=ProxyParser(),
            tcp_probe=passing_tcp_probe, speed_tester=working_speed_tester,
        )
        assert asyncio.run(scanner.retest([])) == ([], [])
