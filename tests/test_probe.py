import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scanner.domain import ParsedConfig, ProbeResult
from scanner.parser import ProxyParser
from scanner.probe import TcpProbe, XraySpeedTester


@pytest.fixture
def tcp_probe() -> TcpProbe:
    return TcpProbe(timeout=2.0, threshold_ms=1050)


@pytest.fixture
def parser() -> ProxyParser:
    return ProxyParser()


@pytest.fixture
def speed_tester(parser) -> XraySpeedTester:
    return XraySpeedTester(xray_path='./xray', parser=parser, timeout=12)


@pytest.fixture
def vless_config() -> ParsedConfig:
    return ParsedConfig(
        protocol='vless',
        raw_link='vless://test-uuid@1.2.3.4:443?type=tcp#freedom-1234',
        host='1.2.3.4',
        port=443,
        user_id='test-uuid',
        method=None,
        remark='freedom-1234',
    )


@pytest.fixture
def ss_config() -> ParsedConfig:
    return ParsedConfig(
        protocol='ss',
        raw_link='ss://encoded@5.6.7.8:8388#freedom-5678',
        host='5.6.7.8',
        port=8388,
        user_id='mypassword',
        method='aes-256-gcm',
        remark='freedom-5678',
    )


# ── TcpProbe.ping ─────────────────────────────────────────────────────────────

class TestTcpProbePing:
    def test_returns_positive_ms_on_success(self, tcp_probe):
        mock_writer = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        async def fake_open(host, port):
            return MagicMock(), mock_writer

        with patch('scanner.probe.asyncio.open_connection', side_effect=fake_open):
            result = asyncio.run(tcp_probe.ping('1.2.3.4', 443))
        assert result >= 0

    def test_returns_minus_one_on_os_error(self, tcp_probe):
        async def failing_open(host, port):
            raise OSError('refused')

        with patch('scanner.probe.asyncio.open_connection', side_effect=failing_open):
            result = asyncio.run(tcp_probe.ping('1.2.3.4', 443))
        assert result == -1

    def test_returns_minus_one_on_timeout(self, tcp_probe):
        async def slow_open(host, port):
            await asyncio.sleep(10)

        with patch('scanner.probe.asyncio.open_connection', side_effect=slow_open):
            result = asyncio.run(tcp_probe.ping('1.2.3.4', 443))
        assert result == -1


# ── TcpProbe.is_reachable ─────────────────────────────────────────────────────

class TestTcpProbeIsReachable:
    def test_returns_true_when_under_threshold(self, tcp_probe):
        with patch.object(tcp_probe, 'ping', new=AsyncMock(return_value=100)):
            assert asyncio.run(tcp_probe.is_reachable('host', 443)) is True

    def test_returns_false_when_over_threshold(self, tcp_probe):
        with patch.object(tcp_probe, 'ping', new=AsyncMock(return_value=2000)):
            assert asyncio.run(tcp_probe.is_reachable('host', 443)) is False

    def test_returns_false_on_failure(self, tcp_probe):
        with patch.object(tcp_probe, 'ping', new=AsyncMock(return_value=-1)):
            assert asyncio.run(tcp_probe.is_reachable('host', 443)) is False


# ── XraySpeedTester.build_xray_config ────────────────────────────────────────

class TestBuildConfig:
    def test_vless_outbound_structure(self, speed_tester, vless_config):
        cfg = speed_tester.build_xray_config(vless_config, socks_port=10000)
        assert cfg['outbounds'][0]['protocol'] == 'vless'
        vnext = cfg['outbounds'][0]['settings']['vnext'][0]
        assert vnext['address'] == '1.2.3.4' and vnext['port'] == 443
        assert vnext['users'][0]['id'] == 'test-uuid'

    def test_ss_outbound_includes_method(self, speed_tester, ss_config):
        cfg = speed_tester.build_xray_config(ss_config, socks_port=10001)
        server = cfg['outbounds'][0]['settings']['servers'][0]
        assert server['method'] == 'aes-256-gcm' and server['password'] == 'mypassword'

    def test_socks_inbound_binds_correct_port(self, speed_tester, vless_config):
        cfg = speed_tester.build_xray_config(vless_config, socks_port=12345)
        assert cfg['inbounds'][0]['port'] == 12345
        assert cfg['inbounds'][0]['listen'] == '127.0.0.1'


