"""Unit tests for SSRF protections in app.ssrf and app.tasks.fetch_external.

These tests are self-contained: they monkeypatch ``socket.getaddrinfo`` and
``requests.get`` directly and do not require the Flask app fixtures.
"""

import socket
from unittest.mock import MagicMock

import pytest
import requests

from app import ssrf
from app import tasks


def _addrinfo(ip, port):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 0, "", (ip, port))]


@pytest.fixture
def resolve_to(monkeypatch):
    """Factory fixture that makes ``socket.getaddrinfo`` return a fixed IP
    for any hostname during the test."""

    def _factory(ip):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return _addrinfo(ip, port or 80)

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    return _factory


class TestValidateURL:
    def test_accepts_plain_http_url(self, resolve_to):
        resolve_to("93.184.216.34")
        scheme, host, port, ip = ssrf.validate_url("http://example.com/foo")
        assert scheme == "http"
        assert host == "example.com"
        assert port == 80
        assert ip == "93.184.216.34"

    def test_accepts_https_default_port(self, resolve_to):
        resolve_to("93.184.216.34")
        _, _, port, _ = ssrf.validate_url("https://example.com/")
        assert port == 443

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://example.com/",
            "dict://example.com:11211/",
            "ftp://example.com/",
            "javascript:alert(1)",
        ],
    )
    def test_rejects_non_http_schemes(self, url):
        with pytest.raises(ssrf.SSRFError):
            ssrf.validate_url(url)

    def test_rejects_credentials_in_url(self, resolve_to):
        resolve_to("93.184.216.34")
        with pytest.raises(ssrf.SSRFError, match="credentials"):
            ssrf.validate_url("http://user:pass@example.com/")

    @pytest.mark.parametrize(
        "url", ["http://example.com:22/", "http://example.com:6379/"]
    )
    def test_rejects_non_http_ports(self, url, resolve_to):
        resolve_to("93.184.216.34")
        with pytest.raises(ssrf.SSRFError, match="port"):
            ssrf.validate_url(url)

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.1.2.3",
            "172.16.0.5",
            "192.168.1.1",
            "169.254.169.254",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
        ],
    )
    def test_rejects_non_public_ip_literals(self, ip):
        with pytest.raises(ssrf.SSRFError):
            ssrf.validate_url(f"http://{ip}/")

    def test_rejects_ipv4_mapped_private_ipv6(self):
        with pytest.raises(ssrf.SSRFError):
            ssrf.validate_url("http://[::ffff:169.254.169.254]/")

    def test_rejects_hostname_resolving_to_private(self, resolve_to):
        resolve_to("127.0.0.1")
        with pytest.raises(ssrf.SSRFError, match="non-public"):
            ssrf.validate_url("http://evil.example.com/")

    def test_rejects_mixed_public_and_private_resolution(self, monkeypatch):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return _addrinfo("93.184.216.34", port or 80) + _addrinfo(
                "10.0.0.1", port or 80
            )

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ssrf.SSRFError):
            ssrf.validate_url("http://split.example.com/")

    def test_dns_failure_raises_ssrf_error(self, monkeypatch):
        def fake_getaddrinfo(*args, **kwargs):
            raise socket.gaierror("no such host")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ssrf.SSRFError, match="DNS"):
            ssrf.validate_url("http://nowhere.example/")


class TestPinDNS:
    def test_patches_only_target_host(self, monkeypatch):
        calls = []

        def real_getaddrinfo(host, port, *args, **kwargs):
            calls.append(host)
            return _addrinfo("93.184.216.34", port or 80)

        monkeypatch.setattr(socket, "getaddrinfo", real_getaddrinfo)

        with ssrf.pin_dns("example.com", "203.0.113.5", 80):
            pinned = socket.getaddrinfo("example.com", 80)
            passthrough = socket.getaddrinfo("other.example", 80)

        assert pinned[0][4][0] == "203.0.113.5"
        assert passthrough[0][4][0] == "93.184.216.34"
        assert "other.example" in calls

    def test_restores_original_resolver(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: sentinel)
        with ssrf.pin_dns("example.com", "203.0.113.5", 80):
            pass
        assert socket.getaddrinfo("any", 0) is sentinel


class _FakeResponse:
    def __init__(
        self, status=200, headers=None, body=b"", is_redirect=False, location=None
    ):
        self.status_code = status
        self.headers = headers or {}
        if location is not None:
            self.headers.setdefault("Location", location)
        self._body = body
        self.is_redirect = is_redirect
        self.is_permanent_redirect = False

    def iter_content(self, chunk_size):
        if not self._body:
            return iter([])
        return iter(
            [
                self._body[i : i + chunk_size]
                for i in range(0, len(self._body), chunk_size)
            ]
        )

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def close(self):
        pass


