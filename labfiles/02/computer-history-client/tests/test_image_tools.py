import json
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import image_tools


class ImageDownloadTests(unittest.TestCase):
    def metadata(self, license_name="CC BY-SA 4.0"):
        return json.dumps({"query": {"pages": {"1": {
            "title": "File:Computer.jpg",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/wikipedia/commons/a/a1/Computer.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Computer.jpg",
                "mime": "image/jpeg", "size": 10,
                "extmetadata": {
                    "LicenseShortName": {"value": license_name},
                    "LicenseUrl": {"value": (
                        "https://creativecommons.org/licenses/by/3.0/"
                        if license_name == "CC BY 3.0" else
                        "https://creativecommons.org/licenses/by-sa/4.0/"
                    )},
                    "Artist": {"value": '<a href="https://example.com">A. Photographer</a>'},
                    "ObjectName": {"value": "Historic computer"},
                    "Permission": {"value": "Public domain in the United States."},
                },
            }],
        }}}}).encode()

    def test_download_and_attribution_for_supported_licenses(self):
        for license_name in ("Public domain", "CC0", "CC BY 3.0", "CC BY-SA 4.0"):
            with self.subTest(license=license_name), tempfile.TemporaryDirectory() as folder:
                with patch.object(image_tools, "DOWNLOADS_DIR", Path(folder)), patch.object(
                    image_tools, "_read_url",
                    side_effect=[(self.metadata(license_name), "application/json"),
                                 (b"\xff\xd8\xffexample", "image/jpeg")],
                ):
                    result = image_tools.download_commons_image(
                        "https://commons.wikimedia.org/wiki/File:Computer.jpg"
                    )
                self.assertEqual(result["license"], license_name)
                self.assertEqual(result["attribution"], "A. Photographer")
                self.assertIn('src="/downloads/', result["markdown"])
                self.assertIn("Public domain in the United States.", result["markdown"])
                self.assertIn("Source:", result["markdown"])
                self.assertEqual(list(Path(folder).iterdir())[0].read_bytes(), b"\xff\xd8\xffexample")

    def test_rejects_unverified_license_and_invalid_download(self):
        for metadata, payload in (
            (self.metadata("All rights reserved"), b"\xff\xd8\xffexample"),
            (self.metadata(), b"<html>not an image</html>"),
            (b'{"query":{"pages":{"-1":{"missing":""}}}}', b""),
        ):
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as folder:
                with patch.object(image_tools, "DOWNLOADS_DIR", Path(folder)), patch.object(
                    image_tools, "_read_url",
                    side_effect=[(metadata, "application/json"), (payload, "image/jpeg")],
                ):
                    with self.assertRaises(ValueError):
                        image_tools.download_commons_image(
                            "https://commons.wikimedia.org/wiki/File:Computer.jpg"
                        )
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_preserves_license_url_without_trailing_slash_and_ported_license(self):
        for name, url in (
            ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0"),
            ("CC BY-SA 2.0 fr", "https://creativecommons.org/licenses/by-sa/2.0/fr/deed.en"),
        ):
            with self.subTest(license=name), tempfile.TemporaryDirectory() as folder:
                data = json.loads(self.metadata(name))
                data["query"]["pages"]["1"]["imageinfo"][0]["extmetadata"]["LicenseUrl"]["value"] = url
                with patch.object(image_tools, "DOWNLOADS_DIR", Path(folder)), patch.object(
                    image_tools, "_read_url",
                    side_effect=[(json.dumps(data).encode(), "application/json"),
                                 (b"\xff\xd8\xffexample", "image/jpeg")],
                ):
                    result = image_tools.download_commons_image(
                        "https://commons.wikimedia.org/wiki/File:Computer.jpg"
                    )
                self.assertEqual(result["license_url"], url)

    def test_rejects_external_urls_before_network_access(self):
        with patch.object(image_tools, "build_opener") as opener:
            for url in ("http://commons.wikimedia.org/wiki/File:Computer.jpg",
                        "https://localhost/wiki/File:Computer.jpg",
                        "https://commons.wikimedia.org.evil.example/wiki/File:Computer.jpg"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    image_tools.download_commons_image(url)
            opener.assert_not_called()

    def test_enforces_download_size_and_blocks_redirects(self):
        with patch.object(image_tools, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b"12345"
            with self.assertRaises(ValueError):
                image_tools._read_url("https://upload.wikimedia.org/image.jpg",
                                      "upload.wikimedia.org", 4)
        with self.assertRaises(ValueError):
            image_tools._NoRedirects().redirect_request(None, None, 302, "", {},
                                                        "http://localhost/private")

    def test_user_agent_identifies_application_and_project_contact(self):
        with patch.object(image_tools, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b"image"
            image_tools._read_url("https://upload.wikimedia.org/image.jpg",
                                  "upload.wikimedia.org", 10)
        request = opener.return_value.open.call_args.args[0]
        self.assertIn("ComputingHistoryLearningLab/", request.get_header("User-agent"))
        self.assertIn("https://github.com/GraemeMalcolm/mslearn-foundry-toolkit",
                      request.get_header("User-agent"))

    def test_rate_limit_obeys_retry_after_without_more_network_requests(self):
        headers = Message()
        headers["Retry-After"] = "120"
        error = HTTPError("https://commons.wikimedia.org/w/api.php", 429, "Too many requests",
                          headers, None)
        with patch.object(image_tools, "build_opener") as opener, \
                patch.object(image_tools, "_retry_at", 0), \
                patch.object(image_tools.time, "monotonic", side_effect=[100, 100, 110, 221]):
            opener.return_value.open.side_effect = error
            with self.assertRaises(image_tools.WikimediaRateLimitError) as first:
                image_tools._read_url("https://commons.wikimedia.org/w/api.php",
                                      "commons.wikimedia.org", 10)
            self.assertEqual(first.exception.retry_after_seconds, 120)
            with self.assertRaises(image_tools.WikimediaRateLimitError) as second:
                image_tools._read_url("https://upload.wikimedia.org/image.jpg",
                                      "upload.wikimedia.org", 10)
            self.assertEqual(second.exception.retry_after_seconds, 110)
            opener.return_value.open.assert_called_once()
            opener.return_value.open.side_effect = None
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b"image"
            image_tools._read_url("https://upload.wikimedia.org/image.jpg",
                                  "upload.wikimedia.org", 10)
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_retry_after_accepts_http_date_and_defaults_when_missing(self):
        with patch.object(image_tools.time, "time", return_value=0):
            self.assertEqual(image_tools._retry_delay("Thu, 01 Jan 1970 00:02:00 GMT"), 120)
        self.assertEqual(image_tools._retry_delay(None), 60)


class ImageDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("agent_client.AgentClient") as client:
            import app
        cls.module = app
        cls.agent = client.return_value
        cls.client = app.app.test_client()

    def test_chat_renders_download_but_not_external_image_or_script(self):
        filename = "a" * 32 + ".jpg"
        self.agent.send_message.return_value = (
            f'![Computer](/downloads/{filename})\n\nAttribution: A. Photographer\n\n'
            '![External](https://example.com/image.jpg)<script>alert(1)</script>'
        )
        response = self.client.post("/chat", json={"message": "Show me a computer"})
        self.assertEqual(response.status_code, 200)
        html = response.json["response_html"]
        self.assertIn(f'src="/downloads/{filename}"', html)
        self.assertIn("Attribution: A. Photographer", html)
        self.assertNotIn('src="https://example.com', html)
        self.assertNotIn("<script", html)

    def test_serves_only_generated_images(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = "b" * 32 + ".png"
            (Path(folder) / filename).write_bytes(b"\x89PNG\r\n\x1a\nexample")
            (Path(folder) / "readme.md").write_text("not an image")
            with patch.object(self.module, "DOWNLOADS_DIR", Path(folder)):
                response = self.client.get(f"/downloads/{filename}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content_type, "image/png")
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                response.close()
                self.assertEqual(self.client.get("/downloads/readme.md").status_code, 404)
                self.assertEqual(self.client.get("/downloads/" + "c" * 32 + ".jpg").status_code, 404)
                self.assertEqual(self.client.get("/downloads/../requirements.txt").status_code, 404)


if __name__ == "__main__":
    unittest.main()
