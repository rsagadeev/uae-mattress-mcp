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


def offer_lead_time_days(o: dict) -> int | None:
    """Product-level minimum lead time in days (from catalog or delivery_note)."""
    v = o.get("lead_time_days")
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    note = o.get("delivery_note") or ""
    m = re.search(r"no earlier than\s+(\d+)\s+days", note, re.I)
    return int(m.group(1)) if m else None


def delivery_summary(o: dict, emirate: str | None) -> str:
    """Emirate slot plus product lead time when the latter is longer (or unscheduled)."""
    note = (o.get("delivery_note") or "").rstrip(".")
    lead = offer_lead_time_days(o)
    product_lt = note or (f"no earlier than {lead} days after order confirmation" if lead else None)
    if emirate:
        r = delivery_for(o, emirate)
        base = f"{emirate}: {delivery_text(r)}"
        emirate_max = (r or {}).get("lead_time_days_max")
        if lead and (emirate_max is None or lead > emirate_max):
            return f"{base}. Product lead time: {product_lt}"
        if product_lt and not lead:
            return f"{base}. {product_lt}"
        return base
    ok = [r["emirate"] for r in delivery_rules(o) if r.get("available")]
    free = all(r.get("cost_aed") == 0 for r in delivery_rules(o) if r.get("available"))
    base = f"{'Free' if free else 'Paid'} delivery to: {', '.join(ok)}; other emirates not listed"
    if product_lt:
        return f"{base}. Product lead time: {product_lt}"
    return base


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

_STOPWORDS = {
    "mattress", "mattresses", "the", "and", "for", "with", "in", "of", "a", "an",
    "cm", "aed", "size", "uae", "ua", "please", "need", "want", "looking",
}

