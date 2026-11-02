#!/usr/bin/env python3
"""Extract an offline KPS source snapshot without an APEX runtime dependency.

Only files supplied explicitly to this command are read. No database or remote
system is written. Repeated source cards and their original ids stay separate.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote_to_bytes

SOURCE_URL = "https://kps-order-test.onrender.com/"
SOURCE_COMMIT = "b9d4f523c18981032151f5ab5dad57bd23f946c5"
OPTIONS_COMMIT = "22c66093bfbcca4b7539f4c31aa9099b8ee9d968"
OPTIONS_PATH = "app/v1038_product_recipe_options_closure.py"
BRANCH_PATH = "app/v1011_canonical_branch_selectors.py"

# Exact published-source definitions, not invented choices. These remain a
# source-code snapshot until the live catalog is successfully verified.
FLAVOURS = [
    ("BLACKBERRY", "توت أسود"), ("BLUE_LAGOON", "توت أزرق"),
    ("PASSION_FRUIT", "باشن فروت"), ("POMEGRANATE", "رمان"),
    ("RASPBERRY", "توت أحمر"), ("STRAWBERRY", "فراولة"),
]
COLOURS = {
    "kps-30": [("PNK", "وردي"), ("BLK", "أسود"), ("BLU", "أزرق"), ("MAU", "موف")],
    "kps-31": [("BLK", "أسود"), ("BLU", "أزرق"), ("MAU", "موف"), ("OFW", "أوف وايت")],
}
THERMAL_CODES = {"kps-30": "KPS-THERMAL-21", "kps-31": "KPS-THERMAL-18"}
BRANCHES = {
    1: "حفر الباطن - المروج", 2: "حفر الباطن - الخالدية",
    3: "حفر الباطن - النايفية", 4: "حفر الباطن - العزيزية",
    5: "الذيبية", 6: "النعيرية - العزيزية", 7: "الدمام - أحد",
    8: "الأرطاوية", 9: "حفر الباطن - المروج",
    10: "القيصومة - الخالدية", 11: "الخفجي - العزيزية",
    12: "حفر الباطن - الباطن", 13: "الزلفي - الفاخرية",
    14: "الدمام - الجامعيين", 15: "المجمعة", 16: "عنيزة",
    17: "حائل", 18: "شعبة نصاب", 19: "رفحاء", 20: "قبه",
}
DELIVERY_CITIES = ["حفر الباطن", "الذيبية", "القيصومة", "الخفجي", "رفحاء", "قبه"]


class MenuParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.products = []
        self.logos = []
        self.section_stack = []
        self.current = None
        self.heading = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            self.section_stack.append({"id": attrs.get("id", ""), "title": ""})
        if tag == "h2" and self.section_stack:
            self.heading = self.section_stack[-1]
        if tag == "article" and "product" in attrs.get("class", "").split():
            self.current = {"attributes": attrs, "section": dict(self.section_stack[-1]) if self.section_stack else {}, "image": ""}
            self.products.append(self.current)
        if tag == "img":
            if self.current:
                self.current["image"] = attrs.get("src", "")
            if "logo" in (attrs.get("alt", "") + attrs.get("class", "")).lower():
                self.logos.append(attrs.get("src", ""))

    def handle_endtag(self, tag):
        if tag == "h2":
            self.heading = None
        if tag == "article":
            self.current = None
        if tag == "section" and self.section_stack:
            self.section_stack.pop()

    def handle_data(self, data):
        if self.heading is not None:
            self.heading["title"] += data


def embedded_image(uri: str, root: Path, label: str) -> str:
    if not uri.startswith("data:"):
        # An external resource cannot silently become an operational dependency.
        return ""
    header, data = uri.split(",", 1)
    mime = header[5:].split(";", 1)[0]
    raw = base64.b64decode(data) if ";base64" in header else unquote_to_bytes(data)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}.get(mime)
    if not suffix:
        raise ValueError(f"Unsupported source image type: {mime}")
    # SHA identifies duplicate content without merging product records.
    basename = f"{label}-{hashlib.sha256(raw).hexdigest()[:12]}{suffix}"
    target = root / "public" / "assets" / basename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return f"/assets/{basename}"


def source_group(code: str, name: str, values: list[tuple[str, str]]) -> dict:
    return {
        "id": code, "source_id": code, "source_db_id": None,
        "code": code, "name": name, "type": "color" if code == "COLOR" else "flavour",
        "unit_scope": "per_unit", "required": True, "per_unit": True,
        "min_select": 1, "max_select": 1,
        "values": [{"id": c, "source_id": c, "source_db_id": None, "code": c, "name": n, "price_delta_halalas": 0} for c, n in values],
    }


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract(source: Path, root: Path, runtime_json: Path | None = None):
    raw = source.read_bytes()
    parsed = MenuParser()
    parsed.feed(raw.decode("utf-8-sig"))
    seen = set()
    products = []
    for card in parsed.products:
        attrs = card["attributes"]
        source_id = attrs.get("data-product-id")
        if not source_id or source_id in seen:
            raise ValueError(f"Missing or duplicate source id: {source_id}")
        seen.add(source_id)
        name = attrs["data-product-name"]
        category = re.sub(r"\s+", " ", card["section"].get("title", "")).strip()
        groups = json.loads(attrs.get("data-option-groups", "[]"))
        source_code = attrs.get("data-apex-product-code", "")
        options_origin = "live_embedded_html" if groups else "not_embedded"
        if name == "موهيتو" and not groups:
            groups = [source_group("FLAVOUR", "نكهة الموهيتو", FLAVOURS)]
            options_origin = f"source_code:{OPTIONS_COMMIT}:{OPTIONS_PATH}"
        if source_id in COLOURS and not groups:
            groups = [source_group("COLOR", "لون الكوب الحراري", COLOURS[source_id])]
            source_code = THERMAL_CODES[source_id]
            options_origin = f"source_code:{OPTIONS_COMMIT}:{OPTIONS_PATH}"
        cities = ["حفر الباطن"] if source_id in {"kps-33", "kps-34"} else []
        price = int(Decimal(attrs["data-product-price"]) * 100)
        image_url = embedded_image(card["image"], root, source_id)
        settings = {
            "source_id": source_id, "source_code": source_code,
            "image_url": image_url, "option_groups": groups, "variants": [],
            "available_cities": cities, "enabled": True,
            "source_verified": False,
            "source_runtime_verified": False,
            "source_price_status": "embedded_live_html_not_runtime_verified",
            "source_options_status": options_origin,
            "source_section_id": card["section"].get("id", ""),
            "source_available_governorates": attrs.get("data-available-governorates", ""),
            "source_url": SOURCE_URL,
        }
        products.append({
            "id": source_id, "source_id": source_id, "brand_id": "kps",
            "name": name, "category": category, "price_halalas": price,
            "image_url": image_url, "option_groups": groups, "variants": [],
            "available_cities": cities, "enabled": True, "settings": settings,
            "source_verified": False,
        })
    metadata = {
        "schema_version": 1, "brand_id": "kps", "source_url": SOURCE_URL,
        "source_repository": "KPS-COFFEE/KPS-ORDER", "source_commit": SOURCE_COMMIT,
        "source_html_sha256": hashlib.sha256(raw).hexdigest(),
        "runtime_verified": False,
        "note": "Exact deployed HTML snapshot. Runtime APEX catalog could not be read; current DB ids, prices, offers and options require verification before live ordering.",
        "logos": [embedded_image(uri, root, "kps-logo") for uri in parsed.logos],
        "delivery_cities": DELIVERY_CITIES,
    }
    if runtime_json:
        payload = json.loads(runtime_json.read_text(encoding="utf-8"))
        if not payload.get("ok") or not isinstance(payload.get("items"), list):
            raise ValueError("Runtime catalog must be a successful public-catalog response")
        # Do not blindly map runtime ids by array order or ambiguous names.
        raise ValueError("Runtime catalog supplied: explicit id/code reconciliation is required before replacing the snapshot")
    write_json(root / "data" / "catalog.json", {"metadata": metadata, "products": products})
    branches = []
    for no, name in BRANCHES.items():
        city = name.split(" - ", 1)[0]
        branches.append({
            "id": f"kps-branch-{no}", "branch_no": no,
            "source_id": f"apex-canonical-branch-no:{no}", "source_db_id": None,
            "brand_id": "kps", "name": name, "city": city,
            "descriptor": "كشك / درايف ثرو" if no == 9 else "",
            "coordinates": None,
            "settings": {
                "pickup_enabled": False, "car_enabled": False, "delivery_enabled": False,
                "delivery_eligible": city in DELIVERY_CITIES,
                "location": None, "opening_hours": None, "coverage": None,
                "delivery_fee_halalas": None, "minimum_order_halalas": None,
                "operational_data_verified": False,
                "source_repository": "KPS-COFFEE/KPS-APEX-OS",
                "source_commit": OPTIONS_COMMIT, "source_path": BRANCH_PATH,
                "source_mapping_type": "explicit_official_branch_number_dictionary",
            },
        })
    write_json(root / "data" / "branches.json", {
        "metadata": {
            "schema_version": 1, "brand_id": "kps", "delivery_cities": DELIVERY_CITIES,
            "identity_verified_from": f"{OPTIONS_COMMIT}:{BRANCH_PATH}",
            "note": "Original explicit operational numbers preserved. Runtime DB ids and operating configuration have not been verified; every service remains disabled until configured.",
        }, "branches": branches,
    })
    return {"products": len(products), "branches": len(branches), "logos": metadata["logos"], "html_sha256": metadata["source_html_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Verified original deployed HTML snapshot")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-json", type=Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.source, args.root, args.runtime_json), ensure_ascii=False))


if __name__ == "__main__":
    main()
