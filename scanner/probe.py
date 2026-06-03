import asyncio
import json
import logging
import os
import signal
import socket
import tempfile
import time

from .domain import ParsedConfig, ProbeResult
from .parser import ProxyParser
from .protocols import get_handler

logger = logging.getLogger(__name__)


class TcpProbe:
    """Tests TCP reachability of a (host, port) pair."""

    def __init__(self, timeout: float = 2.0, threshold_ms: int = 1050):
        self.timeout = timeout
        self.threshold_ms = threshold_ms

    async def is_reachable(self, host: str, port: int) -> bool:
        """Return True if a TCP connection can be established within the latency threshold."""
        delay = await self.ping(host, port)
        return 0 < delay < self.threshold_ms

    async def ping(self, host: str, port: int) -> int:
        """Return round-trip time in milliseconds, or -1 on any failure."""
        try:
            start = time.monotonic()
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self.timeout,
            )
            elapsed = time.monotonic() - start
            writer.close()
            await writer.wait_closed()
            return int(elapsed * 1000)
        except (OSError, asyncio.TimeoutError):
            return -1


class XraySpeedTester:
    """Measures proxy throughput by routing a 100 KB download through xray-core."""

    _TEST_BYTES = 100 * 1024
    # Cloudflare's speed endpoint is edge-cached globally and resistant to
    # country-level filtering — more reliable than tele2 for Iranian networks.
    _TEST_URL = f'https://speed.cloudflare.com/__down?bytes={_TEST_BYTES}'

    def __init__(self, xray_path: str, parser: ProxyParser, timeout: int = 12):
        self.xray_path = xray_path
        self.timeout = timeout
        self.parser = parser

    async def test(self, config: ParsedConfig) -> ProbeResult:
        """
        Launch xray with config, curl 100 KB through the SOCKS5 proxy, measure KB/s.
        The xray process and temp config file are always cleaned up in the finally block.
        """
        proc = None
        socks_port = self.allocate_port()
        fd, config_path = tempfile.mkstemp(suffix='.json', prefix='xray_test_')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.build_xray_config(config, socks_port), f)

            proc = await asyncio.create_subprocess_exec(
                self.xray_path, 'run', '-c', config_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )

            if not await self._wait_for_port(socks_port):
                logger.warning(
                    "xray failed to bind socks port %d for %s:%d",
                    socks_port, config.host, config.port,
                )
                return ProbeResult(config=config, is_working=False, speed_kbps=0.0)

            start = time.monotonic()
            curl_proc = await asyncio.create_subprocess_exec(
                'curl', '--socks5-hostname', f'127.0.0.1:{socks_port}',
                '-o', '/dev/null',
                '-m', str(self.timeout),
                '--connect-timeout', '5',
                self._TEST_URL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr_bytes = await asyncio.wait_for(
                curl_proc.communicate(),
                timeout=self.timeout + 5,
            )
            duration = time.monotonic() - start

            if curl_proc.returncode == 0:
                speed = round(self._TEST_BYTES / 1024 / duration, 2)
                logger.info(
                    "%s %s:%d — %.1f KB/s",
                    config.protocol.upper(), config.host, config.port, speed,
                )
                return ProbeResult(config=config, is_working=True, speed_kbps=speed)

            logger.debug(
                "Speed test failed for %s:%d: %s",
                config.host, config.port, stderr_bytes.decode(errors='replace').strip(),
            )
            return ProbeResult(config=config, is_working=False, speed_kbps=0.0)

        except (OSError, asyncio.TimeoutError) as exc:
            logger.error("xray execution error for %s:%d: %s", config.host, config.port, exc)
            return ProbeResult(config=config, is_working=False, speed_kbps=0.0)
        finally:
            if proc is not None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    await proc.wait()
                except OSError:
                    pass
            try:
                os.unlink(config_path)
            except OSError:
                pass

    def build_xray_config(self, config: ParsedConfig, socks_port: int) -> dict:
        stream = self.build_stream_settings(config)
        outbound = get_handler(config.protocol).build_outbound(config, stream)
        return {
            'log': {'loglevel': 'warning'},
            'inbounds': [{
                'port': socks_port,
                'listen': '127.0.0.1',
                'protocol': 'socks',
                'settings': {'udp': True},
            }],
            'outbounds': [outbound],
        }

    def build_stream_settings(self, config: ParsedConfig) -> dict:
        params = self.parser.parse_query_params(config.raw_link)
        stream: dict = {'network': params.get('type', 'tcp')}

        if params.get('security') == 'tls':
            stream['security'] = 'tls'
            if 'sni' in params:
                stream['tlsSettings'] = {'serverName': params['sni']}
        if stream['network'] == 'ws':
            stream['wsSettings'] = {
                'path': params.get('path', '/'),
                'headers': {'Host': params.get('host', config.host)},
            }
        if stream['network'] == 'grpc':
            stream['grpcSettings'] = {
                'serviceName': params.get('serviceName', ''),
                'multiMode': False,
            }

        return stream

    @staticmethod
    async def _wait_for_port(port: int, timeout: float = 4.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection('127.0.0.1', port),
                    timeout=0.2,
                )
                writer.close()
                await writer.wait_closed()
                return True
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.1)
        return False

    @staticmethod
    def allocate_port() -> int:
        """Ask the OS for a free local port. Thread-safe, no collision risk."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]