# ── XraySpeedTester.allocate_port ─────────────────────────────────────────────

class TestAllocatePort:
    def test_returns_valid_port_number(self):
        port = XraySpeedTester.allocate_port()
        assert 1024 <= port <= 65535

    def test_returns_different_ports_on_successive_calls(self):
        ports = {XraySpeedTester.allocate_port() for _ in range(10)}
        assert len(ports) > 1


# ── XraySpeedTester.test ──────────────────────────────────────────────────────

class TestSpeedTesterTest:
    def _xray_proc(self) -> MagicMock:
        proc = MagicMock()
        proc.pid = 99999
        proc.wait = AsyncMock()
        return proc

    def _curl_proc(self, returncode: int = 0, stderr: bytes = b'') -> MagicMock:
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b'', stderr))
        proc.returncode = returncode
        return proc

    def test_returns_working_result_on_successful_download(self, speed_tester, vless_config):
        with (
            patch('scanner.probe.asyncio.create_subprocess_exec',
                  new=AsyncMock(side_effect=[self._xray_proc(), self._curl_proc()])),
            patch.object(XraySpeedTester, '_wait_for_port', new=AsyncMock(return_value=True)),
            patch('scanner.probe.os.killpg'),
            patch('scanner.probe.os.getpgid', return_value=99999),
        ):
            result = asyncio.run(speed_tester.test(vless_config))

        assert isinstance(result, ProbeResult)
        assert result.is_working is True
        # Speed is _TEST_BYTES / 1024 / duration; with mocked subprocesses the
        # elapsed time is negligible but always > 0, so kbps > 0.
        assert result.speed_kbps > 0

    def test_returns_not_working_when_xray_port_never_opens(self, speed_tester, vless_config):
        with (
            patch('scanner.probe.asyncio.create_subprocess_exec',
                  new=AsyncMock(return_value=self._xray_proc())),
            patch.object(XraySpeedTester, '_wait_for_port', new=AsyncMock(return_value=False)),
            patch('scanner.probe.os.killpg'),
            patch('scanner.probe.os.getpgid', return_value=99999),
        ):
            result = asyncio.run(speed_tester.test(vless_config))
        assert result.is_working is False and result.speed_kbps == 0.0

    def test_returns_not_working_when_curl_fails(self, speed_tester, vless_config):
        with (
            patch('scanner.probe.asyncio.create_subprocess_exec',
                  new=AsyncMock(side_effect=[
                      self._xray_proc(),
                      self._curl_proc(returncode=7, stderr=b'refused'),
                  ])),
            patch.object(XraySpeedTester, '_wait_for_port', new=AsyncMock(return_value=True)),
            patch('scanner.probe.os.killpg'),
            patch('scanner.probe.os.getpgid', return_value=99999),
        ):
            result = asyncio.run(speed_tester.test(vless_config))
        assert result.is_working is False

    def test_returns_not_working_on_os_error(self, speed_tester, vless_config):
        with patch('scanner.probe.asyncio.create_subprocess_exec',
                   new=AsyncMock(side_effect=OSError('xray not found'))):
            result = asyncio.run(speed_tester.test(vless_config))
        assert result.is_working is False

    def test_kills_xray_process_in_finally_block(self, speed_tester, vless_config):
        with (
            patch('scanner.probe.asyncio.create_subprocess_exec',
                  new=AsyncMock(return_value=self._xray_proc())),
            patch.object(XraySpeedTester, '_wait_for_port', new=AsyncMock(return_value=False)),
            patch('scanner.probe.os.killpg') as mock_kill,
            patch('scanner.probe.os.getpgid', return_value=99999),
        ):
            asyncio.run(speed_tester.test(vless_config))
        mock_kill.assert_called_once_with(99999, signal.SIGTERM)
