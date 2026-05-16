import hashlib
from email.message import Message
from io import BytesIO
import math
import re
import time
from urllib.parse import urljoin, urlparse
import uuid

from bs4 import BeautifulSoup
from flask import current_app, jsonify
import gevent
from PIL import Image
import requests

from .config import config
from .misc import WHITESPACE, logger
from .ssrf import MAX_REDIRECTS, pin_dns, validate_url
from .storage import store_thumbnail, thumbnail_url
from .socketio import send_deferred_event


def parse_mimetype(headers):
    """Extract and return the mimetype from the Content-Type header."""
    m = Message()
    m["content-type"] = headers.get("Content-Type", "")
    try:
        return m.get_params()[0][0]
    except IndexError:
        return "text/plain"


def _parse_charset(headers):
    """Extract charset from Content-Type header."""
    m = Message()
    m["content-type"] = headers.get("Content-Type", "")
    return m.get_param("charset", "utf-8")


def should_use_proxy(url):
    """Check if the URL's domain matches one in the config.site.proxydomains list."""
    domain = urlparse(url).netloc
    proxy_domains = config.site.proxydomains
    return any(proxy_domain in domain for proxy_domain in proxy_domains)


def create_thumbnail(fileid, store):
    """Thumbnail a file that we already have in storage.
    Read the bytes directly from the storage backend."""
    if config.app.testing:
        _thumbnail_stored_file(fileid, store)
    else:
        gevent.spawn(
            _thumbnail_stored_file_appctx,
            current_app._get_current_object(),
            fileid,
            store,
        )


def _thumbnail_stored_file_appctx(app, fileid, store):
    with app.app_context():
        _thumbnail_stored_file(fileid, store)


def _thumbnail_stored_file(fileid, store):
    from .storage import read_stored_file

    data = read_stored_file(fileid)
    if data is None:
        logger.warning("No stored file found for %s", fileid)
    _apply_thumbnail(data, kind="image", source_label=fileid, store=store)


def create_thumbnail_external(link, store):
    """Try to create a thumbnail for an external link.  So as not to delay
    the response in the event the external server is slow, fetch from
    that server in a new gevent thread. Store should be a list of
    tuples consisting of database models, primary key names and
    primary key values.  When the thumbnail is successfully created,
    update the thumbnail fields of those records in the database, and emit
    socket server messages to anyone who might be waiting for that thumbnail.
    All outbound requests run through the SSRF-validated fetcher."""
    if config.app.testing:
        create_thumbnail_async(link, store)
    else:
        gevent.spawn(
            create_thumbnail_async_appctx,
            current_app._get_current_object(),
            link,
            store,
        )


def create_thumbnail_async_appctx(app, link, store):
    with app.app_context():
        create_thumbnail_async(link, store)


def create_thumbnail_async(link, store):
    typ, dat = fetch_image_data(link)
    if dat is None:
        logger.debug("No image data found at %s", link)
    _apply_thumbnail(dat, kind=typ or "image", source_label=link, store=store)


def _apply_thumbnail(data, kind, source_label, store):
    """Build a thumbnail from in-memory image bytes, write it, update the
    given DB rows, and broadcast the result. ``kind`` is ``"image"`` for
    a direct image (including uploaded files) or ``"favicon"`` for
    favicon-style PNGs that need a white background."""
    result = ""
    if data is not None:
        try:
            if kind == "favicon":
                logger.debug("Generating thumbnail from favicon at %s", source_label)
                im = Image.open(BytesIO(data))
                n_im = Image.new("RGBA", im.size, "WHITE")
                n_im.paste(im, (0, 0), im)
                img = n_im.convert("RGB")
            else:
                logger.debug("Generating thumbnail for %s", source_label)
                img = Image.open(BytesIO(data)).convert("RGB")
            result = thumbnail_from_img(img)
        except Exception as err:
            logger.warning("Failed to create thumbnail for %s: %s", source_label, err)
            result = ""

    for model, field, value in store:
        model.update(thumbnail=result).where(getattr(model, field) == value).execute()
        token = "-".join([model.__name__, str(value)])
        result_dict = {
            "target": token,
            "thumbnail": thumbnail_url(result) if result else "",
        }
        send_deferred_event("thumbnail", token, result_dict)


