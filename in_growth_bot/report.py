"""Weekly fund report (HTML + plain text email)."""

from __future__ import annotations

import html
import logging
import os
import smtplib
from dataclasses import dataclass
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

import pandas as pd

from . import market_data
from . import universe as U
from . import uk_tax
from .config import settings
from .ledger import Ledger
from .ml_model import ModelReport
from .planner import Research
from .research import regime_text, top_candidates
from .tax import cost_basis, tax_position

logger = logging.getLogger(__name__)


@dataclass
class Holding:
    ticker: str
    name: str
    quantity: float
    price: float
    value: float
    weight: float
    target: float
    cost: float

    @property
    def gain(self) -> float:
        return self.value - self.cost


def benchmark_value(closes: pd.DataFrame, flows: List[tuple], ticker: str = U.BENCHMARK) -> Optional[float]:
    """What the contributions would be worth had each gone into *ticker*."""
    if ticker not in closes or not flows:
        return None
    s = closes[ticker].dropna()
    if s.empty:
        return None
    units = 0.0
    for day, amount, kind in flows:
        if kind not in ("contribution", "withdrawal"):
            continue
        px = s[s.index <= pd.Timestamp(day)]
        px = px.iloc[-1] if not px.empty else s.iloc[0]
        units += amount / px
    return units * float(s.iloc[-1])


def projection(nav: float, monthly: float, annual_rate: float, years: int) -> float:
    r = (1 + annual_rate) ** (1 / 12) - 1
    n = years * 12
    return nav * (1 + r) ** n + monthly * (((1 + r) ** n - 1) / r)


def _uk_dividends_gbp(ledger: Ledger, today: date, fx: float) -> float:
    """Estimated dividends this UK tax year (they're paid to your bank, not
    to Zerodha, so the bot never sees the cash)."""
    start, end = uk_tax.tax_year_bounds(uk_tax.tax_year_of(today))
    total = 0.0
    for t, qty in ledger.holdings().items():
        for ex, dps in market_data.dividends_per_share(t, pd.Timestamp(start)).items():
            if start <= ex.date() <= min(end, today):
                total += dps * qty
    return total / fx if fx else 0.0


