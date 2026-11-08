import html
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from clocked import Clocked, ClockedError, image_to_data_url, pdf_to_data_url
from deals import (
    add_numbers,
    apply_guardrails,
    decide_products,
    extract_products,
    fill_products,
)

st.set_page_config(page_title="Deal Checker", page_icon="📦", layout="wide")

SAMPLES = {
    "BrightNest — clean list": "samples/list1-clean.png",
    "Evergreen — phone photo": "samples/list2-phone-photo.jpg",
    "Summit — tricky items": "samples/list3-tricky.png",
}
BADGE = {"buy": ("BUY", "#0f7b4f", "#e3f5ec"), "review": ("REVIEW", "#9a6200", "#fdf1dc"),
         "skip": ("SKIP", "#b3261e", "#fbe4e2")}

st.markdown(
    """
<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
.hero h1 {font-size: 2.4rem; margin-bottom: .2rem; letter-spacing: -.02em;}
.hero p {color: #5b6270; font-size: 1.05rem; margin-top: 0;}
.steps {display:flex; gap:.6rem; flex-wrap:wrap; margin:.6rem 0 1.4rem;}
.step {border:1px solid #d9dde3; border-radius:999px; padding:.3rem .8rem; font-size:.85rem; color:#39414d;}
.step b {color:#163a6b;}
.badge {display:inline-block; font-weight:700; font-size:.75rem; letter-spacing:.06em;
        padding:.2rem .55rem; border-radius:6px;}
.card {border:1px solid #e3e6ea; border-radius:12px; padding:1rem 1.1rem; margin-bottom:.7rem;}
.card .title {font-weight:650; font-size:1.02rem;}
.card .meta {color:#5b6270; font-size:.88rem; margin:.15rem 0 .45rem;}
.flag {display:inline-block; background:#f1f3f6; color:#39414d; border-radius:6px;
       padding:.1rem .45rem; font-size:.78rem; margin:0 .3rem .3rem 0;}
.note {font-size:.92rem; margin-top:.35rem;}
.ask {font-size:.86rem; color:#39414d; margin-top:.3rem;}
</style>
""",
    unsafe_allow_html=True,
)


def esc(v):
    return html.escape(str(v)) if v else ""


def get_key():
    try:
        return st.secrets["CLOCKED_API_KEY"]
    except Exception:
        return os.environ.get("CLOCKED_API_KEY")


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Your targets")
    referral = st.number_input("Amazon referral fee (%)", 0.0, 50.0, 15.0, 0.5)
    fba = st.number_input("FBA fee per unit ($)", 0.0, 50.0, 4.00, 0.25)
    target_roi = st.number_input("Target ROI (%)", 0, 300, 30, 5)
    min_profit = st.number_input("Minimum profit per unit ($)", 0.0, 100.0, 3.00, 0.5)
    min_conf = st.slider("Send to review if model is less sure than", 0.5, 0.95, 0.70, 0.05)
    st.caption("Fees are estimates. Check the real fee in Seller Central before you buy.")

settings = {
    "referral_pct": referral, "fba_fee": fba, "target_roi": target_roi,
    "min_profit": min_profit, "min_confidence": min_conf,
}

# ------------------------------------------------------------------ header
st.markdown(
    """
<div class="hero">
<h1>📦 Deal Checker</h1>
<p>Drop a supplier price list. Get a <b>buy, review or skip</b> call on every product — with the numbers and the reason.</p>
</div>
<div class="steps">
<span class="step"><b>1 · Extract</b> reads every row</span>
<span class="step"><b>2 · Fill</b> adds category, risks, gating</span>
<span class="step"><b>3 · Decide</b> makes the call with probabilities</span>
</div>
""",
    unsafe_allow_html=True,
)

api_key = get_key()
if not api_key:
    st.error("CLOCKED_API_KEY is not set. Add it in Streamlit → Settings → Secrets.")
    st.stop()

tab_up, tab_sample, tab_text = st.tabs(["Upload price list", "Try a sample", "Paste text"])
source = kind = None
with tab_up:
    up = st.file_uploader("Image (PNG/JPG/WebP) or PDF", type=["png", "jpg", "jpeg", "webp", "pdf"])
    if up:
        raw = up.getvalue()
        if up.name.lower().endswith(".pdf"):
            source, kind = pdf_to_data_url(raw), "pdf"
            st.caption(f"PDF ready: {up.name}")
        else:
            source, kind = image_to_data_url(raw), "image"
            st.image(raw, width=520)
with tab_sample:
    choice = st.radio("Sample price lists (fictional data)", list(SAMPLES), horizontal=True)
    sample_path = Path(__file__).parent / SAMPLES[choice]
    st.image(str(sample_path), width=620)
    use_sample = st.checkbox("Use this sample", value=not bool(up))
    if use_sample and not up:
        source, kind = image_to_data_url(sample_path.read_bytes()), "image"
with tab_text:
    pasted = st.text_area("Paste a price list (copied from an email, CSV or spreadsheet)", height=180)
    if pasted.strip() and not up and not source:
        source, kind = pasted, "text"
    elif pasted.strip():
        st.caption("An upload or sample is selected; it will be used instead of this text.")

go = st.button("Check deals", type="primary", disabled=source is None, width="stretch")

