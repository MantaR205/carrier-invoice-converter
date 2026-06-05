"""
Authentication and session management.
Password stored in environment only. Sessions time out after inactivity.
"""

import os
import time
import streamlit as st

SESSION_TIMEOUT_MINUTES = 30


def check_password() -> bool:
    """
    Returns True if authenticated and the session is still active.
    Shows a login screen otherwise.
    """
    password = os.environ.get("CONVERTER_PASSWORD", "")

    if not password:
        st.error("CONVERTER_PASSWORD not set. Contact your administrator.")
        st.stop()

    if st.session_state.get("authenticated"):
        last_active = st.session_state.get("last_active", 0)
        if time.time() - last_active > SESSION_TIMEOUT_MINUTES * 60:
            st.session_state.clear()
            st.info("Your session has expired. Please log in again.")
        else:
            st.session_state["last_active"] = time.time()
            return True

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown("## 🚚 Carrier Invoice Converter")
        st.caption("Fidelity Fulfilment")
        st.divider()

        with st.form("login_form"):
            entered = st.text_input("Password", type="password", placeholder="Enter password")
            submitted = st.form_submit_button("Login", use_container_width=True, type="primary")

        if submitted:
            if entered == password:
                st.session_state["authenticated"] = True
                st.session_state["last_active"] = time.time()
                st.rerun()
            else:
                st.error("Incorrect password.")

    return False
