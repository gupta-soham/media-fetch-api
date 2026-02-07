"""Tests for cookie manager robustness."""

import pytest

from app.core.cookies import COOKIE_SERVICES, CookieManager


@pytest.fixture()
def tmp_cookie_dir(tmp_path):
    """Provide a temporary cookie directory."""
    return tmp_path


class TestCookieManagerInit:
    def test_creates_dir_if_missing(self, tmp_path):
        new_dir = tmp_path / "nonexistent"
        CookieManager(cookie_dir=new_dir)
        assert new_dir.exists()

    def test_empty_dir_loads_nothing(self, tmp_cookie_dir):
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        assert not cm.has_cookies("youtube")
        assert cm.get_cookies("youtube") == {}
        assert cm.get_cookie("youtube", "SID") is None
        assert cm.get_cookie_header("youtube") == ""


class TestNetscapeFormat:
    def test_loads_valid_netscape_file(self, tmp_cookie_dir):
        cookie_file = tmp_cookie_dir / "youtube.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSID\tabc123\n"
            ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tHSID\txyz789\n"
        )
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        assert cm.has_cookies("youtube")
        assert cm.get_cookie("youtube", "SID") == "abc123"
        assert cm.get_cookie("youtube", "HSID") == "xyz789"

    def test_cookie_header_format(self, tmp_cookie_dir):
        cookie_file = tmp_cookie_dir / "instagram.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tcsrftoken\tTOKEN1\n"
            ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\tSESS2\n"
        )
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        header = cm.get_cookie_header("instagram")
        assert "csrftoken=TOKEN1" in header
        assert "sessionid=SESS2" in header
        assert "; " in header


class TestCorruptFiles:
    def test_handles_garbage_file(self, tmp_cookie_dir):
        cookie_file = tmp_cookie_dir / "youtube.txt"
        cookie_file.write_text("this is not a cookie file at all\n!!!\n")
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        # Should not crash, just fail to load
        assert not cm.has_cookies("youtube")

    def test_handles_partial_netscape(self, tmp_cookie_dir):
        cookie_file = tmp_cookie_dir / "twitter.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".twitter.com\tTRUE\t/\tTRUE\t9999999999\tct0\tTOKEN\n"
            "bad line with no tabs\n"
            ".twitter.com\tTRUE\n"  # too few fields
        )
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        # Should still load the valid line
        assert cm.has_cookies("twitter")
        assert cm.get_cookie("twitter", "ct0") == "TOKEN"

    def test_handles_key_value_format(self, tmp_cookie_dir):
        cookie_file = tmp_cookie_dir / "reddit.txt"
        cookie_file.write_text("token_v2=BEARER_TOKEN\neddit_session=SESSION\n")
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        assert cm.has_cookies("reddit")
        assert cm.get_cookie("reddit", "token_v2") == "BEARER_TOKEN"

    def test_handles_empty_file(self, tmp_cookie_dir):
        cookie_file = tmp_cookie_dir / "youtube.txt"
        cookie_file.write_text("")
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        assert not cm.has_cookies("youtube")

    def test_handles_binary_garbage(self, tmp_cookie_dir):
        cookie_file = tmp_cookie_dir / "youtube.txt"
        cookie_file.write_bytes(b"\x00\xff\xfe\x80binary garbage")
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        assert not cm.has_cookies("youtube")


class TestExpiredCookies:
    def test_expired_cookies_are_still_loaded(self, tmp_cookie_dir):
        cookie_file = tmp_cookie_dir / "youtube.txt"
        # expiry = 1 (Jan 1 1970 — very expired)
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tOLD_COOKIE\tval\n"
        )
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        # Expired cookies should still be loaded (with a logged warning)
        assert cm.has_cookies("youtube")
        assert cm.get_cookie("youtube", "OLD_COOKIE") == "val"


class TestRuntimeOps:
    def test_set_cookies(self, tmp_cookie_dir):
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        cm.set_cookies("youtube", {"SID": "new_sid"})
        assert cm.get_cookie("youtube", "SID") == "new_sid"

    def test_clear_cookies(self, tmp_cookie_dir):
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        cm.set_cookies("youtube", {"SID": "abc"})
        assert cm.has_cookies("youtube")
        cm.clear_cookies("youtube")
        assert not cm.has_cookies("youtube")

    def test_reload_does_not_crash(self, tmp_cookie_dir):
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        cm.reload()  # full reload on empty dir
        cm.reload("youtube")  # single reload, file missing
        # Should not raise

    def test_get_load_errors(self, tmp_cookie_dir):
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        errors = cm.get_load_errors()
        assert isinstance(errors, dict)


class TestIgnoresUnknownServices:
    def test_unknown_service_file_skipped(self, tmp_cookie_dir):
        cookie_file = tmp_cookie_dir / "unknown_site.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n.unknown.com\tTRUE\t/\tTRUE\t9999999999\tA\tB\n"
        )
        cm = CookieManager(cookie_dir=tmp_cookie_dir)
        assert not cm.has_cookies("unknown_site")

    def test_known_services_list(self):
        # Verify the known service list matches what extractors expect
        expected = {
            "youtube",
            "instagram",
            "instagram_bearer",
            "twitter",
            "reddit",
            "vimeo",
            "facebook",
            "tiktok",
            "pinterest",
            "google_drive",
        }
        assert expected == COOKIE_SERVICES
