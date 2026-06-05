"""
Carrier Invoice Converter — Streamlit UI

Drag-drop messy carrier invoice files; get back a single normalized CSV for Unify:
    tracking_number, cost, surcharge, weight, service, despatch_date, order_ref
"""

import io
import time
import logging

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from auth import check_password
from store import ConversionStore
from converters import (
    OUTPUT_COLUMNS,
    FileType,
    detect_file_type,
    detect_carrier,
    royal_mail,
)
from converters.royal_mail import UploadedFile

load_dotenv()
logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="Carrier Invoice Converter", page_icon="🚚", layout="wide")

if not check_password():
    st.stop()

# ── File type display metadata ───────────────────────────────────────────────
TYPE_LABELS = {
    FileType.RM_INVOICE: ("Invoice", "🔵"),
    FileType.RM_OUTBOUND: ("Outbound Tracking", "🟢"),
    FileType.RM_RTS: ("Return to Sender", "🟡"),
    FileType.RM_NO_DATA: ("No Data", "⚪"),
    FileType.UNKNOWN: ("Unknown", "🔴"),
}


@st.cache_resource
def get_store():
    return ConversionStore()


store = get_store()

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🚚 Invoice Converter")
    st.divider()
    st.header("History")
    stats = store.stats()
    c1, c2 = st.columns(2)
    c1.metric("Converted", stats["total_batches"])
    c2.metric("Uploaded", stats["uploaded"])
    st.caption(f"{stats['pending']} awaiting upload to Unify")
    st.divider()
    if st.button("🚪 Logout"):
        st.session_state.clear()
        st.rerun()
    last_active = st.session_state.get("last_active", 0)
    remaining = max(0, 30 - int((time.time() - last_active) / 60))
    st.caption(f"Session expires in {remaining} min")

st.title("🚚 Carrier Invoice Converter")
st.caption("Convert carrier invoice packages into a normalized CSV for Unify.")

tab_convert, tab_history = st.tabs(["Convert", "History"])

