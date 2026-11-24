"""Sample content for Packages: a household's parcels, all invented.

Creates twelve packages under the Practice tracking service (which looks nothing up and
sends nothing): three delivered, three on their way, two out for delivery, one ready to
collect, one with a problem, one waiting for the carrier and one the carrier has not found.
Each status is set by hand, as the owner can on the page, so that Practice does not move
the packages along by itself when its scheduled check runs, and the screen stays as seeded.
Three carriers are saved with their names. Tracking numbers are made up.

Not seeded: dates in the past. A package is stamped with the moment it was added, and the
event line says "You marked it ...", because the app offers no way to set either. Cards for
delivered packages are switched off (``tell_when``) so the sample puts nothing on the Bench.
"""

from __future__ import annotations

SETTINGS: dict[str, object] = {
    "tracker": "practice",
    "tell_when": "off",
}

CARRIERS = {
    "swift": "Swift Parcel",
    "north": "Northern Post",
    "harbor": "Harbor Express",
}

# label, tracking number, carrier code, status
PACKAGES = (
    ("Camper van solar panel", "ZX900100001QQ", "swift", "out_for_delivery"),
    ("Garden hose", "ZX900100002QQ", "north", "out_for_delivery"),
    ("Winter boots", "ZX900100003QQ", "harbor", "on_its_way"),
    ("Seed packets", "ZX900100004QQ", "north", "on_its_way"),
    ("Desk lamp", "ZX900100005QQ", "swift", "on_its_way"),
    ("Replacement water filter", "ZX900100006QQ", "harbor", "pickup"),
    ("Birthday gift", "ZX900100007QQ", "swift", "problem"),
    ("Bike light set", "ZX900100008QQ", "north", "waiting"),
    ("Printer ink", "ZX900100009QQ", "", "not_found"),
    ("Kitchen scale", "ZX900100010QQ", "harbor", "delivered"),
    ("Yoga mat", "ZX900100011QQ", "north", "delivered"),
    ("Phone case", "ZX900100012QQ", "swift", "delivered"),
)


def seed(call, today: str, now_ms: int) -> None:
    """Add the sample parcels by the calls the Packages page makes."""
    for label, number, carrier, status in reversed(PACKAGES):  # the first is newest
        args = {"label": label, "number": number}
        if carrier:
            args.update(carrier=carrier, carrier_name=CARRIERS[carrier])
        made = call("package.add", **args)
        call("package.mark", id=made["id"], status=status)