def _norm_text(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _product_name_blob(p: dict) -> str:
    skus = [o.get("seller_sku") or "" for o in OFFERS_BY_PRODUCT.get(p["product_id"], [])]
    return _norm_text(" ".join([
        p.get("name") or "", p.get("model") or "", p.get("brand") or "",
        p.get("collection") or "", p["product_id"].replace(":", " ").replace("-", " "),
        " ".join(skus),
    ]))


PRODUCT_NAME_BLOB = {pid: _product_name_blob(p) for pid, p in PRODUCTS.items()}


def _query_tokens(query: str | None) -> list[str]:
    if not query:
        return []
    q = re.sub(r"\d{2,3}\s*[x×*х]\s*\d{2,3}", " ", query.lower())
    return [t for t in re.findall(r"[a-z0-9&]+", q) if len(t) > 1 and t not in _STOPWORDS]


def compact_warranty(p: dict) -> dict:
    w = p.get("warranty") or {}
    out = {}
    if w.get("standard_years") is not None:
        out["standard_years"] = w["standard_years"]
    if w.get("extended_years") is not None:
        out["extended_years"] = w["extended_years"]
        if w.get("extended_condition"):
            out["extended_condition"] = w["extended_condition"]
    return out


def age_range(p: dict) -> str | None:
    a = p.get("recommended_age")
    if isinstance(a, dict) and a.get("label"):
        return a["label"]
    return None


def offer_row(o: dict, emirate: str | None = None) -> dict:
    p, v = VARIANTS[o["variant_id"]]
    cur, sale = current_price(o)
    w = compact_warranty(p)
    load = p.get("max_load_per_sleeper_kg")
    row = {
        "product_id": p["product_id"],
        "offer_id": o["offer_id"],
        "name": p["name"],
        "brand": p["brand"],
        "seller": SELLERS[o["seller_id"]]["name"],
        "seller_id": o["seller_id"],
        "size": v["size_label"],
        "size_class": v["size_class"],
        "firmness": firmness_summary(p),
        "height_cm": p.get("height_cm"),
        "construction_type": p.get("construction_type"),
        "audience": p.get("audience"),
        "age_range": age_range(p),
        "features": sorted(product_features(p)),
        "max_load_per_sleeper_kg": load,
        "max_load_qualifier": p.get("max_load_qualifier") if load is not None else None,
        "warranty": w or None,
        "payment_methods": payment_methods(o),
        "regular_price_aed": o.get("price_aed"),
        "current_price_aed": cur,
        "discount_pct": round(100 * (1 - cur / o["price_aed"])) if sale and o.get("price_aed") else 0,
        "sale_ends": o.get("sale_valid_until") if sale else None,
        "availability": o.get("availability"),
        "delivery": delivery_summary(o, emirate),
        "delivery_note": o.get("delivery_note"),
        "lead_time_days": offer_lead_time_days(o),
        "url": o.get("url"),
        "last_updated": o.get("last_updated"),
    }
    rat = product_rating(p)
    row["rating"] = rat.get("value")
    row["review_count"] = rat.get("count")
    if rat.get("note"):
        row["reviews_note"] = rat["note"]
    row["fit"] = compact_fit(p)
    return row


def product_rating(p: dict) -> dict:
    r = p.get("rating") or {}
    revs = p.get("reviews") or {}
    if revs.get("zero_reviews") or (revs.get("count") == 0 and not r.get("count")):
        return {"value": None, "count": 0, "note": "no customer reviews on the brand site"}
    if r.get("count"):
        return {"value": r.get("value"), "count": r.get("count"), "source": r.get("source")}
    return {"value": revs.get("average"), "count": revs.get("count") or 0, "source": revs.get("source")}


def _lab(a: dict | None) -> dict | None:
    if not a:
        return None
    out = {k: a.get(k) for k in ("label", "score", "provenance") if a.get(k) is not None}
    return out or None


def compact_fit(p: dict) -> dict:
    fit = p.get("fit") or {}
    pos = fit.get("sleep_positions") or {}
    w = fit.get("sleeper_weight_kg") or {}
    vfm = fit.get("value_for_money") or {}
    return {
        "sleeper_weight": {k: w.get(k) for k in ("min_kg", "max_kg", "label", "provenance") if w.get(k) is not None},
        "side": _lab(pos.get("side")),
        "back": _lab(pos.get("back")),
        "stomach": _lab(pos.get("stomach")),
        "motion_isolation": _lab(fit.get("motion_isolation")),
        "couples": _lab(fit.get("couples")),
        "hot_sleepers": _lab(fit.get("hot_sleepers")),
        "back_pain_orthopedic": _lab(fit.get("back_pain_orthopedic")),
        "firm_lovers": _lab(fit.get("firm_lovers")),
        "value_for_money": {k: vfm.get(k) for k in ("index", "band", "provenance") if vfm.get(k) is not None},
    }


def _fit_score_ok(attr: dict | None, min_label: str = "ok") -> bool:
    if not attr:
        return False
    order = {"poor": 0, "ok": 1, "good": 2}
    if attr.get("label") in order:
        return order[attr["label"]] >= order.get(min_label, 1)
    return (attr.get("score") or 0) >= 0.34


def weight_fits(p: dict, kg: float) -> bool:
    w = (p.get("fit") or {}).get("sleeper_weight_kg") or {}
    lo, hi = w.get("min_kg"), w.get("max_kg")
    if lo is not None and kg < float(lo):
        return False
    if hi is not None and kg > float(hi):
        return False
    return lo is not None or hi is not None


def compact_review(r: dict) -> dict:
    return {
        "author": r.get("author"),
        "date": r.get("date"),
        "rating": r.get("rating"),
        "text": r.get("text"),
        "verified": r.get("verified"),
        "photo_urls": r.get("photo_urls") or [],
    }


def compact_review_block(p: dict, limit: int = 5, with_photos_only: bool = False) -> dict:
    block = p.get("reviews") or {}
    reviews = list(block.get("reviews") or [])
    if with_photos_only:
        reviews = [r for r in reviews if r.get("photo_urls")]
    return {
        "average": block.get("average"),
        "count": block.get("count") or 0,
        "has_photos": block.get("has_photos"),
        "photo_count": block.get("photo_count") or 0,
        "zero_reviews": bool(block.get("zero_reviews") or (block.get("count") or 0) == 0),
        "summary": (block.get("summary") or {}).get("text"),
        "pros": (block.get("summary") or {}).get("pros") or [],
        "cons": (block.get("summary") or {}).get("cons") or [],
        "source": block.get("source"),
        "reviews": [compact_review(r) for r in reviews[: max(0, int(limit or 5))]],
    }


def seller_store_ratings(seller_id: str) -> dict:
    s = SELLERS.get(seller_id) or {}
    agg = s.get("store_reviews") or {}
    rooms = []
    for sh in s.get("showrooms") or []:
        rooms.append({
            "name": sh.get("name"),
            "emirate": sh.get("emirate"),
            "google_rating": sh.get("google_rating"),
            "google_review_count": sh.get("google_review_count"),
            "google_maps_url": sh.get("google_maps_url"),
        })
    return {
        "seller_id": seller_id,
        "name": s.get("name"),
        "aggregate_rating": agg.get("aggregate_rating"),
        "aggregate_review_count": agg.get("aggregate_review_count"),
        "method": agg.get("method"),
        "showrooms": rooms,
    }



def catalog_meta() -> dict:
    return {"catalog_version": CAT["catalog_version"], "snapshot_generated_at": CAT["generated_at"],
            "currency": "AED", "country": "AE",
            "firmness_scale_note": CAT["firmness_scale"]["note"]}


# ----------------------------------------------------------------------------- core search
def _search(query: str | None = None, firmness_min: float | None = None, firmness_max: float | None = None,
            size: str | None = None, size_class: str | None = None, price_min: float | None = None,
            price_max: float | None = None, construction_type: str | None = None, audience: str | None = None,
            brand: str | None = None, seller: str | None = None, emirate: str | None = None, payment_method: str | None = None,
            features: list[str] | None = None, in_stock_only: bool = False, sort: str = "relevance",
            limit: int = 10, one_per_product: bool = False,
            sleep_position: str | None = None, sleeper_weight_kg: float | None = None,
            min_rating: float | None = None, couple: bool = False, motion_isolation: bool = False) -> dict:
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
    seller_filter = seller.strip().lower() if seller else None
    if seller_filter:
        known = {sid.lower() for sid in SELLERS} | {s["name"].lower() for s in SELLERS.values()}
        if seller_filter not in known:
            notes.append(
                f"Unknown seller '{seller}'. Valid ids: {sorted(SELLERS)}. "
                f"Valid names: {sorted({s['name'] for s in SELLERS.values()})}. Filter ignored."
            )
            seller_filter = None
    ct = norm_key(construction_type) if construction_type else None
    qtokens = _query_tokens(query)
    name_query = False
    name_threshold = 0
    if qtokens:
        phrase = " ".join(qtokens)
        name_hits = []
        for pid, blob in PRODUCT_NAME_BLOB.items():
            hits = sum(1 for tok in qtokens if tok in blob)
            phrase_hit = phrase in blob
            name_hits.append((hits, phrase_hit, pid))
        max_hits = max((h[0] for h in name_hits), default=0)
        any_phrase = any(h[1] for h in name_hits)
        # Treat as a model-name query when some product name contains most tokens or the phrase.
        if any_phrase or (max_hits >= 2 and max_hits >= int(0.75 * len(qtokens) + 0.999)):
            name_query = True
            name_threshold = max(max_hits if any_phrase and max_hits == len(qtokens) else max(2, max_hits - 1), 2)
            # If an exact/full-token name match exists, drop weak name matches.
            if any_phrase or max_hits == len(qtokens):
                name_threshold = max(name_threshold, max_hits)

    rows = []
    for o in CAT["offers"]:
        p, v = VARIANTS[o["variant_id"]]
        if brand and brand.strip().lower() not in (p["brand"] or "").lower():
            continue
        if seller_filter:
            srec = SELLERS.get(o["seller_id"], {})
            if seller_filter not in (o["seller_id"] or "").lower() and seller_filter not in (srec.get("name") or "").lower():
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
        if sleep_position:
            pos = ((p.get("fit") or {}).get("sleep_positions") or {}).get(sleep_position)
            if not _fit_score_ok(pos):
                continue
        if sleeper_weight_kg is not None and not weight_fits(p, float(sleeper_weight_kg)):
            continue
        if min_rating is not None:
            rat = product_rating(p)
            if rat.get("value") is None or rat.get("count") in (None, 0) or float(rat["value"]) < float(min_rating):
                continue
        if couple and not _fit_score_ok((p.get("fit") or {}).get("couples")):
            continue
        if motion_isolation and not _fit_score_ok((p.get("fit") or {}).get("motion_isolation")):
            continue
        score = 0.0
        if qtokens:
            pid = p["product_id"]
            blob = PRODUCT_NAME_BLOB[pid]
            txt = PRODUCT_TEXT[pid]
            name_hits = sum(1 for tok in qtokens if tok in blob)
            phrase = " ".join(qtokens)
            phrase_hit = phrase in blob
            if name_query and name_hits < name_threshold and not phrase_hit:
                continue
            if not name_query and not any(tok in txt for tok in qtokens):
                continue
            score += 20 if phrase_hit else 0
            score += 5 * name_hits
            score += sum(1 for tok in qtokens if tok in txt)
            if o.get("seller_sku") and _norm_text(o.get("seller_sku")) == _norm_text(query):
                score += 30
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
    elif sort == "rating_desc":
        rows.sort(key=lambda r: (-(product_rating(VARIANTS[r[3]["variant_id"]][0]).get("value") or 0),
                                  -(product_rating(VARIANTS[r[3]["variant_id"]][0]).get("count") or 0), r[1]))
    else:
        rows.sort(key=lambda r: (-r[0], r[1]))

    total = len(rows)
    if one_per_product:
        seen, dedup = set(), []
        for r in rows:
            key = (r[3]["product_id"], r[3]["seller_id"])
            if key not in seen:
                seen.add(key)
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
    "Live, agent-readable catalog of mattresses sold in the United Arab Emirates. Pilot sellers: Askona Gulf "
    "(askona.ae — brands Askona, Sleep Professor, Grether & Wells) and The Mattress Store "
    "(themattressstore.com — a multi-brand retailer selling Englander, Vispring and Colunex). "
    "Always name the seller in recommendations: the same model can be offered by more than one seller. "
    "All prices are in AED, VAT-inclusive as listed. "
    "current_price_aed already applies any active sale. Always cite the product/offer url and its last_updated "
    "date when recommending. Never invent missing fields: null means the seller did not publish it — say so. "
    "Firmness uses a normalized 1-10 scale (1 softest, 10 firmest) derived from brand wording, not lab-measured; "
    "dual-sided mattresses list both sides. "
    "Typical flow: list_filters (optional) -> search_mattresses with structured filters (sleep_position, "
    "sleeper_weight_kg, min_rating, couple, motion_isolation) -> get_product or compare_products for details -> "
    "get_reviews / get_seller_reviews for review texts and photo URLs -> get_seller_policies for delivery, "
    "payment (Tabby/Tamara/COD), returns and store ratings. "
    "Semantic fit attributes always carry provenance: brand_stated, derived_rule, or from_reviews. "
    "Never present derived_rule or from_reviews values as brand claims, and never invent reviews. "
    "If review count is 0, say explicitly that the brand site has no customer reviews. "
    "Customer photos are original hosted URLs — do not claim they are stored here. "
    "This is a test catalog snapshot; the buyer completes purchase on the url provided."
)

