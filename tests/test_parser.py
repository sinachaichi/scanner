import base64
import json

import pytest

from scanner.domain import ParsedConfig
from scanner.parser import ProxyParser
from scanner.protocols import get_handler


@pytest.fixture
def parser() -> ProxyParser:
    return ProxyParser()


# ── parse_query_params ────────────────────────────────────────────────────────

class TestParseQueryParams:
    def test_extracts_multiple_params(self, parser):
        link = 'vless://id@host:443?type=ws&security=tls#remark'
        assert parser.parse_query_params(link) == {'type': 'ws', 'security': 'tls'}

    def test_returns_empty_dict_when_no_query(self, parser):
        assert parser.parse_query_params('vless://id@host:443') == {}

    def test_returns_empty_dict_for_malformed_url(self, parser):
        assert parser.parse_query_params('not-a-url') == {}

    def test_value_containing_equals_is_preserved(self, parser):
        params = parser.parse_query_params('vless://id@host:443?path=/foo=bar')
        assert params.get('path') == '/foo=bar'


# ── ProtocolHandler.extract_host_port ────────────────────────────────────────

class TestExtractHostPort:
    def test_vless_ipv4(self):
        host, port = get_handler('vless').extract_host_port('vless://uuid@1.2.3.4:443?type=tcp')
        assert host == '1.2.3.4' and port == 443

    def test_trojan_domain(self):
        host, port = get_handler('trojan').extract_host_port('trojan://password@example.com:8443')
        assert host == 'example.com' and port == 8443

    def test_vmess_decodes_base64(self):
        payload = json.dumps({'add': '10.0.0.1', 'port': '1080', 'id': 'abc', 'ps': 'test'})
        b64 = base64.b64encode(payload.encode()).decode()
        host, port = get_handler('vmess').extract_host_port(f'vmess://{b64}')
        assert host == '10.0.0.1' and port == 1080

    def test_ss_with_at_sign(self):
        userpass = base64.b64encode(b'aes-256-gcm:password').decode()
        host, port = get_handler('ss').extract_host_port(f'ss://{userpass}@1.2.3.4:8388')
        assert host == '1.2.3.4' and port == 8388

    def test_returns_none_for_malformed_link(self):
        host, port = get_handler('vless').extract_host_port('vless://malformed')
        assert host is None and port is None

    def test_returns_none_for_empty_string(self):
        host, port = get_handler('vless').extract_host_port('')
        assert host is None and port is None


# ── ProtocolHandler.extract_user_id ──────────────────────────────────────────

class TestExtractUserId:
    def test_vless_returns_uuid_and_no_method(self):
        uid, method = get_handler('vless').extract_user_id('vless://my-uuid@host:443?type=tcp')
        assert uid == 'my-uuid' and method is None

    def test_trojan_returns_password(self):
        uid, method = get_handler('trojan').extract_user_id('trojan://s3cr3t@host:443')
        assert uid == 's3cr3t' and method is None

    def test_vmess_extracts_id_from_base64(self):
        payload = json.dumps({'id': 'test-uuid', 'add': 'host', 'port': 443, 'ps': ''})
        b64 = base64.b64encode(payload.encode()).decode()
        uid, method = get_handler('vmess').extract_user_id(f'vmess://{b64}')
        assert uid == 'test-uuid' and method is None

    def test_ss_returns_password_and_cipher(self):
        userpass = base64.b64encode(b'aes-256-gcm:mysecret').decode()
        uid, method = get_handler('ss').extract_user_id(f'ss://{userpass}@host:8388')
        assert uid == 'mysecret' and method == 'aes-256-gcm'


# ── parse ─────────────────────────────────────────────────────────────────────

class TestParse:
    def test_valid_vless_returns_parsed_config(self, parser):
        config = parser.parse('vless://my-uuid@1.2.3.4:443?type=tcp', 'vless')
        assert isinstance(config, ParsedConfig)
        assert config.host == '1.2.3.4'
        assert config.port == 443
        assert config.user_id == 'my-uuid'
        assert config.protocol == 'vless'
        assert config.method is None
        assert 'freedom-' in config.remark

    def test_valid_ss_populates_method(self, parser):
        userpass = base64.b64encode(b'aes-256-gcm:pass').decode()
        config = parser.parse(f'ss://{userpass}@1.2.3.4:8388', 'ss')
        assert config is not None
        assert config.method == 'aes-256-gcm'

    def test_malformed_link_returns_none(self, parser):
        assert parser.parse('vless://malformed', 'vless') is None

    def test_remark_is_appended_to_raw_link(self, parser):
        config = parser.parse('vless://uuid@1.2.3.4:443?type=tcp', 'vless')
        assert config is not None and '#' in config.raw_link and config.remark in config.raw_link

    def test_existing_remark_is_replaced_not_appended(self, parser):
        config = parser.parse('vless://uuid@1.2.3.4:443?type=tcp#old-remark', 'vless')
        assert config is not None
        assert 'old-remark' not in config.raw_link
        assert config.remark in config.raw_link

    def test_frozen_dataclass_rejects_mutation(self, parser):
        config = parser.parse('vless://uuid@1.2.3.4:443?type=tcp', 'vless')
        assert config is not None
        with pytest.raises((AttributeError, TypeError)):
            config.host = 'other'  # type: ignore[misc]


# ── extract_from_text ─────────────────────────────────────────────────────────

class TestExtractFromText:
    def test_extracts_multiple_protocols(self, parser):
        text = 'vless://uuid@1.1.1.1:443?type=tcp\nvmess://abc123\nother content'
        protos = {proto for _, proto in parser.extract_from_text(text)}
        assert 'vless' in protos and 'vmess' in protos

    def test_returns_empty_list_for_no_matches(self, parser):
        assert parser.extract_from_text('no proxy links here') == []

    def test_strips_whitespace_from_links(self, parser):
        text = '  vless://uuid@1.1.1.1:443?type=tcp  \n'
        results = parser.extract_from_text(text)
        assert results and not results[0][0].startswith(' ')
