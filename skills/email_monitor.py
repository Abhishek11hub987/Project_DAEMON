"""
Email Monitor — Live World Sensor (Gmail IMAP)
================================================

Connects to ``imap.gmail.com`` using credentials from ``.env``:

    EMAIL_ADDRESS=you@gmail.com
    EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

Uses Python's built-in ``imaplib`` — no extra dependencies.

Returns structured data:
- Total unread (UNSEEN) count
- Subject + sender of the 3 most recent emails

Designed for background polling via ``ProactiveMonitor``.

**Gmail setup**: You must use an App Password (not your regular password).
Go to https://myaccount.google.com/apppasswords to generate one.
"""

from __future__ import annotations

import email
import email.header
import imaplib
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _decode_header(raw: str) -> str:
    """Decode RFC 2047 encoded email headers into plain text."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(data))
    return " ".join(decoded).strip()


def _extract_name(sender: str) -> str:
    """Extract just the display name from 'Name <email>' format."""
    if not sender:
        return "Unknown"
    match = re.match(r'^"?([^"<]+)"?\s*<', sender)
    if match:
        return match.group(1).strip()
    return sender.split("@")[0] if "@" in sender else sender


class EmailMonitor:
    """Gmail IMAP monitor for DAEMON's background polling.

    Usage::

        monitor = EmailMonitor()
        status = monitor.fetch_inbox_status()
        # {
        #   "unread_count": 5,
        #   "total_inbox": 142,
        #   "recent": [
        #     {"sender": "John", "subject": "Meeting tomorrow"},
        #     ...
        #   ],
        #   "error": None
        # }
    """

    def __init__(
        self,
        email_address: Optional[str] = None,
        app_password: Optional[str] = None,
        imap_server: str = "imap.gmail.com",
        imap_port: int = 993,
    ) -> None:
        self._address = email_address or os.getenv("EMAIL_ADDRESS", "").strip()
        self._password = app_password or os.getenv("EMAIL_APP_PASSWORD", "").strip()
        self._server = imap_server
        self._port = imap_port

        if not self._address or not self._password:
            logger.warning(
                "📧 EmailMonitor: EMAIL_ADDRESS or EMAIL_APP_PASSWORD not set in .env. "
                "Email monitoring will return placeholder data."
            )

    @property
    def configured(self) -> bool:
        return bool(self._address and self._password)

    def fetch_inbox_status(self, recent_count: int = 3) -> Dict[str, Any]:
        """Fetch current inbox status from Gmail.

        Parameters
        ----------
        recent_count
            Number of most-recent emails to return subject/sender for.

        Returns
        -------
        dict
            Keys: unread_count, total_inbox, recent (list), error (str or None)
        """
        if not self.configured:
            return self._placeholder()

        conn: Optional[imaplib.IMAP4_SSL] = None
        try:
            # Connect
            conn = imaplib.IMAP4_SSL(self._server, self._port)
            conn.login(self._address, self._password)
            conn.select("INBOX", readonly=True)

            # Count unread
            status, unseen_data = conn.search(None, "(UNSEEN)")
            unseen_ids = unseen_data[0].split() if status == "OK" and unseen_data[0] else []
            unread_count = len(unseen_ids)

            # Count total
            status, all_data = conn.search(None, "ALL")
            all_ids = all_data[0].split() if status == "OK" and all_data[0] else []
            total_inbox = len(all_ids)

            # Fetch most recent N emails (by highest UID = newest)
            recent: List[Dict[str, str]] = []
            if all_ids:
                # Take the last N IDs (most recent)
                target_ids = all_ids[-recent_count:]
                target_ids.reverse()  # newest first

                for msg_id in target_ids:
                    try:
                        status, msg_data = conn.fetch(msg_id, "(RFC822.HEADER)")
                        if status != "OK" or not msg_data or not msg_data[0]:
                            continue
                        raw_header = msg_data[0][1]
                        if isinstance(raw_header, bytes):
                            msg = email.message_from_bytes(raw_header)
                        else:
                            msg = email.message_from_string(raw_header)

                        subject = _decode_header(msg.get("Subject", ""))
                        sender = _extract_name(_decode_header(msg.get("From", "")))
                        recent.append({
                            "sender": sender[:50],
                            "subject": subject[:120],
                        })
                    except Exception as e:
                        logger.debug(f"Email parse error for ID {msg_id}: {e}")
                        continue

            logger.info(
                f"📧 EmailMonitor: {unread_count} unread / {total_inbox} total, "
                f"{len(recent)} recent fetched."
            )
            return {
                "unread_count": unread_count,
                "total_inbox": total_inbox,
                "recent": recent,
                "error": None,
            }

        except imaplib.IMAP4.error as e:
            error_msg = f"IMAP authentication failed: {e}"
            logger.error(f"📧 EmailMonitor: {error_msg}")
            return {
                "unread_count": 0,
                "total_inbox": 0,
                "recent": [],
                "error": error_msg,
            }
        except Exception as e:
            error_msg = f"Email fetch failed: {e}"
            logger.error(f"📧 EmailMonitor: {error_msg}")
            return {
                "unread_count": 0,
                "total_inbox": 0,
                "recent": [],
                "error": error_msg,
            }
        finally:
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass

    @staticmethod
    def _placeholder() -> Dict[str, Any]:
        """Return placeholder data when credentials aren't configured."""
        return {
            "unread_count": 0,
            "total_inbox": 0,
            "recent": [],
            "error": "EMAIL_ADDRESS or EMAIL_APP_PASSWORD not configured in .env",
        }
