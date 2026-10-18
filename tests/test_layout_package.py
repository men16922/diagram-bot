"""diagram_bot.layout is a package; the names callers import from it (drawio, xml_align, tests) stay there."""
import diagram_bot.layout as layout
from diagram_bot.layout import text


def test_package_reexports_sizes_and_entry_points():
    for name in ("ICON", "SLOT", "SIDE_SLOT", "ROW", "LANE_TOP", "SUBNET_HEADER", "AZ_HEADER", "VPC_HEADER", "LINE", "MAX_LINES",
                 "BOTTOM_LABEL", "SIDE_LABEL", "ENGINE", "VERSION", "Canvas", "layout_view", "cell_id", "fit_label", "icon_variant",
                 "resource_label", "text_width", "edge_entry", "edge_between", "route", "detail", "overview"):
        assert name in layout.__all__ and hasattr(layout, name), name
    assert layout.ROW == layout.LANE_TOP + layout.ICON + 8 + layout.MAX_LINES * layout.LINE + 8


def test_labelers_fall_back_to_the_name():
    node = lambda kind, **attrs: {"kind": kind, "name": "n1", "attributes": attrs, "reference_only": False}
    assert layout.resource_label(node("unknown_kind", Type="x")) == "n1"
    assert layout.resource_label(node("load_balancer")) == "n1"  # no Type collected
    assert layout.resource_label(node("target_group", Port=80)) == "n1"  # no Protocol collected
    assert layout.resource_label(node("target_group", Protocol="HTTP", Port=80, TargetType="ip")) == "n1\nHTTP:80 · ip"
    assert set(text.LABELERS) >= {"ec2", "db_instance", "db_standby", "cache_node", "vpc_endpoint"}
