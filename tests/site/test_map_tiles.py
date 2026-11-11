"""The share pages send no referrer (their random path stays private), but OpenStreetMap's tile servers answer a tile
request without one with an "Access blocked" image (seen 2026-11-11). The tile layer sends the site's origin only."""
import re
from importlib.resources import files


def test_the_tile_layer_sends_the_origin_and_the_pages_still_send_no_referrer():
    script = files("tripwright.site").joinpath("share_static/share.js").read_text(encoding="utf-8")
    layer = re.search(r"L\.tileLayer\([^;]*\)", script).group(0)
    assert 'referrerPolicy: "strict-origin"' in layer

    from tripwright.site import templates
    assert templates.BASE.count('<meta name="referrer" content="no-referrer">') == 1