class TestFetchExternal:
    @pytest.fixture(autouse=True)
    def _silence_logger(self, monkeypatch):
        monkeypatch.setattr(tasks, "logger", MagicMock())

    @pytest.fixture(autouse=True)
    def _no_proxy(self, monkeypatch):
        monkeypatch.setattr(tasks, "should_use_proxy", lambda url: False)

    def test_happy_path_returns_body(self, monkeypatch, resolve_to):
        resolve_to("93.184.216.34")
        fake_get = MagicMock(
            return_value=_FakeResponse(
                headers={"Content-Type": "image/png", "Content-Length": "3"},
                body=b"PNG",
            )
        )
        monkeypatch.setattr(tasks.requests, "get", fake_get)

        resp, body = tasks.fetch_external("http://example.com/img.png")
        assert body == b"PNG"
        assert resp.headers["Content-Type"] == "image/png"
        assert fake_get.call_args.kwargs["allow_redirects"] is False

    def test_rejects_internal_host_before_http(self, monkeypatch):
        fake_get = MagicMock()
        monkeypatch.setattr(tasks.requests, "get", fake_get)
        with pytest.raises(ssrf.SSRFError):
            tasks.fetch_external("http://127.0.0.1/admin")
        assert fake_get.call_count == 0

    def test_rejects_aws_metadata_before_http(self, monkeypatch):
        fake_get = MagicMock()
        monkeypatch.setattr(tasks.requests, "get", fake_get)
        with pytest.raises(ssrf.SSRFError):
            tasks.fetch_external(
                "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            )
        assert fake_get.call_count == 0

    def test_redirect_to_internal_is_blocked(self, monkeypatch, resolve_to):
        resolve_to("93.184.216.34")
        responses = iter(
            [
                _FakeResponse(
                    status=302,
                    headers={"Content-Type": "text/html"},
                    is_redirect=True,
                    location="http://169.254.169.254/",
                ),
            ]
        )
        monkeypatch.setattr(
            tasks.requests,
            "get",
            MagicMock(side_effect=lambda *a, **k: next(responses)),
        )

        with pytest.raises(ssrf.SSRFError):
            tasks.fetch_external("http://example.com/start")

    def test_redirect_followed_when_target_is_public(self, monkeypatch):
        resolutions = {
            "a.example": "93.184.216.34",
            "b.example": "93.184.216.35",
        }

        def fake_getaddrinfo(host, port, *args, **kwargs):
            return _addrinfo(resolutions.get(host, "93.184.216.34"), port or 80)

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

        responses = [
            _FakeResponse(
                status=302,
                headers={"Content-Type": "text/html"},
                is_redirect=True,
                location="http://b.example/final",
            ),
            _FakeResponse(
                headers={"Content-Type": "image/png", "Content-Length": "3"},
                body=b"PNG",
            ),
        ]
        it = iter(responses)
        monkeypatch.setattr(
            tasks.requests, "get", MagicMock(side_effect=lambda *a, **k: next(it))
        )

        _, body = tasks.fetch_external("http://a.example/start")
        assert body == b"PNG"

    def test_too_many_redirects_raises(self, monkeypatch, resolve_to):
        resolve_to("93.184.216.34")

        def always_redirect(*args, **kwargs):
            return _FakeResponse(
                status=302,
                headers={"Content-Type": "text/html"},
                is_redirect=True,
                location="http://example.com/loop",
            )

        monkeypatch.setattr(
            tasks.requests, "get", MagicMock(side_effect=always_redirect)
        )
        with pytest.raises(ValueError, match="redirects"):
            tasks.fetch_external("http://example.com/")


class TestCreateThumbnailStoredFile:
    @pytest.fixture(autouse=True)
    def _silence_logger(self, monkeypatch):
        monkeypatch.setattr(tasks, "logger", MagicMock())

    def test_reads_from_storage_without_http(self, monkeypatch):
        """The uploaded-file thumbnail path must not call requests.get —
        that's the whole point of routing through read_stored_file."""
        from app import storage

        fake_get = MagicMock()
        monkeypatch.setattr(tasks.requests, "get", fake_get)
        monkeypatch.setattr(
            storage, "read_stored_file", lambda name: b"\x89PNG\r\n\x1a\n"
        )
        applied = {}

        def fake_apply(data, kind, source_label, store):
            applied["data"] = data
            applied["kind"] = kind

        monkeypatch.setattr(tasks, "_apply_thumbnail", fake_apply)

        tasks._thumbnail_stored_file("abc123.png", store=[])

        assert fake_get.call_count == 0
        assert applied["data"] == b"\x89PNG\r\n\x1a\n"
        assert applied["kind"] == "image"

    def test_missing_stored_file_still_calls_apply_with_none(self, monkeypatch):
        from app import storage

        monkeypatch.setattr(storage, "read_stored_file", lambda name: None)
        calls = []
        monkeypatch.setattr(
            tasks,
            "_apply_thumbnail",
            lambda data, kind, source_label, store: calls.append(data),
        )

        tasks._thumbnail_stored_file("missing.png", store=[])

        assert calls == [None]


class TestFetchImageDataSSRF:
    @pytest.fixture(autouse=True)
    def _silence_logger(self, monkeypatch):
        monkeypatch.setattr(tasks, "logger", MagicMock())

    @pytest.fixture(autouse=True)
    def _no_proxy(self, monkeypatch):
        monkeypatch.setattr(tasks, "should_use_proxy", lambda url: False)

    def test_og_image_pointing_to_metadata_is_blocked(self, monkeypatch, resolve_to):
        """The documented SSRF PoC: attacker page returns og:image pointing
        to AWS metadata. fetch_image_data must refuse the second fetch and
        fall back without ever hitting the metadata service."""
        resolve_to("93.184.216.34")

        html = (
            b"<html><head>"
            b'<meta property="og:image" '
            b'content="http://169.254.169.254/latest/meta-data/iam/">'
            b'<link rel="icon" href="http://10.0.0.1/favicon.ico">'
            b"</head></html>"
        )
        responses = [
            _FakeResponse(
                headers={"Content-Type": "text/html", "Content-Length": str(len(html))},
                body=html,
            )
        ]
        call_log = []

        def fake_get(url, *args, **kwargs):
            call_log.append(url)
            if not responses:
                raise AssertionError(f"unexpected fetch to {url}")
            return responses.pop(0)

        monkeypatch.setattr(tasks.requests, "get", MagicMock(side_effect=fake_get))

        typ, dat = tasks.fetch_image_data("http://attacker.example/page.html")
        assert (typ, dat) == (None, None)
        assert call_log == ["http://attacker.example/page.html"]