mcp = MCPServer(
    name="uae-mattress-catalog",
    title="UAE Mattress Catalog (agent-first, pilot)",
    instructions=INSTRUCTIONS,
    version="0.4.0",
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
        "to the current (sale-aware) price. Results include rating+review count and compact semantic fit "
        "(weight range, sleep positions, couples, motion isolation, value index). Fit labels that are "
        "derived_rule or from_reviews are not brand claims."),
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
    construction_type: Annotated[Literal["pocket_spring", "memory_foam", "hybrid", "latex", "foam"] | None, Field(description="Core construction.")] = None,
    audience: Annotated[Literal["adult", "kids"] | None, Field(description="Adult or kids mattresses.")] = None,
    brand: Annotated[str | None, Field(description="Brand name, e.g. 'Askona', 'Englander', 'Vispring', 'Sleep Professor'.")] = None,
    seller: Annotated[str | None, Field(description="Seller id or name, e.g. 'askona-gulf', 'the-mattress-store', 'Askona', 'The Mattress Store'. Filters offers of that seller only.")] = None,
    emirate: Annotated[str | None, Field(description="Delivery emirate: dubai, abu_dhabi, sharjah, ajman, umm_al_quwain, ras_al_khaimah, fujairah. Keeps only offers with confirmed delivery there.")] = None,
    payment_method: Annotated[Literal["tabby", "tamara", "cod", "card"] | None, Field(description="Required payment option. cod = cash or card on delivery.")] = None,
    features: Annotated[list[str] | None, Field(description="Required features (all must be true): cooling, orthopedic, memory_foam, zoned_support, edge_support, motion_isolation, hypoallergenic_antibacterial, removable_cover, flippable.")] = None,
    in_stock_only: Annotated[bool, Field(description="Only offers explicitly marked in stock (many are 'unknown').")] = False,
    sort: Annotated[Literal["relevance", "price_asc", "price_desc", "firmness", "firmness_asc", "rating_desc"], Field(description="'firmness' = firmest first; 'firmness_asc' = softest first; 'rating_desc' = highest review rating first.")] = "relevance",
    limit: Annotated[int, Field(ge=1, le=50, description="Max results (default 10).")] = 10,
    one_per_product: Annotated[bool, Field(description="Return at most one offer (best-ranked size) per model.")] = False,
    sleep_position: Annotated[Literal["side", "back", "stomach", "combination"] | None,
                              Field(description="Keep models rated ok/good for this sleep position (semantic fit).")] = None,
    sleeper_weight_kg: Annotated[float | None, Field(ge=10, le=250, description="Sleeper body weight in kg; keeps models whose fit weight range includes this value.")] = None,
    min_rating: Annotated[float | None, Field(ge=1, le=5, description="Minimum average product review rating (1-5). Products with zero reviews are excluded.")] = None,
    couple: Annotated[bool, Field(description="If true, keep models rated ok/good for couples (motion isolation + queen+ / duo).")] = False,
    motion_isolation: Annotated[bool, Field(description="If true, keep models rated ok/good for partner motion isolation.")] = False,
) -> dict[str, Any]:
    return _search(query, firmness_min, firmness_max, size, size_class, price_min, price_max, construction_type,
                   audience, brand, seller, emirate, payment_method, features, in_stock_only, sort, limit, one_per_product,
                   sleep_position, sleeper_weight_kg, min_rating, couple, motion_isolation)


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
            "payment_methods": payment_methods(o),
            "delivery": delivery_summary(o, None),
            "delivery_note": o.get("delivery_note"),
            "lead_time_days": offer_lead_time_days(o),
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
        "warranty": p.get("warranty"), "recommended_age": p.get("recommended_age"),
        "age_range": age_range(p), "trial_nights": p.get("trial_nights"),
        "country_of_origin": p.get("country_of_origin"), "certifications": p.get("certifications"),
        "description": p.get("description"), "images": (p.get("images") or [])[:4],
        "rating": product_rating(p),
        "fit": p.get("fit"),
        "reviews": compact_review_block(p, limit=5),
        "seller_store_ratings": [seller_store_ratings(sid) for sid in
                                 sorted({o["seller_id"] for o in OFFERS_BY_PRODUCT.get(p["product_id"], [])})],
        "offers": offers,
        "seller_policies_hint": "Call get_seller_policies(seller_id) for delivery by emirate, payment and returns. Call get_reviews or get_seller_reviews for more review texts.",
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
    "firmness, layers, fillers, semantic fit with provenance, review summary plus up to 5 reviews with photo URLs, seller store ratings, warranty, max load, source url and last_updated. Fit values with provenance derived_rule or from_reviews are not brand claims. "
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
        col["rating"] = product_rating(p)
        col["fit"] = compact_fit(p)
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
        all_offs = OFFERS_BY_PRODUCT.get(p["product_id"], [])
        col["sellers"] = sorted({SELLERS[o["seller_id"]]["name"] for o in all_offs})
        if want and offs:
            col["offers_for_size"] = []
            for o in offs:
                cur, sale = current_price(o)
                col["offers_for_size"].append({
                    "seller": SELLERS[o["seller_id"]]["name"], "seller_id": o["seller_id"],
                    "regular_aed": o.get("price_aed"), "current_aed": cur,
                    "sale_ends": o.get("sale_valid_until") if sale else None,
                    "availability": o.get("availability"), "url": o.get("url"),
                })
            # cheapest offer kept in price_for_size for compatibility
            cheapest = min(col["offers_for_size"], key=lambda x: x["current_aed"] or 10**12)
            col["price_for_size"]["seller"] = cheapest["seller"]
            col["price_for_size"]["seller_id"] = cheapest["seller_id"]
        col["current_price_range_aed"] = [min(prices), max(prices)] if prices else None
        col["sizes_available"] = sorted({VARIANTS[o["variant_id"]][1]["size_label"] for o in all_offs})
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
            "website": s.get("website"), "contact_phone": s.get("contact_phone"),
            "contact_phones": s.get("contact_phones"), "showrooms": s.get("showrooms"),
            "payment_methods": s.get("payment_methods"),
            "delivery_by_emirate": [dict(r, summary=delivery_text(r)) for r in s.get("default_delivery") or []],
            "services": s.get("default_services"), "return_policy": s.get("return_policy"),
            "warranty_policy_url": s.get("warranty_policy_url"), "notes": s.get("notes"),
            "source": s.get("source"),
            "store_reviews": seller_store_ratings(s["seller_id"]),
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
        "sellers": [{"seller_id": s["seller_id"], "name": s["name"], "seller_type": s.get("seller_type"),
                     "offers": sum(1 for o in CAT["offers"] if o["seller_id"] == s["seller_id"])} for s in CAT["sellers"]],
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
        "semantic_filters": {
            "sleep_position": ["side", "back", "stomach", "combination"],
            "sleeper_weight_kg": "number, matches models whose fit weight range includes the value",
            "min_rating": "1-5, excludes products with zero reviews",
            "couple": "boolean, ok/good for couples",
            "motion_isolation": "boolean, ok/good partner-motion isolation",
        },
        "catalog": catalog_meta(),
    }