# ─────────────────────────────────────────────────────────────────────────────
#  Convert tab
# ─────────────────────────────────────────────────────────────────────────────
with tab_convert:
    st.header("1. Upload invoice files")
    st.caption(
        "Drop **all** files for one invoice together: the Royal Mail invoice CSV, "
        "the outbound tracking CSVs, RTS files, and any standalone surcharge files."
    )

    uploaded = st.file_uploader(
        "Drop files here",
        type=["csv", "txt"],
        accept_multiple_files=True,
    )

    if not uploaded:
        st.info("👆 Upload one or more files to begin.")
        with st.expander("What does the output look like?"):
            st.dataframe(pd.DataFrame([{
                "tracking_number": "FA434450680GB", "cost": 2.09, "surcharge": 0.12,
                "weight": 0.20, "service": "ROYAL MAIL TRACKED 48 (HV)",
                "despatch_date": "2026-04-17", "order_ref": "#TD736742",
            }]), use_container_width=True, hide_index=True)
        st.stop()

    # ── Detect file types ──
    parsed_files = []
    for uf in uploaded:
        raw = uf.read()
        first_line = raw[:512].decode("iso-8859-1", errors="replace").splitlines()[0] if raw else ""
        ft = detect_file_type(uf.name, first_line)
        parsed_files.append(UploadedFile(name=uf.name, file_type=ft, data=raw))

    carrier = detect_carrier([pf.file_type for pf in parsed_files])

    st.subheader("Detected files")
    detect_df = pd.DataFrame([{
        "File": pf.name,
        "Type": f"{TYPE_LABELS[pf.file_type][1]} {TYPE_LABELS[pf.file_type][0]}",
    } for pf in parsed_files])
    st.dataframe(detect_df, use_container_width=True, hide_index=True)

    has_invoice = any(pf.file_type == FileType.RM_INVOICE for pf in parsed_files)
    has_unknown = any(pf.file_type == FileType.UNKNOWN for pf in parsed_files)

    if carrier == "unknown":
        st.error("Could not identify the carrier from these files. Only Royal Mail is supported so far.")
        st.stop()
    if not has_invoice:
        st.warning("⚠️ No invoice file detected — costs will be 0 without the billing CSV.")
    if has_unknown:
        st.warning("Some files were not recognised and will be skipped.")

    # ── Convert ──
    st.header("2. Convert")
    if st.button("🔄 Convert to normalized CSV", type="primary"):
        with st.spinner("Converting..."):
            result = royal_mail.convert(parsed_files)
        st.session_state["last_result"] = result

    result = st.session_state.get("last_result")
    if result:
        st.header("3. Review & download")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Parcels", len(result.rows))
        m2.metric("Total cost", f"£{result.total_cost:,.2f}")
        m3.metric("Total surcharge", f"£{result.total_surcharge:,.2f}")
        m4.metric("Computed total", f"£{result.computed_total:,.2f}")

        # Reconciliation sanity check vs the invoice's own stated total
        if result.stated_total_net is not None:
            diff = round(result.computed_total - result.stated_total_net, 2)
            if abs(diff) <= 0.05:
                st.success(
                    f"✅ Computed total £{result.computed_total:,.2f} matches the invoice's "
                    f"stated net £{result.stated_total_net:,.2f}."
                )
            else:
                st.warning(
                    f"⚠️ Computed total £{result.computed_total:,.2f} differs from the invoice's "
                    f"stated net £{result.stated_total_net:,.2f} by £{diff:,.2f}. "
                    "Check for missing outbound files."
                )

        if result.warnings:
            with st.expander(f"⚠️ {len(result.warnings)} warnings"):
                for w in result.warnings:
                    st.write(f"• {w}")

        out_df = pd.DataFrame([r.to_output_dict() for r in result.rows], columns=OUTPUT_COLUMNS)
        st.dataframe(out_df.head(200), use_container_width=True, hide_index=True)
        if len(out_df) > 200:
            st.caption(f"Showing first 200 of {len(out_df)} rows.")

        # Build the output filename
        inv = result.invoice_number or "invoice"
        out_name = f"normalized_{result.carrier}_{inv}.csv"
        csv_bytes = out_df.to_csv(index=False).encode("utf-8")

        col_dl, col_save = st.columns([1, 1])
        with col_dl:
            st.download_button(
                "⬇️ Download normalized CSV",
                data=csv_bytes,
                file_name=out_name,
                mime="text/csv",
                type="primary",
                use_container_width=True,
            )
        with col_save:
            if st.button("💾 Save to history", use_container_width=True):
                existing = store.find_existing_invoice(result.carrier, result.invoice_number)
                if existing:
                    st.warning(
                        f"Invoice {result.invoice_number} was already converted on "
                        f"{existing['created_at']:%d %b %Y} (status: {existing['status']}). "
                        "Saved again as a new batch."
                    )
                batch_id = store.record_batch(
                    carrier=result.carrier,
                    invoice_number=result.invoice_number,
                    invoice_date=result.invoice_date,
                    source_files=len(parsed_files),
                    row_count=len(result.rows),
                    total_cost=result.total_cost,
                    total_surcharge=result.total_surcharge,
                    stated_net=result.stated_total_net,
                    output_filename=out_name,
                    file_summary=result.file_summary,
                )
                st.success(f"Saved as batch #{batch_id}.")

# ─────────────────────────────────────────────────────────────────────────────
#  History tab
# ─────────────────────────────────────────────────────────────────────────────
with tab_history:
    st.header("Conversion history")
    batches = store.recent_batches(limit=100)
    if not batches:
        st.info("No conversions saved yet.")
    else:
        hist_df = pd.DataFrame(batches)
        hist_df = hist_df.rename(columns={
            "id": "Batch", "created_at": "Converted", "carrier": "Carrier",
            "invoice_number": "Invoice", "invoice_date": "Inv. Date",
            "source_files": "Files", "row_count": "Rows", "total_cost": "Cost (£)",
            "total_surcharge": "Surcharge (£)", "stated_net": "Stated Net (£)",
            "status": "Status", "uploaded_at": "Uploaded",
        })
        st.dataframe(hist_df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Mark a batch as uploaded to Unify")
        pending = [b for b in batches if b["status"] != "uploaded"]
        if pending:
            options = {
                f"#{b['id']} — {b['invoice_number'] or 'no number'} ({b['row_count']} rows)": b["id"]
                for b in pending
            }
            choice = st.selectbox("Select batch", list(options.keys()))
            if st.button("✅ Mark as uploaded"):
                store.mark_uploaded(options[choice])
                st.success("Marked as uploaded.")
                st.rerun()
        else:
            st.caption("All saved batches have been marked as uploaded.")
