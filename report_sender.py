"""
report_sender.py
================
Sends the end-of-day HTML trading report via Gmail using smtplib + STARTTLS.
Credentials are read from environment variables.

Required environment variables
-------------------------------
GMAIL_ADDRESS       : Sender's Gmail address (e.g. bot@gmail.com).
GMAIL_APP_PASSWORD  : 16-character Gmail App Password — NOT the account password.
                      Generate one at https://myaccount.google.com/apppasswords
REPORT_RECIPIENT    : Recipient email address.  Defaults to GMAIL_ADDRESS if
                      not set (i.e. sends the report to the sender's own inbox).

Example
-------
>>> sender = ReportSender()
>>> ok = sender.send(report_dict)
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


class ReportSender:
    """
    Sends the EOD trading report via Gmail SMTP with STARTTLS.

    Credentials are read from environment variables at construction time.
    ``send()`` and ``test_connection()`` are safe to call even when credentials
    are missing — they log an appropriate error and return False instead of
    raising.
    """

    def __init__(self) -> None:
        self._sender: str = os.environ.get("GMAIL_ADDRESS", "").strip()
        self._password: str = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
        # Recipient defaults to the sender if REPORT_RECIPIENT is not set.
        self._recipient: str = (
            os.environ.get("REPORT_RECIPIENT", "").strip() or self._sender
        )

        if not self._sender:
            logger.warning(
                "ReportSender: GMAIL_ADDRESS env var not set — "
                "email sending will fail until it is configured."
            )
        if not self._password:
            logger.warning(
                "ReportSender: GMAIL_APP_PASSWORD env var not set — "
                "email sending will fail until it is configured."
            )

        logger.debug(
            "ReportSender initialised: sender=%s recipient=%s",
            self._sender or "<not set>",
            self._recipient or "<not set>",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _credentials_present(self) -> bool:
        """Return True if both sender address and app password are set."""
        return bool(self._sender and self._password)

    def _build_message(self, report: dict) -> MIMEMultipart:
        """
        Build a MIMEMultipart('alternative') email from *report*.

        The message contains both a plain-text part and an HTML part.
        Email clients will render the HTML part where supported, and fall
        back to plain text otherwise.
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = report.get("subject", "Trading Agent Daily Report")
        msg["From"] = self._sender
        msg["To"] = self._recipient

        plain_text: str = report.get("plain_text", "")
        html_body: str = report.get("html_body", "")

        # Attach plain-text first (lower priority — fallback).
        if plain_text:
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))

        # Attach HTML second (higher priority — preferred by clients).
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        return msg

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, report: dict) -> bool:
        """
        Send *report* as an HTML email via Gmail SMTP.

        Parameters
        ----------
        report:
            Dict as returned by ``EODReportGenerator.generate()``.
            Must contain at minimum: ``subject``, ``plain_text``, ``html_body``.

        Returns
        -------
        bool
            True if the message was accepted by the SMTP server, False on
            any error.  Never raises.
        """
        if not self._credentials_present():
            logger.error(
                "ReportSender.send(): missing GMAIL_ADDRESS or "
                "GMAIL_APP_PASSWORD — cannot send report."
            )
            return False

        if not self._recipient:
            logger.error(
                "ReportSender.send(): recipient address is empty — "
                "cannot send report."
            )
            return False

        msg = self._build_message(report)

        try:
            logger.info(
                "Connecting to %s:%d to send EOD report to %s …",
                _SMTP_HOST, _SMTP_PORT, self._recipient,
            )
            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._sender, self._password)
                server.sendmail(
                    self._sender,
                    [self._recipient],
                    msg.as_string(),
                )
            logger.info(
                "EOD report sent successfully to %s (subject: %s)",
                self._recipient,
                report.get("subject", ""),
            )
            return True

        except smtplib.SMTPAuthenticationError as exc:
            logger.error(
                "SMTP authentication failed — check GMAIL_ADDRESS and "
                "GMAIL_APP_PASSWORD: %s",
                exc,
            )
            return False

        except smtplib.SMTPConnectError as exc:
            logger.error(
                "SMTP connection to %s:%d failed: %s",
                _SMTP_HOST, _SMTP_PORT, exc,
            )
            return False

        except smtplib.SMTPRecipientsRefused as exc:
            logger.error(
                "SMTP recipient refused (%s): %s",
                self._recipient, exc,
            )
            return False

        except smtplib.SMTPException as exc:
            logger.error(
                "SMTP error while sending report: %s", exc, exc_info=True
            )
            return False

        except OSError as exc:
            # Catches network-level errors (connection refused, timeout, etc.)
            logger.error(
                "Network error while sending report: %s", exc, exc_info=True
            )
            return False

        except Exception as exc:
            # Belt-and-suspenders: never let unexpected errors propagate.
            logger.error(
                "Unexpected error in ReportSender.send(): %s",
                exc, exc_info=True,
            )
            return False

    def test_connection(self) -> bool:
        """
        Attempt to connect and authenticate with the Gmail SMTP server
        without sending any email.

        Returns True on success, False on any failure.  Never raises.
        Logs the outcome at INFO level.
        """
        if not self._credentials_present():
            logger.error(
                "ReportSender.test_connection(): GMAIL_ADDRESS or "
                "GMAIL_APP_PASSWORD not set — cannot test connection."
            )
            return False

        try:
            logger.info(
                "Testing SMTP connection to %s:%d …",
                _SMTP_HOST, _SMTP_PORT,
            )
            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._sender, self._password)

            logger.info(
                "SMTP connection test PASSED — credentials for %s are valid.",
                self._sender,
            )
            return True

        except smtplib.SMTPAuthenticationError as exc:
            logger.error(
                "SMTP connection test FAILED — authentication error: %s", exc
            )
            return False

        except smtplib.SMTPConnectError as exc:
            logger.error(
                "SMTP connection test FAILED — connection error: %s", exc
            )
            return False

        except smtplib.SMTPException as exc:
            logger.error(
                "SMTP connection test FAILED — SMTP error: %s", exc
            )
            return False

        except OSError as exc:
            logger.error(
                "SMTP connection test FAILED — network error: %s", exc
            )
            return False

        except Exception as exc:
            logger.error(
                "SMTP connection test FAILED — unexpected error: %s",
                exc, exc_info=True,
            )
            return False
