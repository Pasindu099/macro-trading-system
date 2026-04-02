import streamlit as st


def apply_theme():
    st.markdown(
        """
        <style>
        /* -----------------------------
           GLOBAL APP
        ----------------------------- */
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(255,204,0,0.10), transparent 28%),
                radial-gradient(circle at bottom right, rgba(255,204,0,0.08), transparent 25%),
                linear-gradient(135deg, #050505 0%, #0a0a0a 45%, #111111 100%);
            color: #f8f8f8;
        }

        .block-container {
            max-width: 96rem;
            padding-top: 1.2rem;
            padding-bottom: 1.5rem;
        }

        /* -----------------------------
           SIDEBAR
        ----------------------------- */
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #111111 0%, #0b0b0b 100%);
            border-right: 1px solid rgba(255,204,0,0.14);
        }

        /* -----------------------------
           TYPOGRAPHY
        ----------------------------- */
        h1, h2, h3, h4 {
            color: #ffffff !important;
            letter-spacing: 0.2px;
        }

        p, label, div, span {
            color: #e8e8e8;
        }

        .hero-title {
            font-size: 2.1rem;
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 0.35rem;
        }

        .hero-sub {
            color: #cfcfcf;
            font-size: 1rem;
        }

        .muted-text {
            color: #b8b8b8;
            font-size: 0.95rem;
        }

        /* -----------------------------
           CARDS
        ----------------------------- */
        .premium-card {
            background: rgba(18, 18, 18, 0.90);
            border: 1px solid rgba(255,204,0,0.18);
            border-radius: 20px;
            padding: 20px;
            margin-bottom: 16px;
            backdrop-filter: blur(12px);
            box-shadow:
                0 0 0 1px rgba(255,255,255,0.02) inset,
                0 10px 28px rgba(0,0,0,0.34),
                0 0 24px rgba(255,204,0,0.10);
        }

        /* -----------------------------
           SELECTS / INPUTS
        ----------------------------- */
        div[data-baseweb="select"] > div {
            background-color: #0f0f0f !important;
            border: 1px solid rgba(255,204,0,0.18) !important;
            border-radius: 12px !important;
            color: #f8f8f8 !important;
        }

        div[data-testid="stTextInput"] input,
        div[data-testid="stNumberInput"] input,
        div[data-testid="stDateInput"] input {
            background-color: #0f0f0f !important;
            color: #f8f8f8 !important;
            border-radius: 12px !important;
            border: 1px solid rgba(255,204,0,0.18) !important;
        }

        /* -----------------------------
           BUTTONS
        ----------------------------- */
        .stButton > button {
            background: linear-gradient(135deg, #ffcc00 0%, #d4a300 100%);
            color: #111111;
            border: none;
            border-radius: 12px;
            font-weight: 700;
            padding: 0.55rem 1rem;
            box-shadow: 0 4px 18px rgba(255,204,0,0.22);
        }

        .stButton > button:hover {
            filter: brightness(1.05);
        }

        /* -----------------------------
           METRICS
        ----------------------------- */
        div[data-testid="stMetric"] {
            background: rgba(18, 18, 18, 0.90);
            border: 1px solid rgba(255,204,0,0.18);
            border-radius: 18px;
            padding: 14px;
            box-shadow:
                0 8px 24px rgba(0,0,0,0.25),
                0 0 18px rgba(255,204,0,0.07);
        }

        /* -----------------------------
           TABS
        ----------------------------- */
        button[data-baseweb="tab"] {
            background: transparent;
            color: #d4d4d4;
            border-radius: 10px 10px 0 0;
        }

        button[data-baseweb="tab"][aria-selected="true"] {
            color: #ffcc00 !important;
        }

        /* -----------------------------
           DATAFRAME
        ----------------------------- */
        div[data-testid="stDataFrame"] {
            border-radius: 16px;
            overflow: hidden;
            border: 1px solid rgba(255,204,0,0.12);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )