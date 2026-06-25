"""Server-side chart rendering (matplotlib → PNG bytes).

Shared by BOTH the web UI (/api/charts/*.png) and the scheduled Telegram reports
(app.reports), so there's one chart implementation. Rendering is fully local — no
external chart service ever sees your financial data.
"""
from __future__ import annotations

import io
import sqlite3
from datetime import date

import matplotlib
matplotlib.use("Agg")  # headless: render to a buffer, never open a window
# Render literal "$" in labels instead of treating paired $...$ as LaTeX math.
matplotlib.rcParams["text.parse_math"] = False
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from . import budget_engine, goal_engine

# Dark palette matching web/styles.css
_BG, _PANEL = "#181b22", "#1f242d"
_TEXT, _MUTED = "#e6e9ef", "#8b93a3"
_GREEN, _RED, _ACCENT, _AMBER = "#34c77b", "#ff5d5d", "#4f8cff", "#f0b429"


def _fig(w: float = 8.0, h: float = 4.5):
    fig, ax = plt.subplots(figsize=(w, h), dpi=110)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_PANEL)
    for s in ax.spines.values():
        s.set_color(_MUTED)
    ax.tick_params(colors=_MUTED)
    ax.title.set_color(_TEXT)
    ax.title.set_fontsize(13)
    return fig, ax


def _money(x, _pos=None):
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def _legend(ax):
    ax.legend(facecolor=_PANEL, edgecolor=_MUTED, labelcolor=_TEXT, fontsize=8)


def _empty(ax, title, msg):
    ax.text(0.5, 0.5, msg, ha="center", va="center", color=_MUTED,
            transform=ax.transAxes, fontsize=10)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])


def _render(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def goals_chart(conn: sqlite3.Connection) -> bytes:
    """Horizontal progress bars, one per goal (current vs target)."""
    goals = goal_engine.project(conn)["goals"]
    active = [g for g in goals if not g["complete"]] or goals
    fig, ax = _fig(8.0, max(2.6, 0.8 * len(active) + 1.3))
    if not active:
        _empty(ax, "Goal progress", "No goals yet — add some on the Goals tab.")
        return _render(fig)
    pct = [min(100.0, (g["current_amount"] / g["target_amount"] * 100.0)
                if g["target_amount"] > 0 else 0.0) for g in active]
    labels = [f"{g['name']}\n${g['current_amount']:,.0f} of ${g['target_amount']:,.0f}"
              for g in active]
    y = list(range(len(active)))
    ax.barh(y, [100] * len(active), color=_BG, edgecolor=_MUTED, height=0.55)
    ax.barh(y, pct, color=_ACCENT, height=0.55)
    for i, p in enumerate(pct):
        ax.text(min(98, p + 1.5), i, f"{p:.0f}%", va="center", ha="left",
                color=_TEXT, fontsize=9, clip_on=False)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, color=_TEXT, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("% to target", color=_MUTED)
    ax.set_title("Goal progress")
    return _render(fig)


def networth_chart(conn: sqlite3.Connection) -> bytes:
    """Net worth (+ assets / liabilities) over time from daily snapshots."""
    hist = budget_engine.net_worth_history(conn)
    fig, ax = _fig(8.0, 4.5)
    if len(hist) < 2:
        have = "1 day recorded so far" if hist else "no data yet"
        _empty(ax, "Net worth over time",
               f"Net-worth history builds from daily snapshots ({have}).\n"
               "Check back as more days are recorded.")
        return _render(fig)
    dates = [h["snapshot_date"] for h in hist]
    x = list(range(len(dates)))
    ax.plot(x, [h["net_worth"] for h in hist], color=_ACCENT, marker="o",
            linewidth=2, label="Net worth")
    ax.plot(x, [h["assets"] for h in hist], color=_GREEN, linewidth=1,
            linestyle="--", label="Assets")
    ax.plot(x, [h["liabilities"] for h in hist], color=_RED, linewidth=1,
            linestyle="--", label="Liabilities")
    ax.axhline(0, color=_MUTED, linewidth=0.6)
    step = max(1, len(dates) // 8)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(dates[::step], rotation=45, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(FuncFormatter(_money))
    ax.set_title("Net worth over time")
    _legend(ax)
    return _render(fig)


def cashflow_chart(conn: sqlite3.Connection, months: int = 6,
                   include_current: bool = True) -> bytes:
    """Monthly income vs. expenses bars + a net line."""
    # Pull an extra month so we can drop the current one and still show `months`.
    data = budget_engine.cash_flow(conn, months=months + 1)
    if not include_current:
        this_month = date.today().strftime("%Y-%m")
        data = [d for d in data if d["month"] != this_month]
    data = data[-months:] if months else data
    fig, ax = _fig(8.0, 4.5)
    if not data:
        _empty(ax, "Monthly cash flow", "No transactions yet.")
        return _render(fig)
    labels = [d["month"] for d in data]
    x = list(range(len(data)))
    w = 0.4
    ax.bar([i - w / 2 for i in x], [d["income"] for d in data], w,
           color=_GREEN, label="Income")
    ax.bar([i + w / 2 for i in x], [d["expenses"] for d in data], w,
           color=_RED, label="Expenses")
    ax.plot(x, [d["net"] for d in data], color=_ACCENT, marker="o",
            linewidth=1.6, label="Net")
    ax.axhline(0, color=_MUTED, linewidth=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(_money))
    title = "Monthly cash flow" + ("" if include_current else " (through last month)")
    ax.set_title(title)
    _legend(ax)
    return _render(fig)
