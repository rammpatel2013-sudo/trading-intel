"""Vol Regime — five-dimension VIX decomposition + composite VSI.

Reads the latest ``INDEX_VOL_REGIME`` signal and renders:

- A composite **Vol Stress Index** gauge (0-100) at the top.
- A **state card** with the regime label, severity, and a short bullet-list
  rationale assembled from the dimensions that drove it.
- A **5-dimension decomposition strip** — one card per dimension (Level / Skew
  / Tail / Term / Vol-of-vol). Each card shows the raw metric, its 252d
  percentile, a z-score bar, severity tag, and a one-line plain-English read
  pulled from the signal payload.
- **Sparklines** of each dimension's primary metric over the lookback.
- **Recent transitions** table with `from → to` and rationale.

The page is read-only. The math lives in ``strategies/vol_regime.py``; the
descriptions are computed there and shipped in the signal payload so the dash
just renders them.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings

st.set_page_config(page_title="Vol Regime", layout="wide")


# ── DB ─────────────────────────────────────────────────────────────────


@st.cache_resource
def _session() -> Session:
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    return sessionmaker(bind=engine)()


@st.cache_data(ttl=300)
def _latest_signal() -> dict | None:
    sess = _session()
    row = sess.execute(
        text(
            """
            SELECT ts, payload, confidence
              FROM signals
             WHERE signal_type = 'INDEX_VOL_REGIME' AND symbol = 'INDEX'
             ORDER BY ts DESC
             LIMIT 1
            """
        )
    ).mappings().first()
    return dict(row) if row else None


@st.cache_data(ttl=300)
def _recent_transitions(limit: int = 10) -> pd.DataFrame:
    sess = _session()
    rows = sess.execute(
        text(
            """
            SELECT ts, payload, confidence
              FROM signals
             WHERE signal_type = 'VOL_REGIME_TRANSITION' AND symbol = 'INDEX'
             ORDER BY ts DESC
             LIMIT :n
            """
        ),
        {"n": limit},
    ).mappings().all()
    if not rows:
        return pd.DataFrame()
    out = []
    for r in rows:
        p = r["payload"] or {}
        out.append(
            {
                "date": pd.to_datetime(r["ts"]).date(),
                "from": p.get("prior_label"),
                "to": p.get("label"),
                "vsi": p.get("vsi"),
                "confidence": r["confidence"],
            }
        )
    return pd.DataFrame(out)


@st.cache_data(ttl=300)
def _history(lookback_days: int = 252) -> pd.DataFrame:
    sess = _session()
    rows = sess.execute(
        text(
            """
            SELECT date,
                   voli, sdex, tdex,
                   vix_term_9d_30d, vix_options_richness,
                   vvix_vix_ratio, vix_spx_beta_60d
              FROM index_skew_daily
             ORDER BY date DESC
             LIMIT :n
            """
        ),
        {"n": lookback_days},
    ).mappings().all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows]).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    return df


# ── Palette + helpers ──────────────────────────────────────────────────

_SEVERITY_COLOR = {
    "extreme":  "#ef4444",   # vivid red
    "elevated": "#f59e0b",   # amber
    "normal":   "#6b7280",   # slate
    "low":      "#22c55e",   # green
    "calm":     "#10b981",   # deeper green
    "unknown":  "#374151",
}

# Cards keep a near-black surface; the severity colour shows through the
# left accent stripe + badge + z-bar, not the body wash. Keeps text high-
# contrast and readable across the desk.
_SEVERITY_BG = {
    "extreme":  "#0f1115",
    "elevated": "#0f1115",
    "normal":   "#0f1115",
    "low":      "#0f1115",
    "calm":     "#0f1115",
    "unknown":  "#0f1115",
}

# Inset top glow per severity — pure decoration above the text.
_SEVERITY_GLOW = {
    "extreme":  "rgba(239,68,68,0.20)",
    "elevated": "rgba(245,158,11,0.18)",
    "normal":   "rgba(107,114,128,0.12)",
    "low":      "rgba(34,197,94,0.16)",
    "calm":     "rgba(16,185,129,0.18)",
    "unknown":  "rgba(55,65,81,0.10)",
}

_DIMENSION_ACCENT = {
    "LEVEL": "#60a5fa",     # sky blue
    "SKEW":  "#a78bfa",     # violet
    "TAIL":  "#f472b6",     # pink
    "TERM":  "#fbbf24",     # amber/gold
    "VVOL":  "#34d399",     # emerald
}

# Two-tone regime palette: a brighter "label" color (used on dark backdrop
# for max contrast) and a softer "border/glow" tone.
_REGIME_COLOR = {
    "CRASH_HEDGING":     "#fca5a5",   # bright label on dark
    "TERM_STRESS_FLIP":  "#fdba74",
    "VOL_CRUSH_SETUP":   "#93c5fd",
    "STEALTH_STRESS":    "#fcd34d",
    "COMPLACENT":        "#86efac",   # high-contrast bright green
    "MIXED":             "#d1d5db",
}

_REGIME_BORDER = {
    "CRASH_HEDGING":     "#dc2626",
    "TERM_STRESS_FLIP":  "#ea580c",
    "VOL_CRUSH_SETUP":   "#3b82f6",
    "STEALTH_STRESS":    "#f59e0b",
    "COMPLACENT":        "#22c55e",
    "MIXED":             "#6b7280",
}

# Card background stays near-black for text contrast. The regime color shows
# only via the left border + label pill + a subtle glow, never as a body wash.
_REGIME_BG = {
    "CRASH_HEDGING":     "#0f1115",
    "TERM_STRESS_FLIP":  "#0f1115",
    "VOL_CRUSH_SETUP":   "#0f1115",
    "STEALTH_STRESS":    "#0f1115",
    "COMPLACENT":        "#0f1115",
    "MIXED":             "#0f1115",
}

# A faint regime-tinted glow on the top edge — pure decoration, doesn't sit
# behind any text.
_REGIME_GLOW = {
    "CRASH_HEDGING":     "rgba(220,38,38,0.18)",
    "TERM_STRESS_FLIP":  "rgba(234,88,12,0.18)",
    "VOL_CRUSH_SETUP":   "rgba(59,130,246,0.18)",
    "STEALTH_STRESS":    "rgba(245,158,11,0.18)",
    "COMPLACENT":        "rgba(34,197,94,0.18)",
    "MIXED":             "rgba(107,114,128,0.14)",
}


def _vsi_gauge(vsi: float | None) -> go.Figure:
    """Plotly indicator-style gauge for the composite Vol Stress Index."""
    value = vsi if vsi is not None else 0
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=value,
            number={"valueformat": ".0f"},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": "#9ca3af"},
                "bar": {"color": "#3b82f6", "thickness": 0.35},
                "bgcolor": "rgba(0,0,0,0)",
                "borderwidth": 0,
                "steps": [
                    {"range": [0, 20],   "color": "rgba(16,185,129,0.35)"},   # complacent zone
                    {"range": [20, 45],  "color": "rgba(34,197,94,0.25)"},
                    {"range": [45, 60],  "color": "rgba(107,114,128,0.25)"},  # neutral
                    {"range": [60, 80],  "color": "rgba(245,158,11,0.30)"},   # elevated
                    {"range": [80, 100], "color": "rgba(239,68,68,0.40)"},    # extreme
                ],
                "threshold": {
                    "line": {"color": "#f59e0b", "width": 3},
                    "thickness": 0.85,
                    "value": value,
                },
            },
            title={"text": "Vol Stress Index", "font": {"size": 16}},
        )
    )
    fig.update_layout(
        template="plotly_dark",
        height=240,
        margin={"l": 30, "r": 30, "t": 50, "b": 10},
    )
    return fig


def _z_bar(z: float | None) -> str:
    """Compact horizontal bar showing z-score on [-3, +3] scale (HTML)."""
    if z is None:
        return "<div style='height:6px; background:#374151; border-radius:3px;'></div>"
    z_clip = max(-3.0, min(3.0, float(z)))
    pct = (z_clip + 3.0) / 6.0 * 100.0
    # Colour by signed magnitude
    if z >= 1.0:
        fill = "#ef4444"
    elif z >= 0.5:
        fill = "#f59e0b"
    elif z <= -1.0:
        fill = "#10b981"
    elif z <= -0.5:
        fill = "#22c55e"
    else:
        fill = "#6b7280"
    return (
        "<div style='position:relative; height:8px; background:#1f2937; border-radius:4px;'>"
        f"<div style='position:absolute; left:50%; top:-2px; width:1px; height:12px; background:#9ca3af;'></div>"
        f"<div style='position:absolute; left:{pct:.1f}%; transform:translateX(-50%); top:-3px; "
        f"width:10px; height:14px; background:{fill}; border-radius:3px;'></div>"
        "</div>"
    )


def _format_pct(p: float | None) -> str:
    if p is None:
        return "—"
    rank = int(round(p * 100))
    return f"{rank}<sup>th</sup> pct"


def _format_value(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 100:
        return f"{v:,.1f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.3f}"


def _dimension_card(card: dict) -> None:
    """Render one dimension card. HTML must be flush-left or Streamlit's
    markdown treats it as a code block.
    """
    sev = card.get("severity", "unknown")
    color = _SEVERITY_COLOR.get(sev, "#374151")
    bg = _SEVERITY_BG.get(sev, "rgba(55,65,81,0.06)")
    accent = _DIMENSION_ACCENT.get(card.get("name", ""), "#60a5fa")
    z = card.get("z_score")
    z_str = "—" if z is None else f"{z:+.2f}"
    glow = _SEVERITY_GLOW.get(sev, "rgba(55,65,81,0.10)")
    html = (
        f'<div style="position:relative; padding:18px 20px; background:{bg}; '
        f'border-left:6px solid {accent}; '
        f'border-top:1px solid rgba(255,255,255,0.06); '
        f'border-radius:8px; min-height:210px; margin-bottom:8px; '
        f'box-shadow:0 3px 10px rgba(0,0,0,0.4), inset 0 1px 0 {glow};">'
        f'<div style="display:flex; justify-content:space-between; align-items:baseline;">'
        f'<span style="font-size:15px; color:{accent}; font-weight:800; '
        f'letter-spacing:0.06em;">{card["label"].upper()}</span>'
        f'<span style="font-size:12px; color:{color}; font-weight:800; '
        f'text-transform:uppercase; background:rgba(0,0,0,0.40); '
        f'padding:4px 10px; border-radius:10px; border:1px solid {color};">{sev}</span>'
        f'</div>'
        f'<div style="font-size:14px; color:#cbd5e1; margin-top:8px;">{card["metric_name"]}</div>'
        f'<div style="display:flex; justify-content:space-between; '
        f'align-items:baseline; margin-top:10px;">'
        f'<span style="font-size:32px; font-weight:800; color:#f9fafb;">'
        f'{_format_value(card["metric_value"])}</span>'
        f'<span style="font-size:14px; color:#cbd5e1;">z=<b style="color:{color}; '
        f'font-size:16px;">{z_str}</b> · {_format_pct(card["percentile"])}</span>'
        f'</div>'
        f'<div style="margin-top:12px;">{_z_bar(z)}</div>'
        f'<div style="font-size:15px; color:#f3f4f6; margin-top:12px; '
        f'line-height:1.5;">{card["description"]}</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _state_card(sig: dict) -> None:
    """State card. Built as one flush-left HTML string to avoid Streamlit
    treating indented HTML as a code block. Large, high-contrast typography
    so the regime label reads clearly across the desk.
    """
    payload = sig.get("payload") or {}
    label = payload.get("label", "MIXED")
    color = _REGIME_COLOR.get(label, "#d1d5db")           # bright label
    border = _REGIME_BORDER.get(label, "#6b7280")
    bg = _REGIME_BG.get(label, "rgba(107,114,128,0.10)")
    conf = sig.get("confidence")
    conf_pct = f"{(conf * 100):.0f}%" if conf is not None else "—"
    overlays = payload.get("overlays") or []
    bullets = _state_bullets(payload)

    overlay_chips = "".join(
        f'<span style="background:rgba(239,68,68,0.22); color:#fecaca; '
        f'padding:6px 14px; border-radius:14px; font-size:14px; '
        f'margin-left:12px; font-weight:700; letter-spacing:0.06em; '
        f'border:1px solid rgba(239,68,68,0.6);">{o}</span>'
        for o in overlays
    )

    bullet_html = "".join(
        f'<li style="margin-bottom:10px;">{b}</li>' for b in bullets
    ) or '<li>No primary driver above the cutoff thresholds.</li>'

    glow = _REGIME_GLOW.get(label, "rgba(107,114,128,0.14)")
    parts = [
        f'<div style="position:relative; padding:24px 28px; background:{bg}; '
        f'border-left:10px solid {border}; '
        f'border-top:1px solid rgba(255,255,255,0.06); '
        f'border-radius:10px; margin-bottom:16px; '
        f'box-shadow:0 6px 20px rgba(0,0,0,0.45), inset 0 1px 0 {glow};">',
        # Top row: label backdrop chip + overlays + confidence pill.
        '<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">',
        # Label: bright color text on a dark backdrop pill = maximum contrast.
        f'<span style="display:inline-block; background:rgba(0,0,0,0.55); '
        f'padding:10px 22px; border-radius:10px; border:2px solid {border}; '
        f'font-size:44px; font-weight:900; color:{color}; '
        f'letter-spacing:0.04em; line-height:1.0; '
        f'text-shadow:0 0 22px {border}80;">{label}</span>',
        overlay_chips,
        f'<span style="margin-left:auto; font-size:15px; color:#e5e7eb; '
        f'background:rgba(0,0,0,0.40); padding:8px 14px; border-radius:12px; '
        f'border:1px solid rgba(255,255,255,0.08);">'
        f'Confidence <b style="color:{color}; font-size:17px;">{conf_pct}</b> · '
        f'<span style="color:#9ca3af;">As of</span> '
        f'<b style="color:#f9fafb;">{pd.to_datetime(sig["ts"]).date()}</b>'
        '</span>',
        '</div>',
        # "What's happening" header — larger, brighter.
        '<div style="margin-top:18px; font-size:14px; color:#cbd5e1; '
        'text-transform:uppercase; letter-spacing:0.10em; font-weight:700;">'
        "What's happening</div>",
        # Bullets — 16px, near-white for readability.
        '<ul style="font-size:16px; color:#f3f4f6; margin:10px 0 4px 24px; '
        'line-height:1.65;">',
        bullet_html,
        '</ul>',
        # Rationale footer — 13px (was 11), brighter grey.
        '<div style="margin-top:12px; font-size:13px; color:#9ca3af; '
        'font-family:ui-monospace, SFMono-Regular, monospace; '
        'background:rgba(0,0,0,0.30); padding:8px 12px; border-radius:5px;">',
        payload.get("rationale", ""),
        '</div>',
        '</div>',
    ]
    st.markdown("".join(parts), unsafe_allow_html=True)


def _state_bullets(payload: dict) -> list[str]:
    """Plain-English bullets pulled from the dimension descriptions.

    Picks the dimensions whose |z| ≥ 0.5 — the ones actually carrying the read.
    """
    cards = payload.get("cards", [])
    bullets: list[str] = []
    for c in cards:
        z = c.get("z_score")
        if z is None:
            continue
        if abs(z) < 0.5:
            continue
        bullets.append(f"<b>{c['label']}</b>: {c['description']}")
    return bullets


# ── Sparkline panel ────────────────────────────────────────────────────


def _line_chart(df: pd.DataFrame, columns: list[str], *, height: int = 200) -> None:
    if df.empty:
        st.info("No history yet.")
        return
    chart_df = df.set_index("date")[columns].dropna(how="all")
    if chart_df.empty:
        st.info("No history for this dimension.")
        return
    st.line_chart(chart_df, height=height)


# ── Page ───────────────────────────────────────────────────────────────


def main() -> None:
    st.title("Vol Regime — 5-dimension VIX decomposition")
    st.caption(
        "Strategy: `strategies/vol_regime.py` · Inputs: `index_skew_daily` (Nations + "
        "VIX-decomposition cols) · Math: `vol/vix_regime.py`."
    )

    sig = _latest_signal()
    if sig is None:
        st.warning(
            "No `INDEX_VOL_REGIME` signal yet. Run "
            "`python -m trading_intel.scheduler.jobs.vol_regime`."
        )
        return

    payload = sig.get("payload") or {}
    if payload.get("experimental"):
        st.caption(
            ":warning: **Experimental** — rules not yet backtest-validated; "
            "use as a sizing input, not a trade gate."
        )

    # ── Top row: VSI gauge + state card ───────────────────────────
    col_gauge, col_state = st.columns([1, 2.2])
    with col_gauge:
        st.plotly_chart(_vsi_gauge(payload.get("vsi")), use_container_width=True)
    with col_state:
        _state_card(sig)

    # ── Decomposition strip: 5 dimension cards ────────────────────
    st.markdown(
        '<div style="margin:18px 0 10px; display:flex; align-items:center; gap:10px;">'
        '<span style="font-size:11px; color:#9ca3af; letter-spacing:0.12em; '
        'text-transform:uppercase; font-weight:700;">Decomposition</span>'
        '<span style="flex:1; height:1px; background:linear-gradient(to right, '
        '#60a5fa, #a78bfa, #f472b6, #fbbf24, #34d399); opacity:0.5;"></span>'
        '<span style="font-size:11px; color:#6b7280;">what every dimension is saying</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    cards = payload.get("cards", [])
    if cards:
        cols = st.columns(len(cards))
        for col, card in zip(cols, cards, strict=False):
            with col:
                _dimension_card(card)

    # ── History section ───────────────────────────────────────────
    st.markdown("---")
    st.markdown(
        '<div style="margin:6px 0 12px; font-size:11px; color:#9ca3af; '
        'letter-spacing:0.12em; text-transform:uppercase; font-weight:700;">'
        'Trailing history</div>',
        unsafe_allow_html=True,
    )
    lookback = st.slider(
        "Lookback (trading days)",
        min_value=63, max_value=504, value=252, step=21,
    )
    hist = _history(lookback_days=lookback)
    if hist.empty:
        st.info("No `index_skew_daily` history yet — run the backfill.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**LEVEL** — VOLI (ATM IV)")
            _line_chart(hist, ["voli"])
            st.markdown("**TAIL** — TDEX (deep-OTM put cost)")
            _line_chart(hist, ["tdex"])
            st.markdown("**VOL-OF-VOL** — VIX-options richness")
            _line_chart(hist, ["vix_options_richness"])
        with c2:
            st.markdown("**SKEW** — SDEX")
            _line_chart(hist, ["sdex"])
            st.markdown("**TERM** — VIX9D − VIX (positive = backwardation)")
            _line_chart(hist, ["vix_term_9d_30d"])
            st.markdown("**SPX-VIX β (60d)**")
            _line_chart(hist, ["vix_spx_beta_60d"])

    # ── Transitions ───────────────────────────────────────────────
    st.markdown("---")
    st.markdown("##### Recent regime transitions")
    trans = _recent_transitions(limit=15)
    if trans.empty:
        st.info("No transitions yet — emitted only when today's label differs from prior.")
    else:
        st.dataframe(
            trans,
            column_config={
                "date": st.column_config.DateColumn("Date"),
                "from": st.column_config.TextColumn("From"),
                "to": st.column_config.TextColumn("To"),
                "vsi": st.column_config.NumberColumn("VSI", format="%.0f"),
                "confidence": st.column_config.NumberColumn("Conf.", format="%.2f"),
            },
            hide_index=True,
            use_container_width=True,
        )

    st.caption(
        "States: `CRASH_HEDGING` · `TERM_STRESS_FLIP` · `VOL_CRUSH_SETUP` · "
        "`STEALTH_STRESS` · `COMPLACENT` · `MIXED`. Overlay tag: "
        "`VIX_OPTIONS_RICH` when z(VVOL) ≥ 1.5."
    )


main()
