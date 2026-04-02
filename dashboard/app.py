import streamlit as st
from dashboard.components.theme import apply_theme

st.set_page_config(
    page_title="Macro Trading System",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_theme()

st.markdown("<div class='premium-card'>", unsafe_allow_html=True)
st.markdown("<div class='hero-title'>📈 Macro Trading System</div>", unsafe_allow_html=True)
st.markdown(
    "<div class='hero-sub'>A premium yellow-and-black macro dashboard for economic data, policy analysis, currency strength, forecasting, and data management.</div>",
    unsafe_allow_html=True,
)
st.markdown("</div>", unsafe_allow_html=True)

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Platform", "Active")
with col2:
    st.metric("Database", "Connected")
with col3:
    st.metric("Mode", "Research Terminal")

st.markdown("<div class='premium-card'>", unsafe_allow_html=True)
st.subheader("Modules")
st.write(
    """
- **Home** for overview and navigation  
- **Economic Data Visualization** for country-wise chart analysis  
- **Currency Strength** for scoring and ranking  
- **Event Forecasting** for release prediction workflows  
- **Data Management** for uploads, manual entries, and coverage audits  
"""
)
st.markdown("</div>", unsafe_allow_html=True)