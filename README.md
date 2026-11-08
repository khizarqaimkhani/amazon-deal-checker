# 📦 Deal Checker

**Drop a supplier price list. Get a buy, review or skip call on every product — with the numbers and the reason.**

Built for the [Clocked Build Challenge](https://clocked.rezolve.ai/challenge) (Oct 21 – Nov 10, 2026).

## The problem

Amazon wholesale sellers get price lists from suppliers all day: PDFs, spreadsheets, and phone photos of printed sheets. For every list, someone has to type the rows into a sheet, work out fees and profit, then check each product for problems — expiry dates, missing UPCs, gated categories, battery items, discontinued stock. A 30-row list easily takes an hour, and the risky products are the ones people miss.

I run a small Amazon wholesale business, so this is a problem I deal with myself.

## What it does

1. Upload a price list (image, PDF or pasted text), or try one of the three samples.
2. Deal Checker reads every product row, estimates Amazon fees, profit and ROI.
3. It flags the risks on each product and gives a **BUY / REVIEW / SKIP** verdict with how sure it is.
4. Download everything as a CSV.

Your targets (referral fee, FBA fee, target ROI, minimum profit, confidence cut-off) are set in the sidebar.

## How it uses Clocked

Three Clocked endpoints work as a chain. Each one's output changes what the next one sees.

| Step | Endpoint | What it does in the app |
|---|---|---|
| 1 | **Extract** `/clocked/extract` | Reads the supplier name and every product row as a list of records (brand, product, UPC, case pack, unit cost, MSRP, MOQ, notes). Works on images, PDFs and text. Missing values stay empty instead of being guessed — e.g. a missing UPC becomes a flag. |
| 2 | **Fill** `/clocked/fill` | Turns each row (plus the computed fees, profit and ROI) into a full record in one call: Amazon **category** (choice), **risk flags** (multiselect: expiring soon, discontinued, UPC missing, brand may be restricted, battery/hazmat, thin margin), **needs ungating** (yes/no) and a short **question to ask the supplier** before ordering (generated). |
| 3 | **Decide** `/clocked/decide` | One request with one document per product. Each question reads its own product sheet — including Fill's flags — and returns **buy / review / skip** with the probability of each option. |

On top of the model, two plain rules keep it safe:
- a product with zero or negative profit can never be a BUY;
- if Decide is less sure than the sidebar cut-off, the product goes to REVIEW for a person.

Each card's one-line reason is built from the final verdict, the numbers and Fill's flags, so the note can never disagree with the decision.

> The app runs on Streamlit Community Cloud's free tier. If it has been idle, click "Yes, get this app back up" and give it ~30 seconds.

The **"Under the hood"** panel shows the raw Clocked responses and the credits used for each call.

## Run it yourself

```bash
git clone https://github.com/khizarqaimkhani/amazon-deal-checker
cd amazon-deal-checker
pip install -r requirements.txt
mkdir -p .streamlit
echo 'CLOCKED_API_KEY = "your-key-here"' > .streamlit/secrets.toml
streamlit run app.py
```

Get a key at [clocked.rezolve.ai](https://clocked.rezolve.ai). The key is read from Streamlit secrets (or the `CLOCKED_API_KEY` environment variable) and is never stored in the repo.

**Deploy on Streamlit Community Cloud:** New app → pick this repo → main file `app.py` → Advanced settings → Secrets → `CLOCKED_API_KEY = "..."` → Deploy.

## Files

- `app.py` — the Streamlit interface
- `deals.py` — the Extract → Fill → Decide pipeline, fee maths and safety rules
- `clocked.py` — a small HTTPS client for the Clocked API (auth, idempotency keys, retries, credit tracking, image resizing)
- `samples/` — three **fictional** supplier price lists: a clean one, a blurry phone photo, and one full of tricky items (loss-making products, a missing UPC, an expiry date, a discontinued lot, a possibly restricted brand)

## Notes

- Fees are estimates (referral % of MSRP + a flat FBA fee). Always confirm real fees in Seller Central.
- All sample data is made up. No real supplier data is used.
- Built with Streamlit, pandas, Pillow and requests. Code written with help from AI coding tools.

## Team

Muhammad Khizar — [@khizarqaimkhani](https://github.com/khizarqaimkhani)
