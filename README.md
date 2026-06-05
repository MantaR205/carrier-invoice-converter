# Carrier Invoice Converter

A standalone tool that turns messy carrier invoice packages into a single,
normalized CSV that [Unify](https://unify-wms.test) can import for freight
reconciliation.

All the carrier-specific complexity (multi-file packages, manifest-level
billing, surcharge annotations, return-to-sender files) lives **here**, so Unify
only ever has to deal with one simple format.

## Normalized output

Every conversion produces a CSV with exactly these columns:

| Column            | Meaning                                            |
|-------------------|----------------------------------------------------|
| `tracking_number` | Carrier tracking barcode (the match key in Unify)  |
| `cost`            | Base shipping cost, pre-surcharge (£)              |
| `surcharge`       | Total surcharges for the parcel, 0 if none (£)     |
| `weight`          | Measured weight (kg)                               |
| `service`         | Carrier service description                        |
| `despatch_date`   | Despatch date (YYYY-MM-DD)                          |
| `order_ref`       | Customer's own order reference (fallback match key) |

## Supported carriers

| Carrier    | Input files                                                            |
|------------|-----------------------------------------------------------------------|
| Royal Mail | Invoice CSV (pipe-delimited) + Outbound / RTS tracking CSVs (per ZIP)  |

Royal Mail bills per *manifest* (docket), not per parcel. The converter reads
the per-docket cost and surcharges from the invoice, then allocates them evenly
across the individual tracking numbers found in the outbound files.

New carriers are added as a module under `converters/` plus a detection rule in
`converters/detect.py`.

## Running locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # set CONVERTER_PASSWORD
streamlit run app.py
```

Then open http://localhost:8501.

## How finance use it

1. **Convert** tab → drag in all files for one invoice. The app shows each
   file's detected type.
2. Click **Convert** → review the parcel count, totals, and the sanity check
   against the invoice's own stated net total.
3. **Download** the normalized CSV, and **Save to history** so there's a record.
4. Upload the CSV into Unify, then come back to the **History** tab and mark the
   batch as *uploaded*.

## Project layout

```
app.py                  Streamlit UI
auth.py                 Password gate + session timeout
store.py                DuckDB tracking of conversion batches
converters/
  base.py               NormalizedRow / ConversionResult + output schema
  detect.py             File-type + carrier detection
  royal_mail.py         Royal Mail converter (invoice + outbound + RTS)
data/                   DuckDB database (gitignored)
sample_data/            Example input files for testing
```

## Deployment

See [DEPLOY.md](DEPLOY.md) — runs as a systemd service behind Nginx + Certbot
on its own subdomain, same pattern as the Order Reconciler.
