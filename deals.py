"""The three-step Deal Checker pipeline:

1. EXTRACT  - read every product row off the supplier's price list (image, PDF or text)
2. FILL     - enrich each product into a full record (category, risk flags,
               ungating need, a short buyer note)
3. DECIDE   - make the final buy / review / skip call, with probabilities

Each step feeds the next: Fill reads the rows Extract found, and Decide reads
the numbers plus everything Fill added.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from clocked import ClockedError

COLUMNS = {
    "brand": "brand name of the product",
    "product": "product name or title",
    "upc": "UPC, EAN or barcode number",
    "case_pack": "units per case (case pack)",
    "unit_cost": "wholesale unit cost (price the seller pays per unit)",
    "msrp": "MSRP or suggested retail price",
    "moq": "minimum order quantity",
    "notes": "supplier notes or remarks for this product, if any",
}
ORDER = list(COLUMNS)

CATEGORIES = [
    "Home & Kitchen", "Grocery & Gourmet", "Beauty & Personal Care", "Toys & Games",
    "Pet Supplies", "Sports & Outdoors", "Electronics", "Health & Household", "Other",
]
RISK_FLAGS = [
    "Expires within 6 months",
    "Discontinued or last lot",
    "UPC missing",
    "Brand may be restricted on Amazon",
    "Battery or hazmat item",
    "Profit margin too thin",
]


# ---------------------------------------------------------------- helpers
def money(v):
    if v is None:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    return float(m.group().replace(",", "")) if m else None


def integer(v):
    n = money(v)
    return int(n) if n is not None else None


def clean(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("none", "null", "n/a", "-") else s


def _unwrap(field):
    """Fields come back as {"value": ...} in text mode and as plain values in image mode."""
    if isinstance(field, dict) and "value" in field:
        return field["value"]
    return field


def _rows_from_value(value):
    rows = []
    for item in value or []:
        if isinstance(item, dict):
            rows.append({k: clean(_unwrap(item.get(k))) for k in ORDER})
        elif isinstance(item, str) and "|" in item:
            parts = [clean(p) for p in item.split("|")]
            parts += [""] * (len(ORDER) - len(parts))
            rows.append(dict(zip(ORDER, parts)))
    return [r for r in rows if r["product"]]


# ---------------------------------------------------------------- step 1
def extract_products(client, source, kind):
    """kind: 'image' (data URL), 'pdf' (data URL) or 'text'. Returns (supplier, rows, raw)."""
    records = {
        "supplier": {"description": "Name of the supplier or distributor company"},
        "products": {
            "description": "Every product row in the price list table",
            "list": True,
            "max_items": 32,
            "fields": COLUMNS,
            "row_anchor": "product",
        },
    }
    body = {"fields": records}
    if kind == "image":
        body["images"] = [source]
    elif kind == "pdf":
        body["pdf"] = source
    else:
        body["text"] = source

    try:
        raw = client.post("extract", body)
    except ClockedError as e:
        if kind != "image" or e.status not in (400, 422):
            raise
        # Fallback for image mode: one line per row, split on "|"
        body["fields"] = {
            "supplier": {"description": "Name of the supplier or distributor company"},
            "products": {
                "description": (
                    "Each product row of the table on one line, written as: "
                    "brand | product name | UPC | case pack | unit cost | MSRP | MOQ | notes. "
                    "Leave a part empty if the row does not show it."
                ),
                "list": True,
                "max_items": 32,
            },
        }
        raw = client.post("extract", body)

    result = raw.get("result") or raw.get("fields") or {}
    supplier = clean(_unwrap(result.get("supplier"))) or "Unknown supplier"
    rows = _rows_from_value(_unwrap(result.get("products")))
    return supplier, rows, raw


# ---------------------------------------------------------------- numbers
def add_numbers(rows, settings):
    for r in rows:
        cost, msrp, moq = money(r["unit_cost"]), money(r["msrp"]), integer(r["moq"])
        r["cost_n"], r["msrp_n"], r["moq_n"] = cost, msrp, moq
        if cost and msrp:
            fees = msrp * settings["referral_pct"] / 100 + settings["fba_fee"]
            profit = msrp - cost - fees
            r["fees_n"] = round(fees, 2)
            r["profit_n"] = round(profit, 2)
            r["roi_n"] = round(profit / cost * 100, 1)
        else:
            r["fees_n"] = r["profit_n"] = r["roi_n"] = None
    return rows


def product_sheet(r, supplier):
    def fmt(x, pre="$"):
        return f"{pre}{x:,.2f}" if isinstance(x, (int, float)) else "unknown"

    return "\n".join(
        [
            f"Supplier: {supplier}",
            f"Brand: {r['brand'] or 'not stated'}",
            f"Product: {r['product']}",
            f"UPC: {r['upc'] or 'NOT PROVIDED'}",
            f"Case pack: {r['case_pack'] or 'not stated'}",
            f"Wholesale unit cost: {fmt(r['cost_n'])}",
            f"MSRP (expected Amazon selling price): {fmt(r['msrp_n'])}",
            f"Minimum order quantity: {r['moq'] or 'not stated'}",
            f"Supplier notes: {r['notes'] or 'none'}",
            f"Estimated Amazon fees per unit: {fmt(r['fees_n'])}",
            f"Estimated profit per unit after fees: {fmt(r['profit_n'])}",
            "Estimated ROI: " + (f"{r['roi_n']}%" if r["roi_n"] is not None else "unknown"),
        ]
    )


# ---------------------------------------------------------------- step 2
def fill_one(client, r, supplier, settings):
    body = {
        "text": product_sheet(r, supplier),
        "instructions": (
            f"Today's date is {date.today():%d %B %Y}. You are helping an Amazon wholesale "
            f"seller review a supplier price list. A thin margin means ROI under "
            f"{settings['target_roi']}% or profit under ${settings['min_profit']:.2f} per unit."
        ),
        "fields": {
            "category": {
                "type": "choice",
                "description": "Amazon category this product would be listed in",
                "options": CATEGORIES,
            },
            "risk_flags": {
                "type": "multiselect",
                "description": (
                    "Select only the risks this product actually has, based on the sheet. "
                    "Wireless earbuds, power banks, rechargeable lamps and other products with "
                    "lithium batteries count as battery items."
                ),
                "options": RISK_FLAGS,
                "threshold": 0.6,
            },
            "needs_ungating": {
                "type": "yes_no",
                "description": (
                    "Is this product usually gated on Amazon (needs approval before a new seller "
                    "can list it), e.g. grocery, topical beauty or skincare, or a restricted brand?"
                ),
            },
            "ask_supplier": {
                "type": "generate",
                "description": (
                    "One short, specific question the buyer should ask this supplier before "
                    "ordering this product, based on what the sheet is missing or what looks "
                    "risky (for example the exact expiry date, the UPC, a brand authorization "
                    "letter, battery safety documents, or a lower price for a thin margin)."
                ),
                "max_tokens": 45,
                "format": "text",
            },
        },
    }
    raw = client.post("fill", body)
    f = raw.get("fields", {})
    flags = _unwrap(f.get("risk_flags")) or []
    if isinstance(flags, str):
        flags = [flags]
    r["category"] = clean(_unwrap(f.get("category"))) or "Other"
    r["flags"] = [x for x in flags if x]
    r["needs_ungating"] = bool(_unwrap(f.get("needs_ungating")))
    r["ask_supplier"] = clean(_unwrap(f.get("ask_supplier")))
    return raw


def fill_products(client, rows, supplier, settings):
    with ThreadPoolExecutor(max_workers=4) as pool:
        raws = list(pool.map(lambda r: fill_one(client, r, supplier, settings), rows))
    return raws


# ---------------------------------------------------------------- step 3
VERDICTS = {
    "buy": "Buy: the numbers meet the seller's targets and there is no serious listing risk",
    "review": (
        "Review: could be worth it, but a person must check something first "
        "(gating, expiry, missing UPC, battery/hazmat, discontinued stock, or borderline margin)"
    ),
    "skip": "Skip: the margin is too thin or negative, or the risks clearly outweigh the profit",
}


def decide_sheet(r, supplier):
    extra = [
        f"Amazon category: {r.get('category', 'Other')}",
        "Risk flags found: " + (", ".join(r.get("flags") or []) or "none"),
        f"Usually needs Amazon approval (ungating): {'yes' if r.get('needs_ungating') else 'no'}",
    ]
    return product_sheet(r, supplier) + "\n" + "\n".join(extra)


def decide_products(client, rows, supplier, settings):
    instructions = (
        "You are the final buying check for an Amazon wholesale seller. "
        f"Targets: ROI of at least {settings['target_roi']}% and profit of at least "
        f"${settings['min_profit']:.2f} per unit. Negative or tiny profit means skip. "
        "Good numbers with a risk that a person can clear (gating, expiry within 6 months, "
        "missing UPC, battery or hazmat, discontinued stock) mean review. "
        "Good numbers with no real risk mean buy."
    )
    docs = [{"id": f"p{i}", "text": decide_sheet(r, supplier)[:19000]} for i, r in enumerate(rows)]
    questions = {
        f"p{i}": {
            "type": "choice",
            "document": f"p{i}",
            "instructions": "Should the seller buy this product from this supplier?",
            "criteria": VERDICTS,
        }
        for i in range(len(rows))
    }
    raw = client.post(
        "decide", {"documents": docs, "questions": questions, "instructions": instructions}
    )
    answers = raw.get("answers", {})
    for i, r in enumerate(rows):
        a = answers.get(f"p{i}", {})
        r["verdict"] = a.get("choice") or "review"
        r["confidence"] = a.get("confidence")
        r["probs"] = a.get("probabilities") or {}
    return raw


def apply_guardrails(rows, settings):
    """Plain rules on top of the model: a negative profit can never be a buy,
    and an unsure decision is sent to a person."""
    for r in rows:
        r["final"] = r["verdict"]
        r["reason_tag"] = ""
        if r["verdict"] == "buy" and (r["profit_n"] is None or r["profit_n"] <= 0):
            r["final"], r["reason_tag"] = "skip", "rule: no profit"
        elif r["confidence"] is not None and r["confidence"] < settings["min_confidence"] and r["verdict"] != "review":
            r["final"], r["reason_tag"] = "review", "low confidence"
        r["buyer_note"] = make_note(r, settings)
    return rows


def make_note(r, settings):
    """A one-line reason built from the final verdict, the numbers and Fill's flags,
    so the note can never disagree with the decision."""
    p, roi = r.get("profit_n"), r.get("roi_n")
    money_line = f"${p:.2f} profit per unit ({roi}% ROI)" if p is not None else "profit unknown"
    checks = list(r.get("flags") or [])
    if r.get("needs_ungating"):
        checks.append("Amazon approval needed")
    checks = [c for c in checks if c != "Profit margin too thin"]
    if r["final"] == "skip":
        if p is not None and p <= 0:
            return f"Skip: loses ${abs(p):.2f} per unit after Amazon fees."
        if p is not None and (p < settings["min_profit"] or (roi or 0) < settings["target_roi"]):
            return f"Skip: only {money_line}, below your targets."
        return "Skip: the risks outweigh the profit" + (f" ({', '.join(checks).lower()})." if checks else ".")
    if r["final"] == "review":
        if r.get("reason_tag") == "low confidence":
            return f"Check by hand: the model is not sure. Numbers: {money_line}."
        what = ", ".join(checks).lower() if checks else "the margin and listing details"
        return f"Good numbers ({money_line}), but check first: {what}."
    if checks:
        return f"Good deal: {money_line}. Keep an eye on: {', '.join(checks).lower()}."
    return f"Strong deal: {money_line} and no risks found."
