"""Tests for utility helpers."""

import pytest

from app.utils.helpers import (
    clean_html,
    float_or_none,
    format_date,
    int_or_none,
    parse_m3u8_attributes,
    parse_resolution,
    sanitize_filename,
    sort_formats,
    str_or_none,
    traverse_obj,
    url_or_none,
)


class TestTraverseObj:
    def test_simple_key(self):
        assert traverse_obj({"a": 1}, "a") == 1

    def test_nested_tuple_path(self):
        data = {"a": {"b": {"c": 42}}}
        assert traverse_obj(data, ("a", "b", "c")) == 42

    def test_missing_key_returns_default(self):
        assert traverse_obj({"a": 1}, "b", default="nope") == "nope"

    def test_list_index(self):
        data = {"items": [10, 20, 30]}
        assert traverse_obj(data, ("items", 1)) == 20

    def test_none_input(self):
        assert traverse_obj(None, "a", default="d") == "d"

    def test_multiple_paths_first_wins(self):
        data = {"x": None, "y": 99}
        assert traverse_obj(data, ("x",), ("y",)) == 99


class TestIntOrNone:
    @pytest.mark.parametrize(
        ("val", "expected"),
        [
            (42, 42),
            ("100", 100),
            ("3.9", None),  # int() cannot parse decimal strings
            (None, None),
            ("abc", None),
            ("", None),
        ],
    )
    def test_values(self, val, expected):
        assert int_or_none(val) == expected


class TestFloatOrNone:
    @pytest.mark.parametrize(
        ("val", "expected"),
        [
            (3.14, 3.14),
            ("2.5", 2.5),
            (None, None),
            ("nope", None),
        ],
    )
    def test_values(self, val, expected):
        assert float_or_none(val) == expected


class TestStrOrNone:
    def test_string(self):
        assert str_or_none("hello") == "hello"

    def test_empty_string(self):
        assert str_or_none("") is None

    def test_whitespace(self):
        assert str_or_none("   ") is None

    def test_none(self):
        assert str_or_none(None) is None

    def test_int(self):
        assert str_or_none(42) == "42"


class TestUrlOrNone:
    def test_valid_http(self):
        assert url_or_none("https://example.com") == "https://example.com"

    def test_protocol_relative(self):
        assert url_or_none("//cdn.example.com/v.mp4") == "https://cdn.example.com/v.mp4"

    def test_none(self):
        assert url_or_none(None) is None

    def test_empty(self):
        assert url_or_none("") is None

    def test_no_scheme(self):
        assert url_or_none("example.com/video") is None


class TestCleanHtml:
    def test_strips_tags(self):
        assert clean_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_decodes_entities(self):
        assert clean_html("&amp; &lt; &gt;") == "& < >"


class TestParseResolution:
    def test_1080p(self):
        w, h = parse_resolution("1080p")
        assert h == 1080
        assert w == 1920

    def test_720p(self):
        _w, h = parse_resolution("720p")
        assert h == 720

    def test_4k(self):
        _w, h = parse_resolution("4K")
        assert h == 2160

    def test_empty(self):
        assert parse_resolution("") == (None, None)


class TestSanitizeFilename:
    def test_removes_bad_chars(self):
        assert "/" not in sanitize_filename('file/name:with"bad<chars>')

    def test_empty_fallback(self):
        assert sanitize_filename("...") == "download"


class TestSortFormats:
    def test_sorts_by_height(self):
        fmts = [
            {"height": 360, "format_type": "combined"},
            {"height": 1080, "format_type": "combined"},
            {"height": 720, "format_type": "combined"},
        ]
        sorted_fmts = sort_formats(fmts)
        assert sorted_fmts[0]["height"] == 360
        assert sorted_fmts[-1]["height"] == 1080


class TestFormatDate:
    def test_iso(self):
        assert format_date("2024-01-15T12:00:00Z") == "2024-01-15"

    def test_none(self):
        assert format_date(None) is None


class TestParseM3U8Attributes:
    def test_basic(self):
        attrs = parse_m3u8_attributes(
            'BANDWIDTH=2000000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"'
        )
        assert attrs["BANDWIDTH"] == "2000000"
        assert attrs["RESOLUTION"] == "1280x720"
        assert attrs["CODECS"] == "avc1.4d401f,mp4a.40.2"