def fetch_image_data(link):
    """Try to fetch image data from a URL, and return it, or None.

    Every outbound request goes through ``fetch_external`` so both the
    initial page fetch and any og:image or favicon URL derived from its
    HTML are SSRF-validated."""
    try:
        resp, data = fetch_external(link, receive_timeout=60)
    except (requests.exceptions.RequestException, ValueError) as err:
        logger.debug(
            "Thumbnail task failed to fetch image data from %s: %s", link, str(err)
        )
        return None, None
    ctype = resp.headers.get("content-type", "").split(";")[0].lower()
    if ctype in ["image/gif", "image/jpeg", "image/png", "image/webp"]:
        # yay, it's an image!!1
        return "image", data
    elif ctype == "text/html":
        # Not an image!! Let's try with OpenGraph
        try:
            # Trim the HTML to the end of the last meta tag
            end_meta_tag = data.rfind(b"<meta")
            if end_meta_tag == -1:
                return None, None
            end_meta_tag = data.find(b">", end_meta_tag)
            if end_meta_tag == -1:
                end_meta_tag = len(data)
            else:
                end_meta_tag += 1
            data = data[:end_meta_tag]
            logger.debug("Fetched header of %s: %s bytes", link, len(data))
            charset = _parse_charset(resp.headers)
            start = time.time()
            og = BeautifulSoup(data, "lxml", from_encoding=charset)
            logger.debug(
                "Parsed HTML from %s in %s ms", link, int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.warning("Thumbnail fetch failed. Is lxml installed? Error: %s", e)
            return None, None

        og_image_props = ["og:image", "og:image:url"]
        for prop in og_image_props:
            try:
                img = urljoin(link, og("meta", {"property": prop})[0].get("content"))
                _, image = fetch_external(img, receive_timeout=60)
                return "image", image
            except (
                OSError,
                ValueError,
                IndexError,
                requests.exceptions.RequestException,
            ) as err:
                logger.debug("%s fetch from %s rejected: %s", prop, link, err)

        # no image, try fetching just the favicon then
        try:
            img = urljoin(link, og("link", {"rel": "icon"})[0].get("href"))
            _, icon = fetch_external(img, receive_timeout=60)
            return "favicon", icon
        except (
            OSError,
            ValueError,
            IndexError,
            requests.exceptions.RequestException,
        ) as err:
            logger.debug("favicon fetch from %s rejected: %s", link, err)
            return None, None
    return None, None


THUMB_NAMESPACE = uuid.UUID("f674f09a-4dcf-4e4e-a0b2-79153e27e387")


def thumbnail_from_img(im):
    """Generate a thumbnail from an image in memory and write it to
    storage.  Return the filename."""
    thash = hashlib.blake2b(im.tobytes())
    im = generate_thumb(im)
    filename = store_thumbnail(im, str(uuid.uuid5(THUMB_NAMESPACE, thash.hexdigest())))
    im.close()
    return filename


def generate_thumb(im: Image) -> Image:
    x, y = im.size
    while y > x:
        slice_height = min(y - x, 10)
        bottom = im.crop((0, y - slice_height, x, y))
        top = im.crop((0, 0, x, slice_height))

        if _image_entropy(bottom) < _image_entropy(top):
            im = im.crop((0, 0, x, y - slice_height))
        else:
            im = im.crop((0, slice_height, x, y))

        x, y = im.size
        gevent.sleep(0)

    im.thumbnail((140, 140), Image.LANCZOS)
    return im


def _image_entropy(img):
    """calculate the entropy of an image"""
    hist = img.histogram()
    hist_size = sum(hist)
    hist = [float(h) / hist_size for h in hist]

    return -sum(p * math.log(p, 2) for p in hist if p != 0)


def _read_body(r, max_size, receive_timeout, partial_read):
    """Stream the response body with a wall-clock timeout and size cap.
    Extracted so the external and internal paths share identical handling."""
    if int(r.headers.get("Content-Length", 1)) > max_size and not partial_read:
        raise ValueError("response too large")

    size = 0
    start = time.time()
    f = b""
    for chunk in r.iter_content(1024):
        if time.time() - start > receive_timeout:
            raise ValueError("timeout reached")
        gevent.sleep(0)

        size += len(chunk)
        f += chunk
        if size > max_size:
            if partial_read:
                return f
            raise ValueError("response too large")
    return f


_REQUEST_HEADERS = {"User-Agent": "Yahoo! Slurp/Site Explorer"}
_REQUEST_HEADERS_MOBILE = {
    "User-Agent": "Mozilla/5.0 (Android 13; Mobile; rv:109.0) Gecko/113.0 Firefox/113.0"
}
_REQUEST_COOKIES = {"CONSENT": "PENDING+999"}


def fetch_external(
    url, receive_timeout=10, max_size=25000000, mimetypes=None, partial_read=False
):
    """Fetch a URL derived from user input, with SSRF protections.

    Enforces scheme/port allowlists, blocks non-public destinations, pins
    the resolved IP across the TCP connection to defeat DNS rebinding, and
    manually follows redirects, revalidating each hop. Uses a proxy for
    domains configured in config.site.proxydomains, falling back to a
    mobile user agent if the first attempt fails. Raises SSRFError for
    policy violations and ValueError for transport or size/timeout errors.
    """
    current_url = url
    for hop in range(MAX_REDIRECTS + 1):
        _scheme, host, port, resolved_ip = validate_url(current_url)

        use_proxy = should_use_proxy(current_url)
        proxies = config.site.proxy if use_proxy else None
        headers_list = [_REQUEST_HEADERS, _REQUEST_HEADERS_MOBILE]

        with pin_dns(host, resolved_ip, port):
            r = None
            last_error = None
            for headers in headers_list:
                try:
                    r = requests.get(
                        current_url,
                        stream=True,
                        timeout=receive_timeout,
                        headers=headers,
                        cookies=_REQUEST_COOKIES,
                        proxies=proxies,
                        allow_redirects=False,
                    )
                    break
                except (
                    requests.exceptions.ProxyError,
                    requests.exceptions.ConnectionError,
                ) as e:
                    if use_proxy and proxies:
                        logger.warning(
                            "Proxy error for %s: %s. Retrying without proxy.",
                            current_url,
                            e,
                        )
                        proxies = None
                        try:
                            r = requests.get(
                                current_url,
                                stream=True,
                                timeout=receive_timeout,
                                headers=headers,
                                cookies=_REQUEST_COOKIES,
                                allow_redirects=False,
                            )
                            break
                        except requests.exceptions.RequestException as e2:
                            last_error = e2
                    else:
                        last_error = e
                except requests.exceptions.RequestException as e:
                    last_error = e

            if r is None:
                raise ValueError(f"error fetching: {last_error}")

            if r.is_redirect or r.is_permanent_redirect:
                location = r.headers.get("Location")
                r.close()
                if not location:
                    raise ValueError("redirect without Location header")
                if hop >= MAX_REDIRECTS:
                    raise ValueError("too many redirects")
                current_url = urljoin(current_url, location)
                continue

            r.raise_for_status()

            if mimetypes is not None:
                mtype = parse_mimetype(r.headers)
                if mtype not in mimetypes:
                    raise ValueError("wrong content type")

            body = _read_body(r, max_size, receive_timeout, partial_read)
            return r, body

    raise ValueError("too many redirects")


def grab_title(url):
    """Start the grab title process.  Returns a response with a token
    which can be used to get the actual title via socketio, once it has
    been fetched."""
    if config.app.testing:
        return jsonify(grab_title_async(current_app, url))
    else:
        token = "title-" + str(uuid.uuid4())
        gevent.spawn(
            send_title_grab_async, current_app._get_current_object(), url, token
        )
        return jsonify(status="deferred", token=token)


def send_title_grab_async(app, url, token):
    """Grab the title from the url and send it to whoever might be waiting
    via socketio."""
    result = grab_title_async(app, url)
    result.update(target=token)
    with app.app_context():
        logger.info("Grabbed title: %s", result)
        send_deferred_event("grab_title", token, result)


def grab_title_async(app, url):
    with app.app_context():
        try:
            resp, data = fetch_external(
                url, max_size=500000, mimetypes={"text/html"}, partial_read=True
            )

            end_title_pos = data.find(b"</title>")
            if end_title_pos != -1:
                data = data[:end_title_pos] + b"</title></head><body></body>"

            charset = _parse_charset(resp.headers)
            og = BeautifulSoup(data, "lxml", from_encoding=charset)

            title = None
            if og.title:
                title = og.title.string.strip(WHITESPACE)

            if not title:
                og_meta_title = og.find("meta", property="og:title")
                if og_meta_title and og_meta_title.get("content"):
                    title = og_meta_title["content"].strip(WHITESPACE)

            if title:
                title = re.sub(" - YouTube$", "", title)
                return {"status": "ok", "title": title}

            raise ValueError("No title or og:title found.")

        except (
            requests.exceptions.RequestException,
            ValueError,
            OSError,
            IndexError,
            KeyError,
        ):
            return {"status": "error"}
