import base64
from io import BytesIO
import re

from PIL import Image
import pytest

from setup_gui import create_local_qr


@pytest.mark.parametrize("hostname,port,expected", [
    ("sckc", 8080, "http://sckc.local:8080"),
    ("sckc.local", 80, "http://sckc.local"),
    ("sckc.local.", 443, "https://sckc.local"),
])
def test_local_qr_exports_saved_address_and_self_contained_poster(tmp_path, hostname, port, expected):
    url, png, poster = create_local_qr(tmp_path, hostname, port, "예시 <교회> & Church")
    assert url == expected
    assert png.name == "qr_local.png" and poster.name == "qr_local_print.html"
    page = poster.read_text(encoding="utf-8")
    embedded = re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)', page)
    assert embedded is not None
    assert base64.b64decode(embedded.group(1)) == png.read_bytes()
    assert expected in page
    assert "예시 &lt;교회&gt; &amp; Church" in page
    assert "window.print()" in page
    with Image.open(BytesIO(png.read_bytes())) as image:
        assert image.format == "PNG"
        assert image.width == image.height and image.width >= 300


def test_local_qr_files_are_regenerated_after_address_change(tmp_path):
    _, png, _ = create_local_qr(tmp_path, "oldchurch", 8080, "Example Church")
    before = png.read_bytes()
    _, png, poster = create_local_qr(tmp_path, "newchurch", 8080, "Example Church")
    assert png.read_bytes() != before
    assert "http://newchurch.local:8080" in poster.read_text(encoding="utf-8")
    assert "http://oldchurch.local:8080" not in poster.read_text(encoding="utf-8")
