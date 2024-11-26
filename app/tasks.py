import cgi
import hashlib
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
from .storage import store_thumbnail, thumbnail_url
from .socketio import send_deferred_event


def create_thumbnail(fileid, store):
    from .storage import file_url

    create_thumbnail_external(file_url(fileid), store)


def create_thumbnail_external(link, store):
    """Try to create a thumbnail for an external link.  So as not to delay
    the response in the event the external server is slow, fetch from
    that server in a new gevent thread. Store should be a list of
    tuples consisting of database models, primary key names and
    primary key values.  When the thumbnail is successfully created,
    update the thumbnail fields of those records in the database, and emit
    socket server messages to anyone who might be waiting for that thumbnail.
    """
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
    result = ""
    typ, dat = fetch_image_data(link)
    if dat is not None:
        if typ == "image":
            logger.debug("Generating thumbnail for %s", link)
            img = Image.open(BytesIO(dat)).convert("RGB")
        else:  # favicon
            logger.debug("Generating thumbnail from favicon at %s", link)
            im = Image.open(BytesIO(dat))
            n_im = Image.new("RGBA", im.size, "WHITE")
            n_im.paste(im, (0, 0), im)
            img = n_im.convert("RGB")
        result = thumbnail_from_img(img)
    else:
        logger.debug("No image data found at %s", link)
    for model, field, value in store:
        model.update(thumbnail=result).where(getattr(model, field) == value).execute()
        token = "-".join([model.__name__, str(value)])
        result_dict = {
            "target": token,
            "thumbnail": thumbnail_url(result) if result else "",
        }
        send_deferred_event("thumbnail", token, result_dict)


def fetch_image_data(link):
    """Try to fetch image data from a URL , and return it, or None."""
    # 1 - Check if it's an image
    try:
        resp, data = safe_request(link, receive_timeout=60)
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
            _, options = cgi.parse_header(resp.headers.get("Content-Type", ""))
            charset = options.get("charset", "utf-8")
            start = time.time()
            og = BeautifulSoup(data, "lxml", from_encoding=charset)
            logger.debug(
                "Parsed HTML from %s in %s ms", link, int((time.time() - start) * 1000)
            )
        except Exception as e:
            # If it errors here it's probably because lxml is not installed.
            logger.warning("Thumbnail fetch failed. Is lxml installed? Error: %s", e)
            return None, None
        try:
            img = urljoin(link, og("meta", {"property": "og:image"})[0].get("content"))
            _, image = safe_request(img, receive_timeout=60)
            return "image", image
        except (OSError, ValueError, IndexError):
            # for those weirdos using og:image:url instead
            try:
                img = urljoin(
                    link, og("meta", {"property": "og:image:url"})[0].get("content")
                )
                _, image = safe_request(img, receive_timeout=60)
                return "image", image
            except (OSError, ValueError, IndexError):
                # no image, try fetching just the favicon then
                try:
                    img = urljoin(link, og("link", {"rel": "icon"})[0].get("href"))
                    _, icon = safe_request(img, receive_timeout=60)
                    return "favicon", icon
                except (OSError, ValueError, IndexError):
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

    im.thumbnail((140, 140), Image.ANTIALIAS)
    return im


