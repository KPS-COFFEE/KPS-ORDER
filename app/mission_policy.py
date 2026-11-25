"""Physical fleet assignment policy, separated from the delivery dispatch role."""

import json
import os
import re
import unicodedata
import logging
from functools import lru_cache
from . import db

SUPPLY = ("local", "riyadh", "east", "selected")
KINDS = {
    "purchase": "مشتريات",
    "visit": "زيارة فرع",
    "repair": "إصلاح عطل بفرع",
    "workshop": "مراجعة الورشة",
    "local": "توريد داخلي",
    "riyadh": "توريد خط الرياض",
    "east": "توريد خط الشرقية",
    "selected": "توريد فروع محددة",
    "bus": "توصيل موظفين",
    "supervision": "يوم مشرف خارجي",
    "assignment": "تكليف المدير العام",
    "custody": "استلام أو تسليم عهدة",
    "transfer": "نقل مستلزمات بين فرعين",
    "support": "مساندة فرع طارئة",
    "service": "صيانة سيارة أو فحص دوري",
    "other": "أخرى",
}
LEADS = {"owner", "director", "fleet_manager"}
COLD_DRIVERS = {"supplier_a", "supplier_b", "supplier_bus"}
EXTERNAL = {"supervisor_a", "supervisor_b"}


def allowed(profile, category, home, kind):
    if kind not in KINDS:
        return False
    if kind in ("supervision", "assignment"):
        return profile in EXTERNAL and (category == "relief" or home == profile)
    if kind in SUPPLY:
        return (
            category == "cold_van"
            and profile in COLD_DRIVERS
            or category == "cold_truck"
            and profile == "supplier_a"
        )
    if kind == "bus":
        return category == "staff_bus" and profile == "supplier_bus"
    if category == "shared_camry":
        return profile in ("director", "fleet_manager")
    if category in ("cold_van", "cold_truck", "staff_bus", "reserve_cold"):
        return False
    return profile not in COLD_DRIVERS and (home == profile or category == "relief")


@lru_cache(maxsize=1)
def config():
    return json.loads(os.environ.get("FLEET_MISSION_ROSTER", "{}"))


def plate_identity(value):
    raw = unicodedata.normalize("NFKC", str(value)).upper()
    raw = raw.translate(
        str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹أإآٱيیک", "01234567890123456789ااااىىك")
    )
    raw = re.sub(r"[\sـ/\-\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", raw)
    m = re.fullmatch(r"(?:([0-9]{1,4})([A-Zا-ى]{3})|([A-Zا-ى]{3})([0-9]{1,4}))", raw)
    if not m:
        return None
    letters = m[2] or m[3]
    if letters.isascii():
        pairs = dict(zip("ABJDRSXTEGKLZNHUV", "ابحدرسصطعقكلمنهوى"))
        if any(ch not in pairs for ch in letters):
            return None
        letters = "".join(pairs[ch] for ch in reversed(letters))
    return m[1] or m[4], letters


def assign_permanent_keys(c, brand):
    c.execute(
        "UPDATE fleet_mission_cars v SET key_holder=s.user_id FROM fleet_mission_staff s WHERE v.brand_id=%s AND s.brand_id=v.brand_id AND s.profile=v.home_profile AND v.category='permanent' AND v.key_holder IS NULL AND NOT EXISTS(SELECT 1 FROM fleet_missions m WHERE m.vehicle_id=v.vehicle_id)",
        (brand,),
    )


def sync(c):
    for brand, cfg in config().items():
        unresolved = []
        scope = "brand_id=%s"
        params = [brand]
        if c.execute(
            "SELECT to_regclass('staff_brand_memberships') available"
        ).fetchone()["available"]:
            scope += " OR EXISTS(SELECT 1 FROM staff_brand_memberships bm WHERE bm.user_id=users.id AND bm.brand_id=%s AND bm.active)"
            params.append(brand)
        for entry in cfg["people"]:
            if "id" in entry:
                matches = c.execute(
                    "SELECT id FROM users WHERE brand_id=%s AND id=%s AND active AND role<>'customer'",
                    (brand, entry["id"]),
                ).fetchall()
            else:
                matches = c.execute(
                    "SELECT id FROM users WHERE ("
                    + scope
                    + ") AND normalized_identity_phone(phone)=normalized_identity_phone(%s) AND active AND role<>'customer'",
                    (*params, entry["phone"]),
                ).fetchall()
            if len(matches) == 1:
                c.execute(
                    "INSERT INTO fleet_mission_staff VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (brand, matches[0]["id"], entry["profile"]),
                )
            else:
                unresolved.append(entry["profile"])
        existing = c.execute(
            "SELECT id,plate FROM fleet_vehicles WHERE brand_id=%s", (brand,)
        ).fetchall()
        for car in cfg["cars"]:
            ident = plate_identity(car["plate"])
            if not ident:
                continue
            matches = [v for v in existing if plate_identity(v["plate"]) == ident]
            if len(matches) > 1:
                continue
            if not matches:
                pk = re.sub(r"\s+", "", car["plate"].upper())
                v = c.execute(
                    "INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details) VALUES('veh_'||md5(%s),%s,%s,%s,%s) ON CONFLICT(brand_id,plate_key) DO UPDATE SET plate_key=EXCLUDED.plate_key RETURNING id",
                    (
                        brand + ":" + pk,
                        brand,
                        pk,
                        car["plate"],
                        db.json({"type": car["model"], "owned_by_kps": True}),
                    ),
                ).fetchone()
            else:
                v = matches[0]
            c.execute(
                "INSERT INTO fleet_mission_cars(vehicle_id,brand_id,category,home_profile,art) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (v["id"], brand, car["category"], car.get("home"), car.get("art")),
            )
        assign_permanent_keys(c, brand)
        count = c.execute(
            "SELECT count(*) n FROM fleet_mission_cars WHERE brand_id=%s", (brand,)
        ).fetchone()["n"]
        logging.getLogger("uvicorn.error").info(
            "fleet_roster_sync cars=%s unresolved_profiles=%s",
            count,
            ",".join(unresolved) or "none",
        )


def profile(c, u):
    if (
        u.get("brand_id") not in config()
        or u.get("_morning_manager_context")
        or u.get("role") == "customer"
    ):
        return None
    if u["role"] == "owner" and not u.get("home_brand_id"):
        return "owner"
    r = c.execute(
        "SELECT profile FROM fleet_mission_staff WHERE brand_id=%s AND user_id=%s",
        (u["brand_id"], u["id"]),
    ).fetchone()
    return r["profile"] if r else None