@mcp.tool(title="Get product reviews", annotations=RO, description=(
    "Use this for customer reviews of one mattress: average rating, count, short pros/cons, and review texts "
    "with author first name/initial, date, rating, verified flag and original customer photo URLs. "
    "If the product has zero reviews, the response says so explicitly — do not invent reviews. "
    "Set with_photos_only to keep only reviews that include a photo."))
def get_reviews(
    product_id: Annotated[str, Field(description="product_id (offer_id/variant_id also accepted).")],
    with_photos_only: Annotated[bool, Field(description="If true, only reviews that include customer photo URLs.")] = False,
    limit: Annotated[int, Field(ge=1, le=30, description="Max review texts to return (default 5).")] = 5,
) -> dict[str, Any]:
    p = _resolve_product(product_id)
    if not p:
        return {"error": f"Unknown product_id '{product_id}'. Use search_mattresses to find valid ids."}
    block = compact_review_block(p, limit=limit, with_photos_only=with_photos_only)
    return {
        "product_id": p["product_id"],
        "name": p["name"],
        "source_url": (p.get("reviews") or {}).get("source_url") or (p.get("source") or {}).get("url"),
        **block,
        "catalog": catalog_meta(),
    }


@mcp.tool(title="Get seller / showroom reviews", annotations=RO, description=(
    "Use this for Google Maps ratings of seller showrooms: aggregate rating weighted by review count, "
    "per-showroom rating and count, the Maps place URL, and a few representative recent reviews when a "
    "public source published the text. Omit seller_id for both pilot sellers. Do not invent missing review texts."))
