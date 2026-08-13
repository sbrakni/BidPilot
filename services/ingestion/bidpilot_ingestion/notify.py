"""Notification delivery (SPEC §12.4, §8.3).

Raising an alert and delivering it are different things, and P3 only cares about the second.
This module turns a `notifications` row into an actual email and records that it went out.

Design notes worth knowing before changing anything here:

  * **`sent_at` is set only after the transport accepts the message.** A row with `sent_at` NULL
    is an alert that was raised but not delivered - which is exactly the state the redundant
    scheduler (§16) is supposed to retry. Marking it sent before sending would turn a transient
    SMTP failure into a permanently lost deadline alert.
  * **Copy comes from the same message catalogue as the UI** (`apps/web/messages/*.json`), so an
    email and the screen it links to cannot disagree, and neither contains a hardcoded string
    (§20.6).
  * **Recipients are resolved per org inside that org's context**, so a notification can only
    ever reach members of the org it belongs to.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .alerts import all_org_ids, set_org_context

log = logging.getLogger(__name__)

MESSAGES_DIR = Path(__file__).resolve().parents[3] / "apps" / "web" / "messages"

#: Digest send hour in the org's own timezone (SPEC §8.3: 07:30 Europe/Paris, configurable).
DIGEST_HOUR_LOCAL = 7

#: Cap on matches listed in one digest. A digest nobody finishes reading is not a digest.
DIGEST_MAX_MATCHES = 10


class Transport(Protocol):
    """Anything that can accept an email. Injected so delivery is testable without a server."""

    def send(self, message: EmailMessage) -> None: ...


class SmtpTransport:
    """Real SMTP. Points at mailpit in development (SMTP_URL=smtp://localhost:1025)."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("SMTP_URL", "smtp://localhost:1025")

    def send(self, message: EmailMessage) -> None:
        host, _, port = self.url.removeprefix("smtp://").removeprefix("smtps://").partition(":")
        with smtplib.SMTP(host or "localhost", int(port or 1025), timeout=30) as client:
            client.send_message(message)


class CollectingTransport:
    """Captures messages instead of sending them - used by tests and by `--dry-run`."""

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


@dataclass(frozen=True)
class Recipient:
    user_id: str
    email: str
    name: str | None
    locale: str


def _load_messages(locale: str) -> dict[str, Any]:
    """Read the UI catalogue so email copy and screen copy stay identical."""
    path = MESSAGES_DIR / f"{locale}.json"
    if not path.exists():
        path = MESSAGES_DIR / "fr.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _translate(messages: dict[str, Any], dotted_key: str, default: str = "") -> str:
    node: Any = messages
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if isinstance(node, str) else default


#: Subject lines per notification kind. Kept here rather than in the UI catalogue because they
#: are email-only copy, and the placeholders differ from any on-screen string.
SUBJECTS: dict[str, dict[str, str]] = {
    "deadline.j14": {
        "fr": "J-14 : {title}",
        "en": "14 working days left: {title}",
    },
    "deadline.j7": {"fr": "J-7 : {title}", "en": "7 working days left: {title}"},
    "deadline.j3": {"fr": "J-3 : {title}", "en": "3 working days left: {title}"},
    "deadline.j1": {"fr": "Demain : {title}", "en": "Due tomorrow: {title}"},
    "deadline.escalated": {
        "fr": "Alerte J-3 non acquittée : {title}",
        "en": "Unacknowledged J-3 alert: {title}",
    },
    "tender.amended": {
        "fr": "Avis modifié : {title}",
        "en": "Notice amended: {title}",
    },
    "match.high_score": {
        "fr": "Nouvel avis pertinent : {title}",
        "en": "New relevant notice: {title}",
    },
}


def render_notification(
    kind: str, payload: dict[str, Any], recipient: Recipient, *, app_url: str
) -> EmailMessage:
    """Build the email for one notification.

    Plain text only, deliberately: these are short operational alerts whose entire job is to be
    read on a phone and acted on. HTML would add rendering risk for no gain, and the studio's
    branded output (§13) is a different problem with different requirements.
    """
    locale = recipient.locale if recipient.locale in ("fr", "en") else "fr"
    messages = _load_messages(locale)
    title = payload.get("title") or payload.get("notice_title") or ""

    subject_template = SUBJECTS.get(kind, {}).get(locale) or SUBJECTS.get(kind, {}).get("fr")
    subject = (subject_template or kind).format(title=title[:90] or kind)

    deadline = payload.get("deadline_at")
    lines: list[str] = []
    if recipient.name:
        lines.append(f"{recipient.name},")
        lines.append("")

    if kind.startswith("deadline."):
        label = _translate(messages, "notice.deadline", "Date limite")
        lines.append(f"{title}")
        if deadline:
            lines.append(f"{label}: {deadline}")
        if kind == "deadline.escalated":
            lines.append("")
            lines.append(
                "Cette alerte J-3 n'a pas été acquittée depuis 24 heures."
                if locale == "fr"
                else "This J-3 alert has been unacknowledged for 24 hours."
            )
    elif kind == "tender.amended":
        lines.append(title)
        lines.append("")
        lines.append(
            "L'avis a été modifié à la source. L'analyse doit être revue."
            if locale == "fr"
            else "The notice was amended at source. The analysis needs review."
        )
    else:
        lines.append(title)

    tender_id = payload.get("tender_id")
    path = f"/tenders/{tender_id}" if tender_id else "/today"
    lines.extend(["", f"{app_url}{path}"])

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ.get("EMAIL_FROM", "BidPilot <no-reply@bidpilot.example>")
    message["To"] = recipient.email
    # Alerts are transactional and must never be bulk-filtered or auto-replied to.
    message["Auto-Submitted"] = "auto-generated"
    message.set_content("\n".join(lines))
    return message


def recipients_for_org(connection: Connection, org_id: str) -> list[Recipient]:
    """Members of one org, read inside that org's context so nobody else can be reached."""
    set_org_context(connection, org_id)
    rows = (
        connection.execute(
            text(
                """
            SELECT u.id, u.email, u.name, u.locale
            FROM org_members m JOIN users u ON u.id = m.user_id
            WHERE m.org_id = :org AND m.role <> 'viewer'
            """
            ),
            {"org": org_id},
        )
        .mappings()
        .all()
    )
    return [
        Recipient(user_id=row["id"], email=row["email"], name=row["name"], locale=row["locale"] or "fr")
        for row in rows
    ]


def run_notify_send(
    connection: Connection,
    *,
    transport: Transport | None = None,
    limit: int = 100,
    org_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Deliver every raised-but-unsent notification.

    Driven by the `notifications` table rather than by the job payload, so a `notify.send` job
    that was lost or retried cannot cause a double send or a silent miss - the table is the
    record, and `sent_at` is the fact.

    `org_ids` narrows the sweep, which ops needs to re-drive delivery for a single org after an
    incident without touching everyone else's queue.
    """
    transport = transport or SmtpTransport()
    app_url = os.environ.get("APP_PUBLIC_URL", "http://localhost:3000")

    sent = 0
    failed = 0
    for org_id in org_ids if org_ids is not None else all_org_ids(connection):
        set_org_context(connection, org_id)
        pending = (
            connection.execute(
                text(
                    """
                SELECT id, kind, payload FROM notifications
                WHERE sent_at IS NULL
                ORDER BY created_at
                LIMIT :limit
                """
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        )
        if not pending:
            continue

        people = recipients_for_org(connection, org_id)
        if not people:
            log.warning("org %s has no notifiable members; %d alert(s) held", org_id, len(pending))
            continue

        for row in pending:
            delivered_to_any = False
            for recipient in people:
                message = render_notification(row["kind"], row["payload"] or {}, recipient, app_url=app_url)
                try:
                    transport.send(message)
                    delivered_to_any = True
                except Exception as exc:
                    log.warning("delivery to %s failed: %s", recipient.email, exc)

            if delivered_to_any:
                # Only now is it "sent". A row left NULL will be retried on the next tick, which
                # is the behaviour a missed deadline alert requires (§16).
                set_org_context(connection, org_id)
                connection.execute(
                    text("UPDATE notifications SET sent_at = now() WHERE id = :id"), {"id": row["id"]}
                )
                sent += 1
            else:
                failed += 1

    set_org_context(connection, None)
    return {"sent": sent, "failed": failed}


def run_daily_digest(
    connection: Connection,
    *,
    transport: Transport | None = None,
    now: datetime | None = None,
    org_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Send each org its daily digest at the configured local hour (SPEC §8.3).

    The hour is compared in the *org's* timezone, so a Luxembourg org and a Paris org both get
    their digest at breakfast rather than one of them at 05:00.
    """
    transport = transport or SmtpTransport()
    now = now or datetime.now(UTC)
    app_url = os.environ.get("APP_PUBLIC_URL", "http://localhost:3000")

    digests = 0
    skipped = 0
    for org_id in org_ids if org_ids is not None else all_org_ids(connection):
        set_org_context(connection, org_id)
        org = (
            connection.execute(text("SELECT id, name, tz, locale FROM orgs WHERE id = :id"), {"id": org_id})
            .mappings()
            .first()
        )
        if org is None:
            continue

        local_hour = connection.execute(
            text("SELECT EXTRACT(HOUR FROM (CAST(:now AS timestamptz) AT TIME ZONE :tz))"),
            {"now": now, "tz": org["tz"] or "Europe/Paris"},
        ).scalar()
        if int(local_hour or 0) != DIGEST_HOUR_LOCAL:
            skipped += 1
            continue

        # One digest per org per local day, enforced by the notification record.
        already = connection.execute(
            text(
                """
                SELECT 1 FROM notifications
                WHERE kind = 'digest.daily'
                  AND created_at > CAST(:now AS timestamptz) - interval '20 hours'
                LIMIT 1
                """
            ),
            {"now": now},
        ).first()
        if already:
            skipped += 1
            continue

        matches = (
            connection.execute(
                text(
                    """
                SELECT m.score, n.title, n.deadline_at
                FROM matches m JOIN notices n ON n.id = m.notice_id
                WHERE m.state = 'new'
                ORDER BY m.score DESC
                LIMIT :limit
                """
                ),
                {"limit": DIGEST_MAX_MATCHES},
            )
            .mappings()
            .all()
        )
        expiring = connection.execute(
            text("SELECT count(*) FROM evidences WHERE status IN ('expiring', 'expired')")
        ).scalar()

        if not matches and not expiring:
            # Nothing to say. An empty daily email is how people learn to filter the sender.
            skipped += 1
            continue

        locale = (org["locale"] or "fr") if (org["locale"] or "fr") in ("fr", "en") else "fr"
        messages = _load_messages(locale)
        lines = [
            _translate(messages, "today.newMatches", "Nouveaux avis pertinents") + ":",
            "",
        ]
        lines.extend(f"  [{row['score']:3d}] {(row['title'] or '')[:80]}" for row in matches)
        if expiring:
            lines.extend(["", f"{_translate(messages, 'today.vaultAlerts', 'Documents')}: {expiring}"])
        lines.extend(["", f"{app_url}/today"])

        payload = {"matches": len(matches), "evidence_needing_attention": int(expiring or 0)}
        connection.execute(
            text(
                """
                INSERT INTO notifications (id, org_id, user_id, kind, payload, channels, created_at)
                VALUES (:id, :org, NULL, 'digest.daily', CAST(:payload AS jsonb), ARRAY['email'], now())
                """
            ),
            {"id": _new_notification_id(), "org": org_id, "payload": json.dumps(payload)},
        )

        for recipient in recipients_for_org(connection, org_id):
            message = EmailMessage()
            message["Subject"] = (
                f"BidPilot — {len(matches)} nouveaux avis"
                if locale == "fr"
                else f"BidPilot — {len(matches)} new notices"
            )
            message["From"] = os.environ.get("EMAIL_FROM", "BidPilot <no-reply@bidpilot.example>")
            message["To"] = recipient.email
            message["Auto-Submitted"] = "auto-generated"
            message.set_content("\n".join(lines))
            try:
                transport.send(message)
            except Exception as exc:
                log.warning("digest delivery to %s failed: %s", recipient.email, exc)
        digests += 1

    set_org_context(connection, None)
    return {"digests": digests, "skipped": skipped}


def _new_notification_id() -> str:
    from .ids import new_id

    return new_id("ntf")
