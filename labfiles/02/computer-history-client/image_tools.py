"""Provided image download support for the function-calling lab."""

import json
import logging
import math
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

DOWNLOADS_DIR = Path(__file__).resolve().parents[2] / "downloads"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
}
logger = logging.getLogger(__name__)
_request_lock = Lock()
_retry_at = 0.0


class WikimediaRateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Wikimedia rate limited requests (HTTP 429). Wait at least "
            f"{retry_after_seconds} seconds before trying again. "
            "Do not search for replacement images or retry downloads in this request."
        )


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirected downloads are not supported. Choose another image.")


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _metadata_text(metadata: dict, name: str) -> str:
    parser = _PlainText()
    parser.feed(str(metadata.get(name, {}).get("value", "")))
    return " ".join(" ".join(parser.parts).split())


def _validate_url(url: str, host: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != host
            or parsed.username or parsed.password):
        raise ValueError(f"Only HTTPS URLs on {host} are supported.")


def _retry_delay(value: str | None) -> int:
    if value is None:
        return 60
    if value.strip().isdecimal():
        return max(1, int(value))
    try:
        retry_time = parsedate_to_datetime(value)
        if retry_time.tzinfo is None:
            retry_time = retry_time.replace(tzinfo=timezone.utc)
        return max(1, math.ceil(retry_time.timestamp() - time.time()))
    except (TypeError, ValueError, OverflowError):
        logger.warning("Invalid Wikimedia Retry-After header: %r; using 60 seconds", value)
        return 60


def _read_url(url: str, host: str, limit: int) -> tuple[bytes, str]:
    global _retry_at
    _validate_url(url, host)
    request = Request(url, headers={
        "User-Agent": (
            "ComputingHistoryLearningLab/1.1 "
            "(+https://github.com/GraemeMalcolm/mslearn-foundry-toolkit)"
        ),
    })
    with _request_lock:
        remaining = _retry_at - time.monotonic()
        if remaining > 0:
            raise WikimediaRateLimitError(math.ceil(remaining))
        try:
            with build_opener(_NoRedirects()).open(request, timeout=30) as response:
                content = response.read(limit + 1)
                if len(content) > limit:
                    raise ValueError("The download exceeds the supported size limit.")
                return content, response.headers.get_content_type()
        except HTTPError as error:
            if error.code != 429:
                raise
            delay = _retry_delay(error.headers.get("Retry-After"))
            _retry_at = time.monotonic() + delay
            error.close()
            raise WikimediaRateLimitError(delay) from error


def download_commons_image(commons_page_url: str) -> dict[str, str]:
    """Verify Commons license metadata and save a PNG or JPEG of up to 5 MB."""
    if not isinstance(commons_page_url, str):
        raise ValueError("Provide a Wikimedia Commons File page URL.")
    _validate_url(commons_page_url, "commons.wikimedia.org")
    parsed = urlsplit(commons_page_url)
    title = unquote(parsed.path.removeprefix("/wiki/"))
    if not parsed.path.startswith("/wiki/File:") or not title[5:]:
        raise ValueError("Provide a Wikimedia Commons /wiki/File: page URL.")

    query = urlencode({
        "action": "query", "format": "json", "redirects": 1,
        "prop": "imageinfo", "titles": title,
        "iiprop": "url|mime|size|extmetadata",
    })
    content, _ = _read_url(
        f"https://commons.wikimedia.org/w/api.php?{query}",
        "commons.wikimedia.org", 1024 * 1024,
    )
    data = json.loads(content)
    if "error" in data:
        raise ValueError("Wikimedia Commons could not retrieve the image metadata.")
    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    images = page.get("imageinfo", [])
    if not images:
        raise ValueError("The Commons file was not found. Search for another File page.")
    image = images[0]
    metadata = image.get("extmetadata", {})
    license_name = _metadata_text(metadata, "LicenseShortName")
    cc_license = re.fullmatch(
        r"CC BY(-SA)? (1\.0|2\.0|2\.5|3\.0|4\.0)(?: [a-z]{2,3})?", license_name
    )
    if license_name == "Public domain":
        license_url = "https://creativecommons.org/publicdomain/mark/1.0/"
    elif license_name == "CC0":
        license_url = "https://creativecommons.org/publicdomain/zero/1.0/"
    elif cc_license:
        license_url = _metadata_text(metadata, "LicenseUrl")
        if license_url.startswith("http://"):
            license_url = "https://" + license_url.removeprefix("http://")
        _validate_url(license_url, "creativecommons.org")
        kind = "by-sa" if cc_license.group(1) else "by"
        license_path = urlsplit(license_url).path
        expected_path = f"/licenses/{kind}/{cc_license.group(2)}"
        if license_path != expected_path and not license_path.startswith(expected_path + "/"):
            raise ValueError("The Creative Commons license URL could not be verified.")
    else:
        raise ValueError("A supported public domain, CC0, CC BY, or CC BY-SA license was not found.")
    if _metadata_text(metadata, "Restrictions"):
        raise ValueError("This image has additional restrictions. Choose another image.")
    attribution = _metadata_text(metadata, "Attribution") or _metadata_text(metadata, "Artist")
    credit = _metadata_text(metadata, "Credit")
    if not attribution or (cc_license and attribution.lower() == "unknown author"):
        raise ValueError("The image has insufficient attribution metadata. Choose another image.")
    if credit:
        attribution += f". {credit}"
    permission = _metadata_text(metadata, "Permission")
    source_url = image.get("descriptionurl", "")
    _validate_url(source_url, "commons.wikimedia.org")
    if not urlsplit(source_url).path.startswith("/wiki/File:"):
        raise ValueError("The source is not a Commons File page.")

    mime = image.get("mime")
    if mime not in IMAGE_TYPES:
        raise ValueError("Choose a PNG or JPEG image.")
    if not isinstance(image.get("size"), int) or not 0 < image["size"] <= MAX_IMAGE_BYTES:
        raise ValueError("Choose an image of 5 MB or smaller.")
    content, actual_mime = _read_url(
        image.get("url", ""), "upload.wikimedia.org", MAX_IMAGE_BYTES,
    )
    extension, signature = IMAGE_TYPES[mime]
    if actual_mime != mime or not content.startswith(signature):
        raise ValueError("The downloaded file does not match its PNG or JPEG image type.")

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    filename = uuid4().hex + extension
    destination = DOWNLOADS_DIR / filename
    try:
        with destination.open("xb") as output:
            output.write(content)
    except OSError:
        destination.unlink(missing_ok=True)
        raise

    image_url = f"/downloads/{filename}"
    image_title = _metadata_text(metadata, "ObjectName") or page["title"].removeprefix("File:")
    # Escape metadata as HTML text, not as model-authored Markdown.
    caption = (
        f'<p><strong>{escape(image_title)}</strong><br>'
        f'Attribution: {escape(attribution)}<br>'
        f'License: <a href="{license_url}">{escape(license_name)}</a><br>'
        f'Source: <a href="{escape(source_url, quote=True)}">Wikimedia Commons file page</a><br>'
        'Changes: None (original file).'
    )
    if permission:
        caption += f"<br>License context: {escape(permission)}"
    caption += "</p>"
    return {
        "image_url": image_url,
        "title": image_title,
        "attribution": attribution,
        "license": license_name,
        "license_url": license_url,
        "source_url": source_url,
        "markdown": f'<p><img src="{image_url}" alt="{escape(image_title, quote=True)}"></p>\n{caption}',
    }
