"""UAE Mattress Catalog — agent-readable MCP server (read-only, no auth).

Pilot data: Askona Gulf (askona.ae) snapshot. Streamable HTTP at /mcp.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from typing_extensions import Annotated

DATA_PATH = Path(os.environ.get(
    "CATALOG_PATH",
    Path(__file__).resolve().parent.parent / "data" / "askona_mattresses_v0.json",
))
UAE_TZ = timezone(timedelta(hours=4))

# ----------------------------------------------------------------------------- data
CAT: dict[str, Any] = json.loads(DATA_PATH.read_text(encoding="utf-8"))
SELLERS = {s["seller_id"]: s for s in CAT["sellers"]}
PRODUCTS = {p["product_id"]: p for p in CAT["products"]}
VARIANTS = {v["variant_id"]: (p, v) for p in CAT["products"] for v in p["variants"]}
OFFERS = {o["offer_id"]: o for o in CAT["offers"]}
OFFERS_BY_PRODUCT: dict[str, list[dict]] = {}
for _o in CAT["offers"]:
    OFFERS_BY_PRODUCT.setdefault(_o["product_id"], []).append(_o)

EMIRATES = ["dubai", "abu_dhabi", "sharjah", "ajman", "umm_al_quwain", "ras_al_khaimah", "fujairah"]
EMIRATE_ALIASES = {
    "dubai": "dubai", "dxb": "dubai", "дубай": "dubai",
    "abu dhabi": "abu_dhabi", "abudhabi": "abu_dhabi", "ad": "abu_dhabi", "абу-даби": "abu_dhabi", "абу даби": "abu_dhabi",
    "sharjah": "sharjah", "шарджа": "sharjah", "ajman": "ajman", "аджман": "ajman",
    "umm al quwain": "umm_al_quwain", "uaq": "umm_al_quwain",
    "ras al khaimah": "ras_al_khaimah", "rak": "ras_al_khaimah", "fujairah": "fujairah", "фуджейра": "fujairah",
}
PAYMENT_ALIASES = {
    "tabby": {"tabby"}, "tamara": {"tamara"},
    "cod": {"cash_on_delivery", "card_on_delivery"}, "cash": {"cash_on_delivery"},
    "cash_on_delivery": {"cash_on_delivery"}, "card_on_delivery": {"card_on_delivery"},
    "card": {"card_online", "card_on_delivery"}, "card_online": {"card_online"},
    "bnpl": {"tabby", "tamara"}, "installments": {"tabby", "tamara"},
}
SIZE_CLASSES = ["crib", "kids_other", "single", "small_double", "double", "queen", "king", "super_king"]
FEATURE_KEYS = ["cooling", "orthopedic", "motion_isolation", "edge_support", "zoned_support",
                "memory_foam", "hypoallergenic_antibacterial", "removable_cover", "flippable"]
FEATURE_ALIASES = {
    "hypoallergenic": "hypoallergenic_antibacterial", "antibacterial": "hypoallergenic_antibacterial",
    "zoned": "zoned_support", "edge": "edge_support", "two_sided": "flippable", "dual_sided": "flippable",
    "memory foam": "memory_foam", "ortho": "orthopedic", "orthopaedic": "orthopedic",
}


def today_uae():
    return datetime.now(UAE_TZ).date()


def norm_key(s: str | None) -> str:
    return re.sub(r"[\s\-]+", "_", (s or "").strip().lower())


def norm_emirate(s: str | None) -> str | None:
    if not s:
        return None
    k = s.strip().lower().replace("_", " ")
    return EMIRATE_ALIASES.get(k) or (norm_key(s) if norm_key(s) in EMIRATES else None)


def parse_size(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    m = re.search(r"(\d{2,3})\s*[x×*х]\s*(\d{2,3})", s.lower())
    return (int(m.group(1)), int(m.group(2))) if m else None


def current_price(o: dict) -> tuple[float | None, bool]:
    """(price to pay now, sale_active)."""
    sp, until = o.get("sale_price_aed"), o.get("sale_valid_until")
    if sp is not None:
        try:
            ok = until is None or datetime.fromisoformat(until).date() >= today_uae()
        except ValueError:
            ok = True
        if ok:
            return sp, True
    return o.get("price_aed"), False


def firmness_values(p: dict) -> list[float]:
    f = p.get("firmness") or {}
    vals = [s["normalized_1_10"] for s in f.get("sides") or [] if s.get("normalized_1_10") is not None]
    if f.get("normalized_1_10") is not None:
        vals.append(f["normalized_1_10"])
    return vals


def firmness_summary(p: dict) -> dict:
    f = p.get("firmness") or {}
    out = {"label": f.get("brand_label"), "scale_1_10": f.get("normalized_1_10"),
           "dual_sided": f.get("dual_sided")}
    if f.get("sides"):
        out["sides"] = [{"side": s["side"], "label": s.get("brand_label"), "scale_1_10": s.get("normalized_1_10")}
                        for s in f["sides"]]
    return out


def delivery_rules(o: dict) -> list[dict]:
    return o.get("delivery") or SELLERS[o["seller_id"]].get("default_delivery") or []


def payment_methods(o: dict) -> list[str]:
    return o.get("payment_methods") or SELLERS[o["seller_id"]].get("payment_methods") or []


def delivery_for(o: dict, emirate: str | None) -> dict | None:
    for r in delivery_rules(o):
        if r["emirate"] == emirate:
            return r
    return None


def delivery_text(r: dict | None) -> str:
    if not r:
        return "unknown"
    if r.get("available") is None:
        return "not listed by seller (unknown)"
    if not r["available"]:
        return "not available"
    cost = "free" if r.get("cost_aed") == 0 else (f"{r['cost_aed']} AED" if r.get("cost_aed") is not None else "cost not listed")
    lt = r.get("lead_time_note") or (f"up to {r['lead_time_days_max']} days" if r.get("lead_time_days_max") else "")
    return f"{cost}; {lt}".strip("; ")


def delivery_summary(o: dict, emirate: str | None) -> str:
    if emirate:
        return f"{emirate}: {delivery_text(delivery_for(o, emirate))}"
    ok = [r["emirate"] for r in delivery_rules(o) if r.get("available")]
    free = all(r.get("cost_aed") == 0 for r in delivery_rules(o) if r.get("available"))
    return f"{'Free' if free else 'Paid'} delivery to: {', '.join(ok)}; other emirates not listed"


def product_features(p: dict) -> set[str]:
    cf = p.get("comfort_features") or {}
    feats = {k for k in FEATURE_KEYS if cf.get(k) is True}
    if p.get("construction_type") == "memory_foam":
        feats.add("memory_foam")
    if (p.get("firmness") or {}).get("dual_sided"):
        feats.add("flippable")
    return feats


def product_text(p: dict) -> str:
    cf = p.get("comfort_features") or {}
    return " ".join(str(x) for x in [
        p.get("name"), p.get("brand"), p.get("collection"), p.get("model"), p.get("construction_type"),
        p.get("audience"), (p.get("firmness") or {}).get("brand_label"), " ".join(cf.get("feature_tags") or []),
        " ".join(p.get("fillers") or []), p.get("description") or "",
    ]).lower()


PRODUCT_TEXT = {pid: product_text(p) for pid, p in PRODUCTS.items()}


def offer_row(o: dict, emirate: str | None = None) -> dict:
    p, v = VARIANTS[o["variant_id"]]
    cur, sale = current_price(o)
    return {
        "product_id": p["product_id"],
        "offer_id": o["offer_id"],
        "name": p["name"],
        "brand": p["brand"],
        "seller": SELLERS[o["seller_id"]]["name"],
        "size": v["size_label"],
        "size_class": v["size_class"],
        "firmness": firmness_summary(p),
        "height_cm": p.get("height_cm"),
        "construction_type": p.get("construction_type"),
        "audience": p.get("audience"),
        "regular_price_aed": o.get("price_aed"),
        "current_price_aed": cur,
        "discount_pct": round(100 * (1 - cur / o["price_aed"])) if sale and o.get("price_aed") else 0,
        "sale_ends": o.get("sale_valid_until") if sale else None,
        "availability": o.get("availability"),
        "delivery": delivery_summary(o, emirate),
        "url": o.get("url"),
        "last_updated": o.get("last_updated"),
    }


def catalog_meta() -> dict:
    return {"catalog_version": CAT["catalog_version"], "snapshot_generated_at": CAT["generated_at"],
            "currency": "AED", "country": "AE",
            "firmness_scale_note": CAT["firmness_scale"]["note"]}


# ----------------------------------------------------------------------------- core search
def _search(query: str | None = None, firmness_min: float | None = None, firmness_max: float | None = None,
            size: str | None = None, size_class: str | None = None, price_min: float | None = None,
            price_max: float | None = None, construction_type: str | None = None, audience: str | None = None,
            brand: str | None = None, emirate: str | None = None, payment_method: str | None = None,
            features: list[str] | None = None, in_stock_only: bool = False, sort: str = "relevance",
            limit: int = 10, one_per_product: bool = False) -> dict:
    notes: list[str] = []
    want_size = parse_size(size)
    if size and not want_size:
        notes.append(f"Could not parse size '{size}'; expected WIDTHxLENGTH in cm, e.g. 160x200. Size filter ignored.")
    sc = norm_key(size_class) if size_class else None
    if sc and sc not in SIZE_CLASSES:
        notes.append(f"Unknown size_class '{size_class}'. Valid: {SIZE_CLASSES}. Filter ignored.")
        sc = None
    em = norm_emirate(emirate) if emirate else None
    if emirate and not em:
        notes.append(f"Unknown emirate '{emirate}'. Valid: {EMIRATES}. Filter ignored.")
    pay = None
    if payment_method:
        pay = PAYMENT_ALIASES.get(norm_key(payment_method))
        if not pay:
            notes.append(f"Unknown payment_method '{payment_method}'. Valid: tabby, tamara, cod, card. Filter ignored.")
    feats = []
    for f in features or []:
        k = FEATURE_ALIASES.get(f.strip().lower(), norm_key(f))
        if k in FEATURE_KEYS:
            feats.append(k)
        else:
            notes.append(f"Unknown feature '{f}'. Valid: {FEATURE_KEYS}. Ignored.")
    ct = norm_key(construction_type) if construction_type else None
    qtokens = [t for t in re.findall(r"[a-z0-9&]+", (query or "").lower()) if len(t) > 2]

    rows = []
    for o in CAT["offers"]:
        p, v = VARIANTS[o["variant_id"]]
        if brand and brand.strip().lower() not in (p["brand"] or "").lower():
            continue
        if audience and (p.get("audience") or "") != audience.strip().lower():
            continue
        if ct and (p.get("construction_type") or "") != ct:
            continue
        fv = firmness_values(p)
        if firmness_min is not None or firmness_max is not None:
            lo, hi = firmness_min if firmness_min is not None else 0, firmness_max if firmness_max is not None else 11
            if not any(lo <= x <= hi for x in fv):
                continue
        if want_size and (v["width_cm"], v["length_cm"]) != want_size:
            continue
        if sc and v["size_class"] != sc:
            continue
        cur, _ = current_price(o)
        if price_min is not None and (cur is None or cur < price_min):
            continue
        if price_max is not None and (cur is None or cur > price_max):
            continue
        if em:
            r = delivery_for(o, em)
            if not r or r.get("available") is not True:
                continue
        if pay and not (pay & set(payment_methods(o))):
            continue
        pf = product_features(p)
        if feats and not set(feats) <= pf:
            continue
        if in_stock_only and o.get("availability") != "in_stock":
            continue
        score = 0.0
        if qtokens:
            txt = PRODUCT_TEXT[p["product_id"]]
            score += sum(2 if t in (p["name"] or "").lower() else 1 for t in qtokens if t in txt)
        score += 0.5 * (o.get("availability") == "in_stock")
        rows.append((score, cur or 0, fv, o))

    if sort == "price_asc":
        rows.sort(key=lambda r: r[1])
    elif sort == "price_desc":
        rows.sort(key=lambda r: -r[1])
    elif sort in ("firmness", "firmness_desc"):
        rows.sort(key=lambda r: (-(max(r[2]) if r[2] else -1), -(min(r[2]) if r[2] else -1), r[1]))
    elif sort == "firmness_asc":
        rows.sort(key=lambda r: ((min(r[2]) if r[2] else 99), r[1]))
    else:
        rows.sort(key=lambda r: (-r[0], r[1]))

    total = len(rows)
    if one_per_product:
        seen, dedup = set(), []
        for r in rows:
            if r[3]["product_id"] not in seen:
                seen.add(r[3]["product_id"])
                dedup.append(r)
        rows = dedup
    limit = max(1, min(int(limit or 10), 50))
    out = {
        "total_matching_offers": total,
        "returned": min(limit, len(rows)),
        "results": [offer_row(r[3], em) for r in rows[:limit]],
        "catalog": catalog_meta(),
    }
    if em and not rows:
        unk = [sl["name"] for sl in SELLERS.values() if any(r["emirate"] == em and r.get("available") is None for r in sl.get("default_delivery") or [])]
        if unk:
            notes.append(f"Delivery to {em} is not listed by seller(s) {unk}: unknown, not confirmed. Suggest contacting the seller (get_seller_policies).")
    if not rows:
        notes.append("No offers match all filters. Try relaxing price, size or firmness, or call list_filters.")
    if notes:
        out["notes"] = notes
    return out


# ----------------------------------------------------------------------------- server
INSTRUCTIONS = (
    "Live, agent-readable catalog of mattresses sold in the United Arab Emirates (pilot data: Askona Gulf / "
    "askona.ae, brands Askona, Sleep Professor, Grether & Wells). All prices are in AED, VAT-inclusive as listed. "
    "current_price_aed already applies any active sale. Always cite the product/offer url and its last_updated "
    "date when recommending. Never invent missing fields: null means the seller did not publish it — say so. "
    "Firmness uses a normalized 1-10 scale (1 softest, 10 firmest) derived from brand wording, not lab-measured; "
    "dual-sided mattresses list both sides. "
    "Typical flow: list_filters (optional) -> search_mattresses with structured filters -> get_product or "
    "compare_products for details -> get_seller_policies for delivery, payment (Tabby/Tamara/COD), returns. "
    "This is a test catalog snapshot; the buyer completes purchase on the url provided."
)

mcp = MCPServer(
    name="uae-mattress-catalog",
    title="UAE Mattress Catalog (agent-first, pilot)",
    instructions=INSTRUCTIONS,
    version="0.1.0",
)
RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


@mcp.tool(
    title="Search mattresses (UAE)",
    annotations=RO,
    description=(
        "Use this when a shopper wants mattress options in the UAE. Filters structured offers (one row per "
        "model x size x seller) and returns compact results with regular and current price in AED, sale end date, "
        "firmness (1-10), size, height, construction, availability, delivery for the requested emirate, and the "
        "purchase url. Prefer structured filters over free text. Firmness guide: soft 1-4, medium 4.5-6.5, "
        "firm 7-8, extra firm 9-10; dual-sided products match if either side is in range. Price filters apply "
        "to the current (sale-aware) price."),
)
def search_mattresses(
    query: Annotated[str | None, Field(description="Optional free-text keywords (model name, material, e.g. 'latex', 'Arizona'). Used for relevance ranking.")] = None,
    firmness_min: Annotated[float | None, Field(ge=1, le=10, description="Minimum firmness on 1-10 scale.")] = None,
    firmness_max: Annotated[float | None, Field(ge=1, le=10, description="Maximum firmness on 1-10 scale.")] = None,
    size: Annotated[str | None, Field(description="Exact size WIDTHxLENGTH in cm, e.g. '160x200', '90x200', '180x200'.")] = None,
    size_class: Annotated[Literal["crib", "kids_other", "single", "small_double", "double", "queen", "king", "super_king"] | None,
                          Field(description="Size class. UAE/EU mapping: single 80-100 wide, small_double 120, double 140-150, queen 160, king 180, super_king 200.")] = None,
    price_min: Annotated[float | None, Field(ge=0, description="Minimum current price, AED.")] = None,
    price_max: Annotated[float | None, Field(ge=0, description="Maximum current price, AED.")] = None,
    construction_type: Annotated[Literal["pocket_spring", "memory_foam"] | None, Field(description="Core construction.")] = None,
    audience: Annotated[Literal["adult", "kids"] | None, Field(description="Adult or kids mattresses.")] = None,
    brand: Annotated[str | None, Field(description="Brand name, e.g. 'Askona', 'Sleep Professor', 'Grether & Wells'.")] = None,
    emirate: Annotated[str | None, Field(description="Delivery emirate: dubai, abu_dhabi, sharjah, ajman, umm_al_quwain, ras_al_khaimah, fujairah. Keeps only offers with confirmed delivery there.")] = None,
    payment_method: Annotated[Literal["tabby", "tamara", "cod", "card"] | None, Field(description="Required payment option. cod = cash or card on delivery.")] = None,
    features: Annotated[list[str] | None, Field(description="Required features (all must be true): cooling, orthopedic, memory_foam, zoned_support, edge_support, motion_isolation, hypoallergenic_antibacterial, removable_cover, flippable.")] = None,
    in_stock_only: Annotated[bool, Field(description="Only offers explicitly marked in stock (many are 'unknown').")] = False,
    sort: Annotated[Literal["relevance", "price_asc", "price_desc", "firmness", "firmness_asc"], Field(description="'firmness' = firmest first; 'firmness_asc' = softest first.")] = "relevance",
    limit: Annotated[int, Field(ge=1, le=50, description="Max results (default 10).")] = 10,
    one_per_product: Annotated[bool, Field(description="Return at most one offer (best-ranked size) per model.")] = False,
) -> dict[str, Any]:
    return _search(query, firmness_min, firmness_max, size, size_class, price_min, price_max, construction_type,
                   audience, brand, emirate, payment_method, features, in_stock_only, sort, limit, one_per_product)


def _product_full(p: dict, size: str | None = None) -> dict:
    want = parse_size(size)
    offers = []
    for o in OFFERS_BY_PRODUCT.get(p["product_id"], []):
        _, v = VARIANTS[o["variant_id"]]
        if want and (v["width_cm"], v["length_cm"]) != want:
            continue
        cur, sale = current_price(o)
        offers.append({
            "offer_id": o["offer_id"], "size": v["size_label"], "size_class": v["size_class"],
            "seller": SELLERS[o["seller_id"]]["name"], "seller_id": o["seller_id"],
            "regular_price_aed": o.get("price_aed"), "current_price_aed": cur,
            "sale_ends": o.get("sale_valid_until") if sale else None,
            "availability": o.get("availability"), "weight_kg": v.get("weight_kg"), "gtin": v.get("gtin"),
            "url": o.get("url"), "last_updated": o.get("last_updated"),
        })
    offers.sort(key=lambda x: (x["current_price_aed"] or 0))
    cf = dict(p.get("comfort_features") or {})
    return {
        "product_id": p["product_id"], "name": p["name"], "brand": p["brand"], "manufacturer": p.get("manufacturer"),
        "collection": p.get("collection"), "model": p.get("model"), "audience": p.get("audience"),
        "construction_type": p.get("construction_type"), "spring_system": p.get("spring_system"),
        "firmness": firmness_summary(p), "height_cm": p.get("height_cm"), "layers": p.get("layers"),
        "fillers": p.get("fillers"), "cover_material": p.get("cover_material"),
        "comfort_features": cf, "feature_evidence": p.get("feature_evidence"),
        "max_load_per_sleeper_kg": p.get("max_load_per_sleeper_kg"), "max_load_qualifier": p.get("max_load_qualifier"),
        "warranty": p.get("warranty"), "trial_nights": p.get("trial_nights"),
        "country_of_origin": p.get("country_of_origin"), "certifications": p.get("certifications"),
        "description": p.get("description"), "images": (p.get("images") or [])[:4],
        "offers": offers,
        "seller_policies_hint": "Call get_seller_policies(seller_id) for delivery by emirate, payment and returns.",
        "raw_brand_attributes": p.get("raw_brand_attributes"),
        "source": p.get("source"), "last_updated": p.get("last_updated"),
    }


def _resolve_product(pid: str) -> dict | None:
    pid = (pid or "").strip()
    if pid in PRODUCTS:
        return PRODUCTS[pid]
    if pid in OFFERS:
        return PRODUCTS[OFFERS[pid]["product_id"]]
    if pid in VARIANTS:
        return VARIANTS[pid][0]
    low = pid.lower()
    for p in PRODUCTS.values():
        if low and (low == p["name"].lower() or low == (p.get("model") or "").lower()):
            return p
    return None


@mcp.tool(title="Get mattress details", annotations=RO, description=(
    "Use this to get full details of one mattress model: all sizes with regular/current AED prices and urls, "
    "firmness, layers, fillers, features with evidence quotes, warranty, max load, source url and last_updated. "
    "Accepts a product_id (e.g. 'sleep-professor:arizona-duo'), an offer_id or variant_id."))
def get_product(
    product_id: Annotated[str, Field(description="product_id from search results (offer_id/variant_id also accepted).")],
    size: Annotated[str | None, Field(description="Optional size to limit offers, e.g. '160x200'.")] = None,
) -> dict[str, Any]:
    p = _resolve_product(product_id)
    if not p:
        return {"error": f"Unknown product_id '{product_id}'. Use search_mattresses to find valid ids."}
    return {"product": _product_full(p, size), "catalog": catalog_meta()}


COMPARE_FIELDS = ["brand", "construction_type", "height_cm", "max_load_per_sleeper_kg", "trial_nights", "country_of_origin"]


@mcp.tool(title="Compare mattresses", annotations=RO, description=(
    "Use this to compare 2-5 mattress models side by side (firmness, height, construction, layers, features, "
    "warranty, max load, price for a given size). Pass size (e.g. '160x200') to compare prices for that size; "
    "otherwise the price range across sizes is shown."))
def compare_products(
    product_ids: Annotated[list[str], Field(min_length=2, max_length=5, description="2-5 product_ids.")],
    size: Annotated[str | None, Field(description="Optional size to compare prices, e.g. '160x200'.")] = None,
) -> dict[str, Any]:
    want = parse_size(size)
    cols, errors = [], []
    for pid in product_ids:
        p = _resolve_product(pid)
        if not p:
            errors.append(f"Unknown product_id '{pid}'")
            continue
        offs = OFFERS_BY_PRODUCT.get(p["product_id"], [])
        if want:
            offs = [o for o in offs if (VARIANTS[o["variant_id"]][1]["width_cm"], VARIANTS[o["variant_id"]][1]["length_cm"]) == want]
        prices = [current_price(o)[0] for o in offs if current_price(o)[0] is not None]
        col = {"product_id": p["product_id"], "name": p["name"]}
        col.update({k: p.get(k) for k in COMPARE_FIELDS})
        col["firmness"] = firmness_summary(p)
        col["layers"] = [l["material"] for l in p.get("layers") or []]
        col["features_true"] = sorted(product_features(p))
        col["warranty"] = p.get("warranty")
        if want:
            if offs:
                o = offs[0]
                cur, sale = current_price(o)
                col["price_for_size"] = {"size": size, "regular_aed": o.get("price_aed"), "current_aed": cur,
                                         "sale_ends": o.get("sale_valid_until") if sale else None,
                                         "availability": o.get("availability"), "url": o.get("url")}
            else:
                col["price_for_size"] = f"{size} not offered"
        col["current_price_range_aed"] = [min(prices), max(prices)] if prices else None
        col["sizes_available"] = sorted({VARIANTS[o["variant_id"]][1]["size_label"] for o in OFFERS_BY_PRODUCT.get(p["product_id"], [])})
        col["url"] = (p.get("source") or {}).get("url")
        col["last_updated"] = p.get("last_updated")
        cols.append(col)
    out: dict[str, Any] = {"products": cols, "catalog": catalog_meta()}
    if errors:
        out["errors"] = errors
    return out


@mcp.tool(title="Get seller policies", annotations=RO, description=(
    "Use this for delivery (by emirate: cost, lead time), payment options (card, cash/card on delivery, Tabby, "
    "Tamara), return/exchange rules, installation and old-mattress removal, showrooms and contact of a seller. "
    "Omit seller_id to get all sellers."))
def get_seller_policies(
    seller_id: Annotated[str | None, Field(description="Seller id, e.g. 'askona-gulf'. Omit for all sellers.")] = None,
) -> dict[str, Any]:
    sellers = [SELLERS[seller_id]] if seller_id in SELLERS else list(SELLERS.values())
    res = []
    for s in sellers:
        res.append({
            "seller_id": s["seller_id"], "name": s["name"], "legal_name": s.get("legal_name"),
            "trade_license_no": s.get("trade_license_no"), "seller_type": s.get("seller_type"),
            "website": s.get("website"), "contact_phone": s.get("contact_phone"), "showrooms": s.get("showrooms"),
            "payment_methods": s.get("payment_methods"),
            "delivery_by_emirate": [dict(r, summary=delivery_text(r)) for r in s.get("default_delivery") or []],
            "services": s.get("default_services"), "return_policy": s.get("return_policy"),
            "warranty_policy_url": s.get("warranty_policy_url"), "notes": s.get("notes"),
            "source": s.get("source"),
        })
    out: dict[str, Any] = {"sellers": res, "catalog": catalog_meta()}
    if seller_id and seller_id not in SELLERS:
        out["notes"] = [f"Unknown seller_id '{seller_id}'; returning all sellers."]
    return out


@mcp.tool(title="List available filters", annotations=RO, description=(
    "Use this first if unsure what the catalog contains: returns brands, sizes, size classes, construction "
    "types, firmness range and labels, current price range (AED), emirates with delivery, payment methods, "
    "feature flags and counts."))
def list_filters() -> dict[str, Any]:
    from collections import Counter
    prices = [current_price(o)[0] for o in CAT["offers"] if current_price(o)[0] is not None]
    fv = [x for p in CAT["products"] for x in firmness_values(p)]
    feat = Counter(f for p in CAT["products"] for f in product_features(p))
    em = sorted({r["emirate"] for s in SELLERS.values() for r in s.get("default_delivery") or [] if r.get("available")})
    pm = sorted({m for s in SELLERS.values() for m in s.get("payment_methods") or []})
    return {
        "counts": {"products": len(PRODUCTS), "offers": len(OFFERS), "sellers": len(SELLERS)},
        "brands": dict(Counter(p["brand"] for p in CAT["products"])),
        "audiences": dict(Counter(p["audience"] for p in CAT["products"])),
        "construction_types": dict(Counter(p["construction_type"] or "unknown" for p in CAT["products"])),
        "sizes": dict(Counter(VARIANTS[o["variant_id"]][1]["size_label"] for o in CAT["offers"]).most_common()),
        "size_classes": dict(Counter(VARIANTS[o["variant_id"]][1]["size_class"] for o in CAT["offers"])),
        "firmness_range_1_10": [min(fv), max(fv)] if fv else None,
        "firmness_label_map": CAT["firmness_scale"]["mapping"],
        "current_price_range_aed": [min(prices), max(prices)],
        "emirates_with_confirmed_delivery": em,
        "payment_methods": pm,
        "features_true_counts": dict(feat),
        "catalog": catalog_meta(),
    }


# ----------------------------------------------------------------------------- ChatGPT search/fetch compat
WORD_FIRMNESS = [("extra firm", (9, 10)), ("very firm", (9, 10)), ("medium firm", (6, 7)), ("medium soft", (4, 5)),
                 ("firmest", (8, 10)), ("hard", (7.5, 10)), ("firm", (7, 10)), ("soft", (1, 4.5)), ("medium", (4.5, 6.5)),
                 ("жестк", (7, 10)), ("жёстк", (7, 10)), ("мягк", (1, 4.5)), ("средн", (4.5, 6.5))]


def parse_free_text(q: str) -> dict:
    ql = q.lower()
    f: dict[str, Any] = {}
    sz = parse_size(ql)
    if sz:
        f["size"] = f"{sz[0]}x{sz[1]}"
    for k in ["super_king", "super king", "king", "queen", "double", "single"]:
        if re.search(rf"\b{k}\b", ql) and "size" not in f:
            f["size_class"] = k.replace(" ", "_")
            break
    m = re.search(r"(?:under|below|less than|max|up to|до|<|≤)\s*(\d[\d,\s]{2,})", ql)
    if m:
        f["price_max"] = float(re.sub(r"\D", "", m.group(1)))
    m = re.search(r"(?:over|above|from|от|more than|>)\s*(\d[\d,\s]{2,})", ql)
    if m:
        f["price_min"] = float(re.sub(r"\D", "", m.group(1)))
    for w, (lo, hi) in WORD_FIRMNESS:
        if w in ql:
            f["firmness_min"], f["firmness_max"] = lo, hi
            break
    if any(w in ql for w in ["kid", "child", "baby", "детск", "ребен", "ребён"]):
        f["audience"] = "kids"
    for alias, em in EMIRATE_ALIASES.items():
        if len(alias) > 3 and alias in ql:
            f["emirate"] = em
            break
    for pm in ["tabby", "tamara"]:
        if pm in ql:
            f["payment_method"] = pm
    for b in {p["brand"] for p in CAT["products"]}:
        if b.lower() in ql:
            f["brand"] = b
    feats = [k for k in FEATURE_KEYS if k.replace("_", " ") in ql or k in ql]
    feats += [v for a, v in FEATURE_ALIASES.items() if a in ql and v not in feats]
    if "cool" in ql and "cooling" not in feats:
        feats.append("cooling")
    if feats:
        f["features"] = feats
    if "firmest" in ql:
        f["sort"] = "firmness"
    elif any(w in ql for w in ["cheapest", "cheap", "lowest price", "budget", "дешев"]):
        f["sort"] = "price_asc"
    return f


@mcp.tool(title="Search (ChatGPT-compatible)", annotations=RO, description=(
    "Free-text mattress search for clients that only use search/fetch (e.g. ChatGPT deep research / company "
    "knowledge). Understands size (160x200, king), budget ('under 2000'), firmness words, kids, emirate, "
    "Tabby/Tamara. Returns {results:[{id,title,url}]}; pass an id to fetch for full details. Prefer "
    "search_mattresses when structured filters are available."))
def search(query: Annotated[str, Field(description="Natural-language shopping query.")]) -> dict[str, Any]:
    f = parse_free_text(query)
    res = _search(query=query, limit=10, one_per_product="size" not in f and "size_class" not in f, **f)
    if not res["results"] and f:
        res = _search(query=query, limit=10, one_per_product=True)
    results = []
    for r in res["results"]:
        sale = f", sale until {r['sale_ends']}" if r["sale_ends"] else ""
        results.append({
            "id": r["offer_id"],
            "title": f"{r['name']} {r['size']} — {r['current_price_aed']:.0f} AED (was {r['regular_price_aed']:.0f}{sale})",
            "url": r["url"],
        })
    return {"results": results}


@mcp.tool(title="Fetch (ChatGPT-compatible)", annotations=RO, description=(
    "Fetch full details for an id returned by search (offer_id or product_id). Returns {id,title,text,url,metadata}."))
def fetch(id: Annotated[str, Field(description="An id from search results (offer_id or product_id).")]) -> dict[str, Any]:
    p = _resolve_product(id)
    if not p:
        return {"id": id, "title": "Not found", "text": f"Unknown id '{id}'.", "url": "", "metadata": {}}
    o = OFFERS.get(id)
    full = _product_full(p)
    if o:
        full["selected_offer"] = offer_row(o)
    url = (o or {}).get("url") or (p.get("source") or {}).get("url") or ""
    title = p["name"] + (f" {VARIANTS[o['variant_id']][1]['size_label']}" if o else "")
    seller = SELLERS[(o or OFFERS_BY_PRODUCT[p["product_id"]][0])["seller_id"]]
    full["seller_policies"] = {"payment_methods": seller.get("payment_methods"),
                               "delivery_by_emirate": {r["emirate"]: delivery_text(r) for r in seller.get("default_delivery") or []},
                               "returns": seller.get("return_policy")}
    return {"id": id, "title": title, "text": json.dumps(full, ensure_ascii=False), "url": url,
            "metadata": {"product_id": p["product_id"], "brand": p["brand"], "last_updated": p.get("last_updated"),
                         "currency": "AED"}}


# ----------------------------------------------------------------------------- app
app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


async def health(request: Request):
    return JSONResponse({"ok": True, "server": "uae-mattress-catalog", "mcp_endpoint": "/mcp",
                         "products": len(PRODUCTS), "offers": len(OFFERS), **catalog_meta()})

app.add_route("/", health, methods=["GET"])
app.add_route("/health", health, methods=["GET"])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8765")), log_level="info",
                proxy_headers=True, forwarded_allow_ips="*")
