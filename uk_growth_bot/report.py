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

from . import universe as U
from .config import settings
from .ledger import Ledger
from .ml_model import ModelReport
from .planner import Research
from .research import regime_text, top_candidates
from .tax import TaxPosition, pool_cost_basis, rates_for, tax_year_bounds

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


def benchmark_value(closes: pd.DataFrame, flows: List[tuple], ticker: str = U.REGIME_INDEX) -> Optional[float]:
    """Value today had every contribution gone straight into *ticker*."""
    if ticker not in closes or not flows:
        return None
    s = closes[ticker].dropna()
    units = 0.0
    for d, amount, kind in flows:
        if kind not in ("contribution", "withdrawal"):
            continue
        px = s[s.index <= pd.Timestamp(d)]
        if px.empty:
            px = s.iloc[:1]
        units += amount / float(px.iloc[-1])
    return units * float(s.iloc[-1])


def projection(nav: float, monthly: float, annual_rate: float, years: int) -> float:
    r = (1 + annual_rate) ** (1 / 12) - 1
    n = years * 12
    return nav * (1 + r) ** n + monthly * (((1 + r) ** n - 1) / r)


def build(today: date, ledger: Ledger, prices: Dict[str, float], targets: Dict[str, float],
          research: Research, tax: TaxPosition, model: Optional[ModelReport],
          closes: pd.DataFrame) -> Dict[str, str]:
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

    rows: List[Holding] = []
    for t, q in sorted(holdings.items(), key=lambda kv: -kv[1] * prices.get(kv[0], 0)):
        p = prices.get(t, 0.0)
        _, cost = pool_cost_basis(txns, t)
        slot = U.core_slot(t) or t
        rows.append(Holding(t, U.ALL[t].name if t in U.ALL else t, q, p, q * p,
                            q * p / nav if nav else 0, targets.get(slot, 0.0), cost))

    # Executed trades are listed from the ledger; keep only the commentary.
    decisions = [d for d in ledger.decisions(week_ago) if not d["text"].startswith(("BUY ", "SELL "))]
    trades = ledger.txn_rows(week_ago)
    cands = top_candidates(research)
    next_contrib = date(today.year, today.month, settings.contribution_day)
    if today.day >= settings.contribution_day:
        next_contrib = date(today.year + (today.month == 12), today.month % 12 + 1, settings.contribution_day)

    def money(x: float) -> str:
        return f"£{x:,.2f}" if x >= 0 else f"-£{-x:,.2f}"

    lines = [
        f"UK GROWTH FUND — WEEKLY REPORT ({today.isoformat()}, {settings.mode_label})",
        "=" * 64,
        f"Fund value:          {money(nav)}  (cash {money(cash)})",
        f"Total contributed:   {money(contributed)}",
        f"Growth:              {money(gain)} ({gain / contributed:+.1%})" if contributed else "Growth: n/a",
        f"This week:           {money(week_change)} (excl. new contributions)" if week_change is not None else None,
        f"Benchmark:           {money(bench)} if all contributions had gone into {U.REGIME_INDEX}" if bench else None,
        f"Market regime:       {regime_text(research)}",
        "", "HOLDINGS", "-" * 64,
    ]
    held_at_loss = ledger.get_state("held_at_loss", {}) or {}
    for h in rows:
        lines.append(f"{h.ticker:<8} {h.quantity:>6g} x {money(h.price):>9} = {money(h.value):>11}  "
                     f"weight {h.weight:5.1%} (target {h.target:5.1%})  gain {money(h.gain)}"
                     + ("  *" if h.ticker in held_at_loss else ""))
    if not rows:
        lines.append("No holdings yet.")
    if any(h.ticker in held_at_loss for h in rows):
        lines.append("* Due to be sold but below its average cost: kept, no new money, "
                     "sold once it recovers.")
        lines += [f"  {t}: {why}" for t, why in held_at_loss.items() if t in holdings]
    lines += ["", "ACTIVITY THIS WEEK", "-" * 64]
    lines += [f"{t['day']} {t['side']} {t['quantity']:g} {t['symbol']} @ {money(t['price'])} "
              f"(fees {money(t['fees'])}) — {t['reason']}" for t in trades] or ["No trades."]
    lines += [f"{d['day']} note: {d['text']}" for d in decisions]
    lines += ["", "RESEARCH — TOP GROWTH CANDIDATES", "-" * 64]
    for t in cands:
        lines.append(f"{t:<8} score {research.scores[t]:+.2f} (momentum {research.momentum.get(t, 0):+.2f}, "
                     f"ML {research.ml.get(t, 0):+.2f}, news {research.sentiment.get(t, 0):+.2f})")
    if model:
        lines.append(f"ML model: out-of-sample AUC {model.auc:.3f}, accuracy {model.accuracy:.1%}, "
                     f"{'ACTIVE' if model.active else 'NOT USED (below quality bar)'}; trained {model.trained_at}")
    else:
        lines.append("ML model: not trained yet — decisions use momentum and news only.")
    if settings.is_isa:
        start, end = tax_year_bounds(tax.tax_year)
        subscribed = sum(a for d, a, k in ledger.cash_flows() if k == "contribution" and start <= d <= end)
        isa_limit = rates_for(tax.tax_year)["isa_allowance"]
        lines += [
            "", f"UK TAX — {tax.label} (Stocks & Shares ISA)", "-" * 64,
            "Gains and dividends are tax-free; nothing to declare on Self Assessment.",
            f"ISA subscriptions this tax year: {money(subscribed)} of {money(isa_limit)}"
            + (" — NEARLY FULL, extra money would need a GIA." if subscribed > 0.9 * isa_limit else "."),
        ]
    else:
        lines += [
            "", f"UK TAX — {tax.label} (General Investment Account, estimates)", "-" * 64,
            f"Realised gains {money(tax.realised_gains)}, losses {money(tax.realised_losses)}, "
            f"net {money(tax.net_gains)}",
            f"CGT allowance {money(tax.allowance)}; remaining {money(tax.allowance_remaining)}; "
            f"estimated CGT {money(tax.estimated_cgt)} at {tax.cgt_rate:.0%}",
            f"Dividends {money(tax.dividends)} of {money(tax.dividend_allowance)} allowance; "
            f"estimated dividend tax {money(tax.estimated_dividend_tax)}",
            "Self Assessment: " + ("REPORT these disposals on your return." if tax.must_report
                                   else "nothing to report on current figures."),
            "Net loss: claim it on Self Assessment (within 4 years) to carry it forward against future gains."
            if tax.net_gains < 0 else None,
            "Accumulating ETFs: the fund's 'excess reportable income' is taxable dividend income "
            "even though no cash is paid — check each fund's annual report.",
        ]
    lines += [
        "", "OUTLOOK", "-" * 64,
        f"Next contribution: {money(settings.monthly_contribution)} on/after {next_contrib.isoformat()}",
        "Illustration only (not a forecast) — value in 10 years at £{:,.0f}/month: "
        "5%/yr {} · 7%/yr {} · 9%/yr {}".format(
            settings.monthly_contribution, *(money(projection(nav, settings.monthly_contribution, r, 10))
                                             for r in (0.05, 0.07, 0.09))),
        "", "Automated investing bot. Not financial or tax advice. Capital at risk.",
    ]
    text = "\n".join(l for l in lines if l is not None)
    html_body = "<html><body style='font-family:Arial,sans-serif;max-width:720px;margin:auto'>" \
                + "<pre style='white-space:pre-wrap;font-size:13px'>" + html.escape(text) + "</pre></body></html>"
    subject = f"UK Growth Fund weekly: {money(nav)} ({money(gain)} growth)"
    return {"subject": subject, "text": text, "html": html_body}


def send(report: Dict[str, str]) -> bool:
    sender = os.getenv("GMAIL_ADDRESS", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    to = os.getenv("UK_REPORT_RECIPIENT", "").strip() or os.getenv("REPORT_RECIPIENT", "").strip() or sender
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
