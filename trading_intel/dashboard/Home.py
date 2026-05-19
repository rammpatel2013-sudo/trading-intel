"""trading-intel — dashboard home page.

This is the Streamlit composition root. Instantiate clients here and pass
them into pages via streamlit's session_state.
"""
import streamlit as st

from trading_intel.config import get_settings

st.set_page_config(page_title="trading-intel", page_icon="📈", layout="wide")

st.title("📈 trading-intel")
st.caption("Institutional-grade stock research intelligence — Phase 0 scaffold")

settings = get_settings()
st.write(f"**Env:** `{settings.APP_ENV}`")
st.write(f"**Watchlist:** {', '.join(settings.watchlist_symbols)}")
st.write(f"**DB:** `{settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else 'local'}`")

st.divider()
st.info("Use the sidebar to navigate pages. Pages will appear here as they are built.")
