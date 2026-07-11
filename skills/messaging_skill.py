"""
Messaging Skill — Send Emails & WhatsApp from Voice
=====================================================

Handles commands like:
    "email Ravi that the project is ready"
    "message mom I'll be home by 9"
    "send a WhatsApp to Priya saying meeting at 3"

Flow:
    1. Parse recipient + message body from natural language
    2. Resolve contact from contacts.json
    3. Confirm with user before sending
    4. Send via SMTP (email) or pywhatkit (WhatsApp)
"""

from __future__ import annotations

import json
import logging
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core_logic.config import Config

logger = logging.getLogger(__name__)


class MessagingSkill:
    """Send emails and WhatsApp messages from voice commands."""

    _contacts: Optional[Dict[str, Any]] = None

    @classmethod
    def _load_contacts(cls) -> Dict[str, Any]:
        """Load contacts from the JSON file."""
        if cls._contacts is not None:
            return cls._contacts

        contacts_path = Config.CONTACTS_PATH
        if contacts_path.exists():
            try:
                with open(contacts_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Remove internal keys
                cls._contacts = {
                    k: v for k, v in data.items()
                    if not k.startswith("_")
                }
                logger.info(f"📇 Loaded {len(cls._contacts)} contact(s)")
                return cls._contacts
            except Exception as e:
                logger.warning(f"Failed to load contacts: {e}")

        cls._contacts = {}
        return cls._contacts

    @classmethod
    def _resolve_contact(cls, name: str) -> Optional[Dict[str, Any]]:
        """Find a contact by name (case-insensitive, fuzzy)."""
        contacts = cls._load_contacts()
        name_lower = name.lower().strip()

        # Exact match
        if name_lower in contacts:
            return contacts[name_lower]

        # Partial match
        for key, val in contacts.items():
            if name_lower in key.lower() or key.lower() in name_lower:
                return val

        return None

    @staticmethod
    def _parse_message(text: str) -> Tuple[Optional[str], Optional[str], str]:
        """Extract recipient, message body, and channel from natural language.

        Returns: (recipient_name, message_body, channel)
        Channel is 'email', 'whatsapp', or 'auto'.
        """
        text_lower = text.lower()

        # Detect channel
        channel = "auto"
        if any(w in text_lower for w in ["whatsapp", "wa ", "watsapp"]):
            channel = "whatsapp"
        elif any(w in text_lower for w in ["email", "mail", "e-mail"]):
            channel = "email"

        # Try patterns like "email <name> that <message>"
        patterns = [
            # "email/message/text Ravi that the project is ready"
            r'(?:email|message|text|send\s+(?:a\s+)?(?:message|email|text)\s+(?:to\s+)?)(\w+)\s+(?:that|saying|say)\s+(.+)',
            # "whatsapp mom I'll be home by 9"
            r'(?:whatsapp|wa)\s+(\w+)\s+(?:that|saying|say)?\s*(.+)',
            # "send to Ravi: the project is ready"
            r'(?:send\s+(?:to\s+)?)(\w+)[:\s]+(.+)',
            # "tell Ravi the project is done" (when clearly messaging)
            r'(?:tell)\s+(\w+)\s+(?:that\s+)?(.+)',
            # "message Ravi project is ready"
            r'(?:email|message|text|whatsapp)\s+(\w+)\s+(.+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                recipient = match.group(1).strip()
                body = match.group(2).strip()
                # Clean up the body
                body = body.rstrip(".")
                if body:
                    return recipient, body, channel

        return None, None, channel

    @classmethod
    def handle(cls, text: str) -> str:
        """Handle a messaging command.

        Returns a confirmation prompt or error message (NOT the actual send).
        The actual sending happens via send_email() or send_whatsapp()
        after user confirmation.
        """
        recipient_name, body, channel = cls._parse_message(text)

        if not recipient_name:
            return (
                "I couldn't figure out who to message. "
                "Try something like: 'email Ravi that the project is ready'."
            )

        if not body:
            return f"What would you like me to say to {recipient_name}?"

        contact = cls._resolve_contact(recipient_name)

        if not contact:
            return (
                f"I don't have {recipient_name} in my contacts. "
                f"Add them to config/contacts.json with their email or phone, "
                f"and I'll be able to message them."
            )

        # Determine the best channel
        if channel == "auto":
            if contact.get("email"):
                channel = "email"
            elif contact.get("phone") and Config.ENABLE_WHATSAPP:
                channel = "whatsapp"
            else:
                channel = "email"

        if channel == "email" and not contact.get("email"):
            return f"I don't have an email address for {recipient_name}. Add it to contacts.json."
        if channel == "whatsapp" and not contact.get("phone"):
            return f"I don't have a phone number for {recipient_name}. Add it to contacts.json."

        # Store pending message for confirmation
        cls._pending = {
            "recipient_name": recipient_name,
            "body": body,
            "channel": channel,
            "contact": contact,
        }

        if channel == "email":
            return (
                f"I'll email {recipient_name} at {contact['email']} saying: "
                f"'{body}'. Should I send it?"
            )
        else:
            return (
                f"I'll WhatsApp {recipient_name} at {contact['phone']} saying: "
                f"'{body}'. Should I send it?"
            )

    @classmethod
    def confirm_send(cls) -> str:
        """Actually send the pending message after user confirms."""
        pending = getattr(cls, "_pending", None)
        if not pending:
            return "No pending message to send."

        try:
            if pending["channel"] == "email":
                result = cls.send_email(
                    to_email=pending["contact"]["email"],
                    subject=f"Message from D.A.E.M.O.N.",
                    body=pending["body"],
                )
            else:
                result = cls.send_whatsapp(
                    phone=pending["contact"]["phone"],
                    message=pending["body"],
                )

            cls._pending = None
            return result
        except Exception as e:
            cls._pending = None
            return f"Failed to send: {e}"

    @staticmethod
    def send_email(to_email: str, subject: str, body: str) -> str:
        """Send an email via SMTP using configured credentials."""
        from_email = Config.EMAIL_ADDRESS if hasattr(Config, "EMAIL_ADDRESS") else ""
        password = Config.EMAIL_APP_PASSWORD if hasattr(Config, "EMAIL_APP_PASSWORD") else ""

        if not from_email or not password:
            return (
                "Email credentials not configured. Set EMAIL_ADDRESS and "
                "EMAIL_APP_PASSWORD in your .env file."
            )

        try:
            msg = MIMEMultipart()
            msg["From"] = from_email
            msg["To"] = to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(Config.EMAIL_SMTP_SERVER, Config.EMAIL_SMTP_PORT) as server:
                server.starttls()
                server.login(from_email, password)
                server.send_message(msg)

            logger.info(f"📧 Email sent to {to_email}")
            return f"Done! Email sent to {to_email}."

        except smtplib.SMTPAuthenticationError:
            return (
                "Email authentication failed. Make sure EMAIL_APP_PASSWORD "
                "is a Gmail App Password, not your regular password."
            )
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return f"Email send failed: {e}"

    @staticmethod
    def send_whatsapp(phone: str, message: str) -> str:
        """Send a WhatsApp message using pywhatkit."""
        if not Config.ENABLE_WHATSAPP:
            return "WhatsApp is disabled. Set ENABLE_WHATSAPP=true in .env."

        try:
            import pywhatkit
            # pywhatkit.sendwhatmsg_instantly sends immediately
            pywhatkit.sendwhatmsg_instantly(
                phone_no=phone,
                message=message,
                wait_time=15,
                tab_close=True,
            )
            logger.info(f"📱 WhatsApp sent to {phone}")
            return f"Done! WhatsApp message sent to {phone}."
        except ImportError:
            return "pywhatkit not installed. Run: pip install pywhatkit"
        except Exception as e:
            logger.error(f"WhatsApp send failed: {e}")
            return f"WhatsApp send failed: {e}"

    @classmethod
    def list_contacts(cls) -> str:
        """List all configured contacts."""
        contacts = cls._load_contacts()
        if not contacts:
            return "No contacts configured. Add them to config/contacts.json."

        lines = [f"You have {len(contacts)} contact(s):"]
        for name, info in contacts.items():
            parts = [name.title()]
            if info.get("email"):
                parts.append(f"email: {info['email']}")
            if info.get("phone"):
                parts.append(f"phone: {info['phone']}")
            lines.append(f"  - {', '.join(parts)}")
        return "\n".join(lines)

    # Pending message storage
    _pending: Optional[Dict[str, Any]] = None