# ------------------------------------------------------------------ run
if go:
    client = Clocked(api_key)
    raws = {}
    try:
        with st.status("Checking deals…", expanded=True) as status:
            st.write("**1 · Extract** — reading the price list")
            supplier, rows, raws["extract"] = extract_products(client, source, kind)
            if not rows:
                status.update(label="No product rows found", state="error")
                st.json(raws["extract"])
                st.stop()
            st.write(f"Found **{len(rows)} products** from **{supplier}**")
            add_numbers(rows, settings)

            st.write("**2 · Fill** — category, risk flags, gating, buyer note")
            raws["fill"] = fill_products(client, rows, supplier, settings)

            st.write("**3 · Decide** — buy / review / skip")
            raws["decide"] = decide_products(client, rows, supplier, settings)
            apply_guardrails(rows, settings)
            status.update(label=f"Done — {len(rows)} products checked", state="complete", expanded=False)
        st.session_state["result"] = {
            "supplier": supplier, "rows": rows, "raws": raws,
            "credits": client.credits_used, "calls": client.calls,
        }
    except ClockedError as e:
        st.error(f"Clocked returned an error ({e.status}).")
        st.json(e.body if isinstance(e.body, (dict, list)) else {"error": str(e.body)})
        st.stop()

res = st.session_state.get("result")
if res:
    rows = res["rows"]
    buys = [r for r in rows if r["final"] == "buy"]
    st.subheader(f"Results — {res['supplier']}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 Buy", len(buys))
    c2.metric("🟡 Review", sum(r["final"] == "review" for r in rows))
    c3.metric("🔴 Skip", sum(r["final"] == "skip" for r in rows))
    pot = sum((r["profit_n"] or 0) * (r["moq_n"] or 0) for r in buys)
    c4.metric("Est. profit on Buy items (at MOQ)", f"${pot:,.0f}")

    order = {"buy": 0, "review": 1, "skip": 2}
    for r in sorted(rows, key=lambda x: (order.get(x["final"], 3), -(x["roi_n"] or -999))):
        label, fg, bg = BADGE.get(r["final"], BADGE["review"])
        conf = f"{r['confidence'] * 100:.0f}% sure" if r.get("confidence") is not None else ""
        tag = f" · {r['reason_tag']}" if r["reason_tag"] else ""
        roi = f"{r['roi_n']}%" if r["roi_n"] is not None else "—"
        profit = f"${r['profit_n']:.2f}" if r["profit_n"] is not None else "—"
        flags = "".join(f'<span class="flag">⚠ {esc(f)}</span>' for f in r.get("flags", []))
        if r.get("needs_ungating"):
            flags += '<span class="flag">🔒 Usually needs approval</span>'
        st.markdown(
            f"""
<div class="card">
<span class="badge" style="color:{fg};background:{bg}">{label}</span>
<span style="color:#5b6270;font-size:.85rem;margin-left:.4rem">{conf}{tag}</span>
<div class="title" style="margin-top:.35rem">{esc(r['product'])}</div>
<div class="meta">{esc(r['brand']) or '—'} · {esc(r.get('category', ''))} · UPC {esc(r['upc']) or 'missing'} ·
cost {esc(r['unit_cost']) or '—'} → MSRP {esc(r['msrp']) or '—'} · <b>profit {profit}/unit · ROI {roi}</b> · MOQ {esc(r['moq']) or '—'}</div>
{flags}
<div class="note">{esc(r.get('buyer_note', ''))}</div>
{f'<div class="ask">💬 Ask the supplier: {esc(r["ask_supplier"])}</div>' if r.get("ask_supplier") and r["final"] != "skip" else ""}
</div>
""",
            unsafe_allow_html=True,
        )
        if r.get("probs"):
            with st.expander("Why? decision probabilities", expanded=False):
                for k in ("buy", "review", "skip"):
                    p = float(r["probs"].get(k, 0))
                    st.progress(min(max(p, 0.0), 1.0), text=f"{k}: {p * 100:.1f}%")

    table = pd.DataFrame(
        [
            {
                "verdict": r["final"], "confidence": r.get("confidence"), "brand": r["brand"],
                "product": r["product"], "upc": r["upc"], "unit_cost": r["cost_n"],
                "msrp": r["msrp_n"], "est_fees": r["fees_n"], "profit_per_unit": r["profit_n"],
                "roi_pct": r["roi_n"], "moq": r["moq_n"], "category": r.get("category"),
                "risk_flags": "; ".join(r.get("flags", [])), "needs_ungating": r.get("needs_ungating"),
                "buyer_note": r.get("buyer_note"), "ask_supplier": r.get("ask_supplier"),
            }
            for r in rows
        ]
    )
    st.download_button("Download results (CSV)", table.to_csv(index=False), "deal-check.csv", "text/csv")

    with st.expander("Under the hood — raw Clocked responses"):
        st.caption(f"Credits used this run: {res['credits']:.0f} across {len(res['calls'])} calls")
        st.dataframe(pd.DataFrame(res["calls"]), width="stretch", hide_index=True)
        for step in ("extract", "fill", "decide"):
            st.markdown(f"**{step}**")
            data = res["raws"].get(step)
            st.code(json.dumps(data, indent=2)[:15000], language="json")