def _image_entropy(img):
    """calculate the entropy of an image"""
    hist = img.histogram()
    hist_size = sum(hist)
    hist = [float(h) / hist_size for h in hist]

    return -sum(p * math.log(p, 2) for p in hist if p != 0)


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
            resp, data = safe_request(
                url, max_size=500000, mimetypes={"text/html"}, partial_read=True
            )

            # Truncate the HTML to minimize parsing workload
            end_title_pos = data.find(b"</title>")
            if end_title_pos != -1:
                data = data[:end_title_pos] + b"</title></head><body></body>"

            _, options = cgi.parse_header(resp.headers.get("Content-Type", ""))
            charset = options.get("charset", "utf-8")
            og = BeautifulSoup(data, "lxml", from_encoding=charset)

            # Attempt to fetch the <title> tag content
            title = None
            if og.title:
                title = og.title.string.strip(WHITESPACE)

            # Fallback: Check for <meta property="og:title">
            if not title:
                og_meta_title = og.find("meta", property="og:title")
                if og_meta_title and og_meta_title.get("content"):
                    title = og_meta_title["content"].strip(WHITESPACE)

            # Final title processing
            if title:
                title = re.sub(" - YouTube$", "", title)
                return {"status": "ok", "title": title}

            # No title found
            raise ValueError("No title or og:title found.")

        except (
            requests.exceptions.RequestException,
            ValueError,
            OSError,
            IndexError,
            KeyError,
        ):
            return {"status": "error"}


def should_use_proxy(url):
    """
    Check if the URL's domain matches one in the config.site.proxydomains list.
    """
    domain = urlparse(url).netloc
    proxy_domains = config.site.proxydomains  # Load domains from configuration
    return any(proxy_domain in domain for proxy_domain in proxy_domains)


def make_request(url, timeout, user_agent, proxies=None):
    """
    Helper function to make a request.
    Retries without proxy if a proxy error occurs and the domain is not in the proxy list.
    """
    headers = {"User-Agent": user_agent}
    cookies = {"CONSENT": "PENDING+999"}

    def fetch(proxies_to_use):
        """
        Inner function to perform the actual HTTP request.
        """
        return requests.get(
            url,
            stream=True,
            timeout=timeout,
            headers=headers,
            cookies=cookies,
            proxies=proxies_to_use,
        )

    # Determine if we should use a proxy based on the domain
    use_proxy = should_use_proxy(url)
    current_proxies = proxies if use_proxy else None

    try:
        response = fetch(
            current_proxies
        )  # Pass the outer variable to the inner function
        response.raise_for_status()
        return response
    except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
        if use_proxy:  # Retry without proxy only if the proxy was initially used
            print(f"ProxyError: {e}. Retrying without proxy.")
            return fetch(None)
        raise ValueError(f"Error fetching URL: {e}")


def safe_request(
    url, receive_timeout=10, max_size=25000000, mimetypes=None, partial_read=False
):
    """
    Fetches data with constraints on size, type, and timeout.
    """
    # Modify the URL if it's a relative path
    if url.startswith("/") and config.storage.server and "server_name" in config.site:
        url = f"http://{config.site.server_name}{url}"

    proxies = config.site.proxy  # Proxy configuration
    user_agents = [
        "Yahoo! Slurp/Site Explorer",
        "Mozilla/5.0 (Android 13; Mobile; rv:109.0) Gecko/113.0 Firefox/113.0",
    ]

    # Attempt to fetch data with different user agents
    for user_agent in user_agents:
        try:
            response = make_request(url, receive_timeout, user_agent, proxies)
            break  # Exit loop on success
        except ValueError as e:
            print(f"Retrying with a different user agent: {e}")
    else:
        raise ValueError("Failed to fetch URL with all user agents.")

    # Check content length
    content_length = int(response.headers.get("Content-Length", 1))
    if content_length > max_size and not partial_read:
        raise ValueError("Response too large.")

    # Check content type if mimetypes are specified
    if mimetypes:
        content_type, _ = cgi.parse_header(response.headers.get("Content-Type", ""))
        if content_type not in mimetypes:
            raise ValueError(f"Invalid content type: {content_type}")

    # Read the response content
    size = 0
    start_time = time.time()
    content = b""  # Use immutable bytes for simplicity
    for chunk in response.iter_content(1024):
        if time.time() - start_time > receive_timeout:
            raise ValueError("Timeout reached while reading response.")
        gevent.sleep(0)  # Yield to other greenlets

        size += len(chunk)
        content += chunk
        if size > max_size:
            if partial_read:
                return response, content
            raise ValueError("Response too large.")

    return response, content
