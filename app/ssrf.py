"""SSRF validation primitives for outbound HTTP requests.

Two helpers, used together by the thumbnail fetcher in ``tasks.py``:

- ``validate_url(url)`` parses a URL, enforces scheme/port/credential
  allowlists, resolves the hostname once, and confirms every returned
  address is in public unicast space (rejecting RFC1918, loopback,
  link-local incl. the cloud-metadata address 169.254.169.254, multicast,
  reserved, and IPv4-mapped IPv6 equivalents).

- ``pin_dns(host, ip, port)`` is a context manager that forces
  ``socket.getaddrinfo`` to return the pre-validated IP for ``host`` for
  the duration of a request. This defeats DNS rebinding: the attacker
  cannot swap the resolution between validation and connection.

The caller is responsible for combining the two around each outbound

request, and for revalidating redirects. Anything derived from user input
must go through this module before hitting the network.
"""

import contextlib
import ipaddress
import socket
from urllib.parse import urlsplit


class SSRFError(ValueError):
    """Raised when a URL fails SSRF validation."""


ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (80, 443)
MAX_REDIRECTS = 5


def _is_public_ip(ip) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_public_ip(ip.ipv4_mapped)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_public_ip(host: str, port: int) -> str:
    """Resolve ``host`` and return one address after confirming every
    resolved address is in public unicast space. Raises SSRFError if any
    address is not public, so an attacker can't win by returning a mix of
    public and private records."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SSRFError(f"DNS resolution failed for {host!r}: {e}")
    if not infos:
        raise SSRFError(f"no addresses for {host!r}")
    first = None
    for info in infos:
        ip_str = str(info[4][0])
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SSRFError(f"unparseable address {ip_str!r} for {host!r}")
        if not _is_public_ip(ip):
            raise SSRFError(f"host {host!r} resolves to non-public address {ip_str}")
        if first is None:
            first = ip_str
    assert first is not None
    return first


def validate_url(url: str) -> tuple:
    """Parse ``url``, apply scheme/port/credential/IP allowlists, and
    return ``(scheme, host, port, resolved_ip)``. Raises SSRFError on any
    violation. DNS is resolved exactly once here; the caller should pin
    the returned IP for the actual connection."""
    if not isinstance(url, str) or not url:
        raise SSRFError("empty url")
    try:
        parsed = urlsplit(url)
    except ValueError as e:
        raise SSRFError(f"unparseable url: {e}")

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFError(f"scheme {parsed.scheme!r} not allowed")
    if parsed.username or parsed.password:
        raise SSRFError("credentials in URL not allowed")

    host = parsed.hostname
    if not host:
        raise SSRFError("missing host")

    try:
        port = parsed.port
    except ValueError as e:
        raise SSRFError(f"bad port: {e}")
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if port not in ALLOWED_PORTS:
        raise SSRFError(f"port {port} not allowed")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(literal):
            raise SSRFError(f"literal address {host} is not public")
        resolved = str(literal)
    else:
        resolved = _resolve_public_ip(host, port)

    return parsed.scheme, host, port, resolved


@contextlib.contextmanager
def pin_dns(host: str, pinned_ip: str, port: int):
    """Temporarily force ``socket.getaddrinfo`` to return ``pinned_ip`` for
    ``host``. This defeats DNS rebinding: even though ``requests`` will
    resolve the hostname again internally, it will receive the same IP we
    already validated. All other hostnames pass through to the real
    resolver.

    Note: gevent's monkey-patched socket module is also covered, because
    we patch the name on the ``socket`` module itself.
    """
    original = socket.getaddrinfo

    def patched(h, p, *args, **kwargs):
        if h == host:
            family = socket.AF_INET6 if ":" in pinned_ip else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 0, "", (pinned_ip, port))]
        return original(h, p, *args, **kwargs)

    socket.getaddrinfo = patched
    try:
        yield
    finally:
        socket.getaddrinfo = original