def build(today: date, ledger: Ledger, prices: Dict[str, float], targets: Dict[str, float],
          research: Research, model: Optional[ModelReport], closes: pd.DataFrame) -> Dict[str, str]:
    txns = ledger.txns()
    holdings = ledger.holdings()
    cash = ledger.cash()
    nav = cash + sum(q * prices.get(t, 0.0) for t, q in holdings.items())
    contributed = ledger.net_contributions()
    gain = nav - contributed
    week_ago = today - timedelta(days=7)
    hist = [h for h in ledger.nav_history() if h["day"] <= week_ago.isoformat()]
    week_change = None
    if hist:
        prev = hist[-1]
        week_change = (nav - prev["nav"]) - (contributed - prev["net_contributions"])
    bench = benchmark_value(closes, ledger.cash_flows())
    fx = market_data.fx_rate(closes)

    rows: List[Holding] = []
    for t, q in sorted(holdings.items(), key=lambda kv: -kv[1] * prices.get(kv[0], 0)):
        p = prices.get(t, 0.0)
        _, cost = cost_basis(txns, t)
        rows.append(Holding(t, U.ALL[t].name if t in U.ALL else t, q, p, q * p,
                            q * p / nav if nav else 0, targets.get(t, 0.0), cost))

    decisions = [d for d in ledger.decisions(week_ago) if not d["text"].startswith(("BUY ", "SELL "))]
    trades = ledger.txn_rows(week_ago)
    cands = top_candidates(research)
    nxt = date(today.year, today.month, settings.contribution_day)
    if today.day >= settings.contribution_day:
        nxt = date(today.year + (today.month == 12), today.month % 12 + 1, settings.contribution_day)

    def money(x: float) -> str:
        return f"Rs{x:,.0f}" if x >= 0 else f"-Rs{-x:,.0f}"

    def gbp(x: float) -> str:
        return f"£{x:,.0f}" if x >= 0 else f"-£{-x:,.0f}"

    lines = [
        f"INDIA GROWTH FUND — WEEKLY REPORT ({today.isoformat()}, {settings.mode_label})",
        "=" * 64,
        f"Fund value:          {money(nav)}  (cash {money(cash)})" + (f"  ≈ {gbp(nav / fx)}" if fx else ""),
        f"Total contributed:   {money(contributed)}",
        f"Growth:              {money(gain)} ({gain / contributed:+.1%})" if contributed else "Growth: n/a",
        f"This week:           {money(week_change)} (excl. new contributions)" if week_change is not None else None,
        f"Benchmark:           {money(bench)} if all contributions had gone into {U.BENCHMARK}" if bench else None,
        f"Market regime:       {regime_text(research)}",
        "", "HOLDINGS", "-" * 64,
    ]
    held_at_loss = ledger.get_state("held_at_loss", {}) or {}
    for h in rows:
        lines.append(f"{h.ticker:<14} {h.quantity:>5g} x {money(h.price):>9} = {money(h.value):>10}  "
                     f"weight {h.weight:5.1%} (target {h.target:5.1%})  gain {money(h.gain)}"
                     + ("  *" if h.ticker in held_at_loss else ""))
    if not rows:
        lines.append("No holdings yet.")
    if any(h.ticker in held_at_loss for h in rows):
        lines.append("* Due to be sold but below its average cost: kept, no new money, sold once it recovers.")
        lines += [f"  {t}: {why}" for t, why in held_at_loss.items() if t in holdings]
    lines += ["", "ACTIVITY THIS WEEK", "-" * 64]
    lines += [f"{t['day']} {t['side']} {t['quantity']:g} {t['symbol']} @ {money(t['price'])} "
              f"(charges {money(t['fees'])}) — {t['reason']}" for t in trades] or ["No trades."]
    lines += [f"{d['day']} note: {d['text']}" for d in decisions]
    lines += ["", "RESEARCH — TOP RANKED SHARES", "-" * 64]
    for t in cands:
        lines.append(f"{t:<14} score {research.scores[t]:+.2f} (momentum {research.momentum.get(t, 0):+.2f}, "
                     f"ML {research.ml.get(t, 0):+.2f}, news {research.sentiment.get(t, 0):+.2f})")
    if model:
        lines.append(f"ML model: out-of-sample AUC {model.auc:.3f}, accuracy {model.accuracy:.1%}, "
                     f"{'ACTIVE' if model.active else 'NOT USED (below quality bar)'}; trained {model.trained_at}")
    else:
        lines.append("ML model: not trained yet — decisions use momentum and news only.")

    tin = tax_position(txns, today)
    lines += [
        "", f"INDIAN TAX — {tin.label} (estimates)", "-" * 64,
        f"Short-term gains (held ≤12 months, {tin.stcg_rate:.0%}): {money(tin.st_gains)}",
        f"Long-term gains (held >12 months, {tin.ltcg_rate:.1%} above {money(tin.exemption)}): "
        f"{money(tin.lt_gains)}; exemption left {money(tin.exemption_remaining)}",
        f"Estimated Indian tax incl. {tin.cess:.0%} cess: {money(tin.estimated_tax)}. As an NRI it is deducted "
        "at source (TDS) when you sell; file an ITR to claim back any excess.",
    ]
    if fx:
        gtx = uk_tax.to_gbp(txns, fx)
        ukp = uk_tax.tax_position(gtx, [(today, _uk_dividends_gbp(ledger, today, fx))], today)
        lines += [
            "", f"UK TAX — {ukp.label} (you're UK resident; GBP estimates at each trade's exchange rate)", "-" * 64,
            f"Gains {gbp(ukp.realised_gains)}, losses {gbp(ukp.realised_losses)}, net {gbp(ukp.net_gains)} "
            f"against the {gbp(ukp.allowance)} CGT allowance (shared with any other UK gains)",
            f"UK CGT before foreign tax credit: {gbp(ukp.estimated_cgt)} at {ukp.cgt_rate:.0%}; "
            "Indian tax on the same gains is credited (India-UK DTAA)",
            f"Estimated dividends (paid to your bank): {gbp(ukp.dividends)}; UK dividend allowance "
            f"{gbp(ukp.dividend_allowance)}",
            "Self Assessment: declare foreign gains and dividends (SA106/SA108)"
            + (" — REPORTABLE on current figures." if ukp.must_report or ukp.dividends > ukp.dividend_allowance
               else "; nothing reportable on current figures."),
        ]
    if settings.include_etfs:
        lines.append("ETFs are enabled: Indian ETFs aren't UK reporting funds, so their gains are taxed in "
                     "the UK as income, not CGT.")
    lines += [
        "", "OUTLOOK", "-" * 64,
        f"Next contribution: {money(settings.monthly_contribution)} on/after {nxt.isoformat()}"
        + (" — transfer it into Zerodha." if settings.uses_broker else " (simulated)."),
        "Illustration only (not a forecast) — value in 10 years at {}/month: "
        "8%/yr {} · 11%/yr {} · 14%/yr {}".format(
            money(settings.monthly_contribution), *(money(projection(nav, settings.monthly_contribution, r, 10))
                                                    for r in (0.08, 0.11, 0.14))),
        "", "Automated investing bot. Not financial or tax advice. Capital at risk.",
    ]
    text = "\n".join(l for l in lines if l is not None)
    html_body = "<html><body style='font-family:Arial,sans-serif;max-width:720px;margin:auto'>" \
                + "<pre style='white-space:pre-wrap;font-size:13px'>" + html.escape(text) + "</pre></body></html>"
    subject = f"India Growth Fund weekly: {money(nav)} ({money(gain)} growth)"
    return {"subject": subject, "text": text, "html": html_body}


def send(report: Dict[str, str]) -> bool:
    sender = os.getenv("GMAIL_ADDRESS", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    to = (os.getenv("IN_REPORT_RECIPIENT", "").strip() or os.getenv("UK_REPORT_RECIPIENT", "").strip()
          or os.getenv("REPORT_RECIPIENT", "").strip() or sender)
    if not (sender and password and to):
        logger.warning("Email not configured; report written to disk only.")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = report["subject"], sender, to
    msg.attach(MIMEText(report["text"], "plain", "utf-8"))
    msg.attach(MIMEText(report["html"], "html", "utf-8"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.starttls()
            s.login(sender, password)
            s.sendmail(sender, [to], msg.as_string())
        logger.info("Weekly report emailed to %s", to)
        return True
    except Exception as exc:
        logger.error("Sending weekly report failed: %s", exc)
        return False