def get_seller_reviews(
    seller_id: Annotated[str | None, Field(description="Seller id, e.g. 'askona-gulf' or 'the-mattress-store'. Omit for all.")] = None,
    showroom: Annotated[str | None, Field(description="Optional showroom name substring, e.g. 'Festival City' or 'Deerfields'.")] = None,
) -> dict[str, Any]:
    sellers = [SELLERS[seller_id]] if seller_id in SELLERS else list(SELLERS.values())
    out = []
    needle = (showroom or "").strip().lower()
    for s in sellers:
        rooms = []
        for sh in s.get("showrooms") or []:
            if needle and needle not in (sh.get("name") or "").lower() and needle not in (sh.get("address") or "").lower():
                continue
            reviews = [compact_review(r) for r in (sh.get("google_reviews") or [])[:5]]
            rooms.append({
                "name": sh.get("name"),
                "emirate": sh.get("emirate"),
                "address": sh.get("address"),
                "phone": sh.get("phone") or (sh.get("phones") or [None])[0],
                "google_rating": sh.get("google_rating"),
                "google_review_count": sh.get("google_review_count"),
                "google_maps_url": sh.get("google_maps_url"),
                "rating_source": sh.get("google_rating_source"),
                "reviews_source": sh.get("google_reviews_source"),
                "reviews": reviews,
                "review_texts_available": bool(reviews),
            })
        agg = s.get("store_reviews") or {}
        out.append({
            "seller_id": s["seller_id"],
            "name": s.get("name"),
            "aggregate_rating": agg.get("aggregate_rating"),
            "aggregate_review_count": agg.get("aggregate_review_count"),
            "method": agg.get("method"),
            "source_note": agg.get("source_note"),
            "collected_at": agg.get("collected_at"),
            "showrooms": rooms,
        })
    res: dict[str, Any] = {"sellers": out, "catalog": catalog_meta()}
    if seller_id and seller_id not in SELLERS:
        res["notes"] = [f"Unknown seller_id '{seller_id}'; returning all sellers."]
    return res


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
    for sid, srec in SELLERS.items():
        names = [sid.replace("-", " "), sid, srec.get("name") or ""]
        if any(n.lower() in ql for n in names if len(n) >= 5):
            f["seller"] = sid
            break
    if "mattress store" in ql or "themattressstore" in ql:
        f["seller"] = "the-mattress-store"
    if "askona.ae" in ql or "askona gulf" in ql:
        f["seller"] = "askona-gulf"
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
    if any(w in ql for w in ["side sleeper", "sleep on my side", "sleep on the side", "на боку"]):
        f["sleep_position"] = "side"
    elif any(w in ql for w in ["stomach sleeper", "sleep on my stomach", "на живот"]):
        f["sleep_position"] = "stomach"
    elif any(w in ql for w in ["back sleeper", "sleep on my back", "на спине"]):
        f["sleep_position"] = "back"
    m = re.search(r"(\d{2,3})\s*(?:kg|килограмм|кг)\b", ql)
    if m:
        f["sleeper_weight_kg"] = float(m.group(1))
    if any(w in ql for w in ["couple", "couples", "partner", "для пар", "изоляц"]):
        f["couple"] = True
        f["motion_isolation"] = True
    m = re.search(r"(?:rating|rated|stars|оценка)\s*(?:at least|over|≥|>=)?\s*(\d(?:[.,]\d)?)", ql)
    if m:
        f["min_rating"] = float(m.group(1).replace(",", "."))
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
