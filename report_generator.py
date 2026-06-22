"""
report_generator.py
===================
Generates the end-of-day trading report from trades.csv and session state.
Produces an HTML email body and a plain-text summary.
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class EODReportGenerator:
    """
    Generates the end-of-day trading report.

    Usage
    -----
    >>> gen = EODReportGenerator()
    >>> report = gen.generate(
    ...     session_date='2026-06-08',
    ...     portfolio_summary={'portfolio_value': 100_000, 'cash': 50_000, ...},
    ...     performance={'total_pnl': 250.0, 'win_rate': 66.7, 'num_trades': 3, ...},
    ...     trades_csv_path='trading_agent/trades.csv',
    ... )
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_today_trades(
        self, trades_csv_path: str, session_date: str
    ) -> List[Dict]:
        """
        Read trades.csv and return only rows matching *session_date*.

        Returns an empty list if the file does not exist or has no rows
        for today.

        Expected CSV columns (as written by PortfolioTracker):
            date, time, symbol, action, quantity, price, pnl, exit_reason
        """
        if not os.path.exists(trades_csv_path):
            logger.info(
                "trades.csv not found at %s — reporting 'no trades'.", trades_csv_path
            )
            return []

        trades: List[Dict] = []
        try:
            with open(trades_csv_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    # Support both 'date' and 'timestamp' column names.
                    row_date = (
                        row.get("date", "")
                        or row.get("timestamp", "")[:10]
                    ).strip()
                    if row_date == session_date:
                        trades.append(row)
        except Exception as exc:
            logger.error(
                "Error reading trades.csv: %s", exc, exc_info=True
            )

        return trades

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        """Safely coerce *value* to float."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _compute_win_rate(self, trades: List[Dict]) -> float:
        """Return win-rate as a percentage for SELL trades with a P&L column."""
        sell_trades = [
            t for t in trades if str(t.get("action", "")).upper() == "SELL"
        ]
        if not sell_trades:
            return 0.0
        winners = sum(
            1
            for t in sell_trades
            if self._safe_float(t.get("pnl", 0)) > 0
        )
        return round(winners / len(sell_trades) * 100, 1)

    def _compute_total_pnl(self, trades: List[Dict]) -> float:
        """Sum P&L across all SELL trades for the session."""
        return round(
            sum(
                self._safe_float(t.get("pnl", 0))
                for t in trades
                if str(t.get("action", "")).upper() == "SELL"
            ),
            2,
        )

    # ------------------------------------------------------------------
    # HTML generation
    # ------------------------------------------------------------------

    def _build_html(
        self,
        session_date: str,
        portfolio_summary: dict,
        performance: dict,
        trades: List[Dict],
        total_pnl: float,
        win_rate: float,
        daily_loss_pct: float,
    ) -> str:
        """Return a fully self-contained HTML email body (inline CSS only)."""

        portfolio_value = self._safe_float(
            portfolio_summary.get("portfolio_value", 0)
        )
        num_trades = len(trades)

        # Colour logic -------------------------------------------------
        pnl_colour = "#1a7a3f" if total_pnl >= 0 else "#c0392b"
        pnl_bg = "#eafaf1" if total_pnl >= 0 else "#fdf2f2"

        if daily_loss_pct <= 1.0:
            risk_colour = "#1a7a3f"
            risk_bg = "#eafaf1"
            risk_label = "WITHIN LIMIT"
        elif daily_loss_pct <= 1.75:
            risk_colour = "#d68910"
            risk_bg = "#fef9e7"
            risk_label = "APPROACHING LIMIT"
        else:
            risk_colour = "#c0392b"
            risk_bg = "#fdf2f2"
            risk_label = "LIMIT BREACHED"

        # Trades table rows --------------------------------------------
        trade_rows_html = ""
        if not trades:
            trade_rows_html = (
                "<tr><td colspan='8' style='text-align:center;"
                "color:#888;padding:16px;'>No trades executed today.</td></tr>"
            )
        else:
            for t in trades:
                action = str(t.get("action", "")).upper()
                pnl_val = self._safe_float(t.get("pnl", ""))
                if action == "BUY":
                    row_bg = "#ebf5fb"
                elif action == "SELL" and pnl_val > 0:
                    row_bg = "#eafaf1"
                elif action == "SELL" and pnl_val < 0:
                    row_bg = "#fdf2f2"
                else:
                    row_bg = "#ffffff"

                pnl_str = (
                    f"{CUR_SYM}{pnl_val:+.2f}"
                    if action == "SELL"
                    else "—"
                )
                pnl_cell_colour = pnl_colour if action == "SELL" else "#333"
                trade_rows_html += (
                    f"<tr style='background:{row_bg};'>"
                    f"<td style='{_TD}'>{t.get('date','')}</td>"
                    f"<td style='{_TD}'>{t.get('time','')}</td>"
                    f"<td style='{_TD};font-weight:600;'>{t.get('symbol','')}</td>"
                    f"<td style='{_TD}'>{action}</td>"
                    f"<td style='{_TD};text-align:right;'>{t.get('quantity','')}</td>"
                    f"<td style='{_TD};text-align:right;'>{CUR_SYM}{self._safe_float(t.get('price',0)):.2f}</td>"
                    f"<td style='{_TD};text-align:right;font-weight:600;color:{pnl_cell_colour};'>{pnl_str}</td>"
                    f"<td style='{_TD}'>{t.get('exit_reason','')}</td>"
                    "</tr>"
                )

        # Open positions table -----------------------------------------
        open_positions: dict = portfolio_summary.get("open_positions", {})
        positions_section = ""
        if open_positions:
            pos_rows = ""
            for sym, pos in open_positions.items():
                qty = pos.get("quantity", 0)
                avg_cost = self._safe_float(pos.get("avg_cost", 0))
                market_value = self._safe_float(pos.get("market_value", 0))
                unrealised = self._safe_float(pos.get("unrealised_pnl", 0))
                u_colour = "#1a7a3f" if unrealised >= 0 else "#c0392b"
                pos_rows += (
                    f"<tr>"
                    f"<td style='{_TD};font-weight:600;'>{sym}</td>"
                    f"<td style='{_TD};text-align:right;'>{qty}</td>"
                    f"<td style='{_TD};text-align:right;'>{CUR_SYM}{avg_cost:.2f}</td>"
                    f"<td style='{_TD};text-align:right;'>{CUR_SYM}{market_value:.2f}</td>"
                    f"<td style='{_TD};text-align:right;font-weight:600;color:{u_colour};'>{CUR_SYM}{unrealised:+.2f}</td>"
                    "</tr>"
                )
            positions_section = f"""
            <h3 style='margin:28px 0 10px;color:#2c3e50;font-size:15px;font-family:Arial,sans-serif;'>
                Open Positions
            </h3>
            <table style='width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;'>
                <thead>
                    <tr style='background:#2c3e50;color:#fff;'>
                        <th style='{_TH}'>Symbol</th>
                        <th style='{_TH};text-align:right;'>Qty</th>
                        <th style='{_TH};text-align:right;'>Avg Cost</th>
                        <th style='{_TH};text-align:right;'>Market Value</th>
                        <th style='{_TH};text-align:right;'>Unrealised P&amp;L</th>
                    </tr>
                </thead>
                <tbody>{pos_rows}</tbody>
            </table>
            """

        html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>NSE Nifty 50 Trading Report — {session_date}</title>
</head>
<body style='margin:0;padding:0;background:#f4f6f9;font-family:Arial,sans-serif;'>

<table width='100%' cellpadding='0' cellspacing='0' style='background:#f4f6f9;padding:24px 0;'>
<tr><td align='center'>
<table width='640' cellpadding='0' cellspacing='0'
       style='background:#ffffff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 8px rgba(0,0,0,0.10);max-width:640px;width:100%;'>

  <!-- HEADER -->
  <tr>
    <td style='background:#1a252f;padding:28px 32px;'>
      <p style='margin:0;font-size:11px;color:#7f8c8d;letter-spacing:1.5px;
                text-transform:uppercase;font-family:Arial,sans-serif;'>
        Automated Trading Report
      </p>
      <h1 style='margin:6px 0 0;font-size:22px;color:#ecf0f1;font-family:Arial,sans-serif;
                 font-weight:700;'>
        NSE Nifty 50 Automated Trading Report
      </h1>
      <p style='margin:8px 0 0;font-size:13px;color:#bdc3c7;font-family:Arial,sans-serif;'>
        Session date: <strong style='color:#ecf0f1;'>{session_date}</strong>
      </p>
    </td>
  </tr>

  <!-- BODY -->
  <tr>
    <td style='padding:28px 32px;'>

      <!-- PERFORMANCE OVERVIEW -->
      <h3 style='margin:0 0 12px;color:#2c3e50;font-size:15px;font-family:Arial,sans-serif;'>
        Performance Overview
      </h3>
      <table width='100%' cellpadding='0' cellspacing='0' style='border-collapse:collapse;'>
        <tr>
          <td width='25%' style='padding:4px;'>
            <div style='background:{pnl_bg};border:1px solid #dde;border-radius:6px;padding:14px;text-align:center;'>
              <p style='margin:0;font-size:11px;color:#7f8c8d;text-transform:uppercase;letter-spacing:1px;'>Session P&amp;L</p>
              <p style='margin:6px 0 0;font-size:20px;font-weight:700;color:{pnl_colour};'>{CUR_SYM}{total_pnl:+.2f}</p>
            </div>
          </td>
          <td width='25%' style='padding:4px;'>
            <div style='background:#f0f4f8;border:1px solid #dde;border-radius:6px;padding:14px;text-align:center;'>
              <p style='margin:0;font-size:11px;color:#7f8c8d;text-transform:uppercase;letter-spacing:1px;'>Portfolio Value</p>
              <p style='margin:6px 0 0;font-size:20px;font-weight:700;color:#2c3e50;'>{CUR_SYM}{portfolio_value:,.2f}</p>
            </div>
          </td>
          <td width='25%' style='padding:4px;'>
            <div style='background:#f0f4f8;border:1px solid #dde;border-radius:6px;padding:14px;text-align:center;'>
              <p style='margin:0;font-size:11px;color:#7f8c8d;text-transform:uppercase;letter-spacing:1px;'>Win Rate</p>
              <p style='margin:6px 0 0;font-size:20px;font-weight:700;color:#2c3e50;'>{win_rate:.1f}%</p>
            </div>
          </td>
          <td width='25%' style='padding:4px;'>
            <div style='background:#f0f4f8;border:1px solid #dde;border-radius:6px;padding:14px;text-align:center;'>
              <p style='margin:0;font-size:11px;color:#7f8c8d;text-transform:uppercase;letter-spacing:1px;'>Trades Executed</p>
              <p style='margin:6px 0 0;font-size:20px;font-weight:700;color:#2c3e50;'>{num_trades}</p>
            </div>
          </td>
        </tr>
      </table>

      <!-- RISK STATUS -->
      <div style='margin:16px 0 0;background:{risk_bg};border-left:4px solid {risk_colour};
                  border-radius:4px;padding:12px 16px;'>
        <p style='margin:0;font-size:13px;font-family:Arial,sans-serif;color:#2c3e50;'>
          <strong style='color:{risk_colour};'>Risk Status: {risk_label}</strong>
          &nbsp;—&nbsp; Daily loss: <strong>{daily_loss_pct:.2f}%</strong>
          (limit: 2.00%)
        </p>
      </div>

      <!-- TRADE HISTORY -->
      <h3 style='margin:28px 0 10px;color:#2c3e50;font-size:15px;font-family:Arial,sans-serif;'>
        Trade History
      </h3>
      <table style='width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;'>
        <thead>
          <tr style='background:#2c3e50;color:#fff;'>
            <th style='{_TH}'>Date</th>
            <th style='{_TH}'>Time</th>
            <th style='{_TH}'>Symbol</th>
            <th style='{_TH}'>Action</th>
            <th style='{_TH};text-align:right;'>Qty</th>
            <th style='{_TH};text-align:right;'>Price</th>
            <th style='{_TH};text-align:right;'>P&amp;L</th>
            <th style='{_TH}'>Exit Reason</th>
          </tr>
        </thead>
        <tbody>
          {trade_rows_html}
        </tbody>
      </table>

      {positions_section}

    </td>
  </tr>

  <!-- FOOTER -->
  <tr>
    <td style='background:#f4f6f9;border-top:1px solid #dde;padding:16px 32px;text-align:center;'>
      <p style='margin:0;font-size:11px;color:#95a5a6;font-family:Arial,sans-serif;line-height:1.6;'>
        Agent run by Perplexity Computer — for informational purposes only.
        Not financial advice.
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>

</body>
</html>"""
        return html

    # ------------------------------------------------------------------
    # Plain-text generation
    # ------------------------------------------------------------------

    def _build_plain_text(
        self,
        session_date: str,
        portfolio_summary: dict,
        performance: dict,
        trades: List[Dict],
        total_pnl: float,
        win_rate: float,
        daily_loss_pct: float,
    ) -> str:
        """Return a clean ASCII tabular plain-text report."""
        portfolio_value = self._safe_float(
            portfolio_summary.get("portfolio_value", 0)
        )
        num_trades = len(trades)
        SEP = "=" * 72

        lines: List[str] = [
            SEP,
            "  NSE NIFTY 50 AUTOMATED TRADING REPORT",
            f"  Session date : {session_date}",
            SEP,
            "",
            "PERFORMANCE OVERVIEW",
            "-" * 40,
            f"  Session P&L      : {CUR_SYM}{total_pnl:+,.2f}",
            f"  Portfolio Value  : {CUR_SYM}{portfolio_value:,.2f}",
            f"  Win Rate         : {win_rate:.1f}%",
            f"  Trades Executed  : {num_trades}",
            "",
            "RISK STATUS",
            "-" * 40,
            f"  Daily Loss       : {daily_loss_pct:.2f}% (limit: 2.00%)",
            "",
            "TRADE HISTORY",
            "-" * 72,
            f"{'Date':<12} {'Time':<10} {'Symbol':<8} {'Action':<6} "
            f"{'Qty':>6} {'Price':>10} {'P&L':>10} {'Exit Reason':<20}",
            "-" * 72,
        ]

        if not trades:
            lines.append("  No trades executed today.")
        else:
            for t in trades:
                action = str(t.get("action", "")).upper()
                pnl_val = self._safe_float(t.get("pnl", ""))
                pnl_str = f"{CUR_SYM}{pnl_val:+.2f}" if action == "SELL" else "—"
                price_val = self._safe_float(t.get("price", 0))
                lines.append(
                    f"{t.get('date',''):<12} {t.get('time',''):<10} "
                    f"{t.get('symbol',''):<8} {action:<6} "
                    f"{str(t.get('quantity','')):>6} "
                    f"{CUR_SYM}{price_val:>9.2f} "
                    f"{pnl_str:>10} "
                    f"{str(t.get('exit_reason','')):.<20}"
                )

        # Open positions
        open_positions: dict = portfolio_summary.get("open_positions", {})
        lines += ["", "OPEN POSITIONS", "-" * 72]
        if not open_positions:
            lines.append("  No open positions.")
        else:
            lines.append(
                f"{'Symbol':<8} {'Qty':>6} {'Avg Cost':>10} "
                f"{'Mkt Value':>12} {'Unrealised P&L':>16}"
            )
            lines.append("-" * 72)
            for sym, pos in open_positions.items():
                qty = pos.get("quantity", 0)
                avg_cost = self._safe_float(pos.get("avg_cost", 0))
                market_value = self._safe_float(pos.get("market_value", 0))
                unrealised = self._safe_float(pos.get("unrealised_pnl", 0))
                lines.append(
                    f"{sym:<8} {str(qty):>6} {CUR_SYM}{avg_cost:>9.2f} "
                    f"{CUR_SYM}{market_value:>11.2f} {CUR_SYM}{unrealised:>+15.2f}"
                )

        lines += [
            "",
            SEP,
            "  Agent run by Perplexity Computer — for informational purposes only.",
            "  Not financial advice.",
            SEP,
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        session_date: str,
        portfolio_summary: dict,
        performance: dict,
        trades_csv_path: str,
    ) -> dict:
        """
        Generate the end-of-day trading report.

        Parameters
        ----------
        session_date:
            Date string in ISO format, e.g. '2026-06-08'.
        portfolio_summary:
            Dict as returned by ``PortfolioTracker.get_summary()``.
        performance:
            Dict as returned by ``PortfolioTracker.get_performance()``.
        trades_csv_path:
            Path to the trades CSV file (``config.agent.trades_csv``).

        Returns
        -------
        dict with keys:
            subject, plain_text, html_body, session_date,
            total_pnl, num_trades, win_rate
        """
        trades = self._read_today_trades(trades_csv_path, session_date)
        total_pnl = self._compute_total_pnl(trades)
        win_rate = self._compute_win_rate(trades)
        num_trades = len(trades)

        # Daily loss percentage from portfolio summary or performance dict.
        daily_loss_pct = abs(
            self._safe_float(
                portfolio_summary.get("daily_loss_pct")
                or performance.get("daily_loss_pct", 0.0)
            )
        )

        subject = (
            f"NSE Nifty 50 Trading Report \u2014 {session_date} | "
            f"P&L: \u20b9{total_pnl:+.2f}"
        )

        html_body = self._build_html(
            session_date=session_date,
            portfolio_summary=portfolio_summary,
            performance=performance,
            trades=trades,
            total_pnl=total_pnl,
            win_rate=win_rate,
            daily_loss_pct=daily_loss_pct,
        )

        plain_text = self._build_plain_text(
            session_date=session_date,
            portfolio_summary=portfolio_summary,
            performance=performance,
            trades=trades,
            total_pnl=total_pnl,
            win_rate=win_rate,
            daily_loss_pct=daily_loss_pct,
        )

        logger.info(
            "EOD report generated for %s: trades=%d pnl=%s%.2f win_rate=%.1f%%",
            session_date,
            num_trades,
            CUR_SYM, total_pnl,
            win_rate,
        )

        return {
            "subject": subject,
            "plain_text": plain_text,
            "html_body": html_body,
            "session_date": session_date,
            "total_pnl": total_pnl,
            "num_trades": num_trades,
            "win_rate": win_rate,
        }


# ---------------------------------------------------------------------------
# Shared CSS fragment constants (defined at module level for reuse)
# ---------------------------------------------------------------------------
_TD = "padding:8px 10px;border-bottom:1px solid #eaecee;vertical-align:middle;"
_TH = "padding:9px 10px;text-align:left;font-weight:600;font-size:12px;"
