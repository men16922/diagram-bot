"""Sizes every view shares, cell ids, and the node facts placement depends on (subnet tier, on premises)."""
import hashlib

from ..graph import tag_map
from ..views import SITE_KINDS

ENGINE, VERSION = "diagram-bot-layered", 2
ICON, LINE, MAX_LINES = 52, 15, 4
SIDE_LINES = 3  # labels beside an icon stay within the icon row
BOTTOM_LABEL, SIDE_LABEL = 140, 200
SLOT, SIDE_SLOT = 150, 290
LANE_TOP = 36  # space above an icon row for up to LANES entering routes
ROW = LANE_TOP + ICON + 8 + MAX_LINES * LINE + 8
SUBNET_HEADER, AZ_HEADER, VPC_HEADER = 46, 32, 38


def cell_id(value):
    return "r-" + hashlib.sha256(value.encode()).hexdigest()[:24]


def is_public(subnet):
    """Route table decides when collected; otherwise MapPublicIpOnLaunch or the name/Tier tag."""
    if subnet.get("route_class"):
        return subnet["route_class"] == "public"
    tags = tag_map(subnet["attributes"])
    return bool(subnet["attributes"].get("MapPublicIpOnLaunch")) or "public" in (tags.get("Tier", "") + " " + subnet["name"]).lower()


def tier_rank(subnet):
    """Order subnets the way architecture diagrams read: public, then web, app, data tiers."""
    if is_public(subnet):
        return 0
    tags = tag_map(subnet["attributes"])
    words = (tags.get("Tier", "") + " " + subnet["name"]).lower()
    for rank, names in enumerate((("web", "front"), ("app", "service", "private"), ("data", "db", "database")), 1):
        if any(name in words for name in names):
            return rank
    return 3 if subnet.get("route_class") == "isolated" else 2


def outside_vpc(node):
    """On premises: a target registered with AvailabilityZone=all, a VPN customer gateway or a Direct Connect connection."""
    return node["kind"] in SITE_KINDS or \
        node["kind"] == "registered_target" and node["attributes"].get("Target", {}).get("AvailabilityZone") == "all"
