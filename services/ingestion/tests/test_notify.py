"""Notification delivery tests (SPEC §12.4, §8.3).

The property that matters most is the one asserted first: a notification is marked sent only
after a transport accepted it. Marking it sent before sending would turn a transient SMTP
failure into a permanently lost deadline alert - the failure §16 calls the worst there is.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from email.message import EmailMessage

import pytest
from bidpilot_ingestion.notify import (
    CollectingTransport,
    Recipient,
    render_notification,
    run_daily_digest,
    run_notify_send,
)
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; delivery tests need a migrated Postgres",
)


class FailingTransport:
    """Rejects everything, to prove a failed send is retried rather than lost."""

    def send(self, message: EmailMessage) -> None:
        raise RuntimeError("smtp unavailable")


@pytest.fixture(scope="module")
def db():
    from bidpilot_ingestion import db as db_module

    return db_module


@pytest.fixture
def connection(db):
    with db.get_engine().connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


@pytest.fixture
def org_with_member(connection):
    """A disposable org with one notifiable member and one raised alert."""
    from bidpilot_ingestion.ids import new_id

    org_id = new_id("org")
    user_id = new_id("usr")
    connection.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id})
    connection.execute(text("SELECT set_config('app.user_id', :user, true)"), {"user": user_id})
    connection.execute(
        text(
            """
            INSERT INTO orgs (id, name, country, locale, tz, settings, plan, ai_credits_balance,
                              created_at, updated_at)
            VALUES (:id, 'Notify Test', 'FR', 'fr', 'Europe/Paris', '{}'::jsonb, 'trial', 0,
                    now(), now())
            """
        ),
        {"id": org_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO users (id, email, name, locale, created_at, updated_at)
            VALUES (:id, :email, 'Léa Test', 'fr', now(), now())
            """
        ),
        {"id": user_id, "email": f"{user_id}@example.test"},
    )
    connection.execute(
        text(
            """
            INSERT INTO org_members (org_id, user_id, role, created_at)
            VALUES (:org, :user, 'owner', now())
            """
        ),
        {"org": org_id, "user": user_id},
    )
    notification_id = new_id("ntf")
    connection.execute(
        text(
            """
            INSERT INTO notifications (id, org_id, user_id, kind, payload, channels, created_at)
            VALUES (:id, :org, NULL, 'deadline.j7', CAST(:payload AS jsonb),
                    ARRAY['in_app','email'], now())
            """
        ),
        {
            "id": notification_id,
            "org": org_id,
            "payload": json.dumps(
                {
                    "tender_id": "tnd_notify",
                    "title": "Infogérance du système d'information",
                    "deadline_at": "2026-11-20T11:00:00+00:00",
                    "offset_working_days": 7,
                }
            ),
        },
    )
    return {"org_id": org_id, "user_id": user_id, "notification_id": notification_id}


# ---------------------------------------------------------------- rendering


def test_subject_and_body_carry_what_the_reader_needs():
    recipient = Recipient(user_id="usr_1", email="lea@example.test", name="Léa", locale="fr")
    message = render_notification(
        "deadline.j3",
        {"title": "Maintenance des lycées", "deadline_at": "2026-11-20T11:00:00+00:00", "tender_id": "tnd_1"},
        recipient,
        app_url="https://app.test",
    )
    assert "J-3" in message["Subject"]
    assert "Maintenance des lycées" in message["Subject"]
    body = message.get_content()
    assert "2026-11-20" in body
    # A deep link, so acting on the alert is one tap rather than a search.
    assert "https://app.test/tenders/tnd_1" in body
    # Transactional mail must not be bulk-filtered or auto-replied to.
    assert message["Auto-Submitted"] == "auto-generated"


def test_copy_follows_the_recipient_locale():
    payload = {"title": "IT maintenance", "tender_id": "tnd_2"}
    fr = render_notification("deadline.j1", payload, Recipient("u", "a@test", None, "fr"), app_url="x")
    en = render_notification("deadline.j1", payload, Recipient("u", "a@test", None, "en"), app_url="x")
    assert "Demain" in fr["Subject"]
    assert "tomorrow" in en["Subject"].lower()


def test_an_unknown_locale_falls_back_rather_than_failing():
    message = render_notification(
        "deadline.j7", {"title": "T"}, Recipient("u", "a@test", None, "de"), app_url="x"
    )
    assert message["Subject"]


def test_amendment_notification_says_what_changed_and_why_it_matters():
    message = render_notification(
        "tender.amended",
        {"title": "Fourniture de licences", "tender_id": "tnd_3"},
        Recipient("u", "a@test", None, "fr"),
        app_url="x",
    )
    assert "modifié" in message["Subject"]
    assert "revue" in message.get_content()


# ---------------------------------------------------------------- delivery


def test_delivery_marks_sent_only_after_the_transport_accepts(connection, org_with_member):
    transport = CollectingTransport()
    result = run_notify_send(connection, transport=transport, org_ids=[org_with_member["org_id"]])

    assert result["sent"] >= 1
    assert transport.messages, "an alert must actually be handed to a transport"

    connection.execute(
        text("SELECT set_config('app.org_id', :org, true)"), {"org": org_with_member["org_id"]}
    )
    sent_at = connection.execute(
        text("SELECT sent_at FROM notifications WHERE id = :id"), {"id": org_with_member["notification_id"]}
    ).scalar()
    assert sent_at is not None


def test_a_failed_send_leaves_the_alert_unsent_for_retry(connection, org_with_member):
    """The single most important behaviour here: a transient SMTP failure must not consume the
    alert. `sent_at` stays NULL so the next tick tries again (§16 redundancy)."""
    result = run_notify_send(connection, transport=FailingTransport(), org_ids=[org_with_member["org_id"]])
    assert result["sent"] == 0
    assert result["failed"] >= 1

    connection.execute(
        text("SELECT set_config('app.org_id', :org, true)"), {"org": org_with_member["org_id"]}
    )
    sent_at = connection.execute(
        text("SELECT sent_at FROM notifications WHERE id = :id"), {"id": org_with_member["notification_id"]}
    ).scalar()
    assert sent_at is None, "a failed delivery must remain pending, not be silently dropped"


def test_delivery_is_idempotent_across_repeated_runs(connection, org_with_member):
    first = CollectingTransport()
    run_notify_send(connection, transport=first, org_ids=[org_with_member["org_id"]])
    second = CollectingTransport()
    run_notify_send(connection, transport=second, org_ids=[org_with_member["org_id"]])

    assert len(first.messages) >= 1
    assert second.messages == [], "an already-sent alert must not be re-delivered"


def test_delivery_only_reaches_members_of_the_owning_org(connection, org_with_member):
    """A notification must never leak to another tenant's users."""
    transport = CollectingTransport()
    run_notify_send(connection, transport=transport, org_ids=[org_with_member["org_id"]])

    recipients = {message["To"] for message in transport.messages}
    assert recipients == {f"{org_with_member['user_id']}@example.test"}


# ---------------------------------------------------------------- digest


def test_digest_is_skipped_outside_the_local_send_hour(connection, org_with_member):
    """§8.3 sets a send hour, compared in the org's own timezone so nobody is emailed at 05:00."""
    transport = CollectingTransport()
    # 02:00 UTC is 03:00 in Paris - not the digest hour.
    result = run_daily_digest(
        connection,
        transport=transport,
        now=datetime(2026, 8, 13, 2, 0, tzinfo=UTC),
        org_ids=[org_with_member["org_id"]],
    )
    assert result["digests"] == 0
    assert transport.messages == []


def test_digest_is_not_sent_when_there_is_nothing_to_say(connection, org_with_member):
    """An empty daily email is how people learn to filter the sender."""
    transport = CollectingTransport()
    # 05:00 UTC is 07:00 in Paris during summer time - the configured hour.
    result = run_daily_digest(
        connection,
        transport=transport,
        now=datetime(2026, 8, 13, 5, 0, tzinfo=UTC),
        org_ids=[org_with_member["org_id"]],
    )
    assert result["digests"] == 0


def test_digest_lists_top_matches_when_there_are_some(connection, org_with_member):
    from bidpilot_ingestion.ids import new_id

    org_id = org_with_member["org_id"]
    connection.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id})

    notice_id = "ntc_digest_test"
    connection.execute(
        text(
            """
            INSERT INTO notices (id, source_refs, status, notice_type, country, title, cpv, nuts,
                                 lots, urls, provenance, version, docs_available, created_at, updated_at)
            VALUES (:id, '[]'::jsonb, 'active', 'competition', 'FR', 'Infogérance à surveiller',
                    ARRAY['72000000'], ARRAY['FR101'], '[]'::jsonb, '{}'::jsonb, '{}'::jsonb, 1,
                    false, now(), now())
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": notice_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO matches (id, org_id, notice_id, score, breakdown, state, created_at, updated_at)
            VALUES (:id, :org, :notice, 88, '{}'::jsonb, 'new', now(), now())
            ON CONFLICT (org_id, notice_id) DO NOTHING
            """
        ),
        {"id": new_id("mch"), "org": org_id, "notice": notice_id},
    )

    transport = CollectingTransport()
    result = run_daily_digest(
        connection,
        transport=transport,
        now=datetime(2026, 8, 13, 5, 0, tzinfo=UTC),
        org_ids=[org_with_member["org_id"]],
    )
    assert result["digests"] == 1
    body = transport.messages[0].get_content()
    assert "Infogérance à surveiller" in body
    assert "88" in body


def test_digest_sends_at_most_once_per_day(connection, org_with_member):
    from bidpilot_ingestion.ids import new_id

    org_id = org_with_member["org_id"]
    connection.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id})
    connection.execute(
        text(
            """
            INSERT INTO notifications (id, org_id, kind, payload, channels, created_at)
            VALUES (:id, :org, 'digest.daily', '{}'::jsonb, ARRAY['email'],
                    CAST(:now AS timestamptz) - interval '2 hours')
            """
        ),
        {"id": new_id("ntf"), "org": org_id, "now": datetime(2026, 8, 13, 5, 0, tzinfo=UTC)},
    )

    transport = CollectingTransport()
    result = run_daily_digest(
        connection,
        transport=transport,
        now=datetime(2026, 8, 13, 5, 0, tzinfo=UTC),
        org_ids=[org_with_member["org_id"]],
    )
    assert result["digests"] == 0


def test_digest_respects_a_non_paris_timezone(connection, org_with_member):
    """Two orgs in different zones must each get breakfast, not one of them dawn."""
    connection.execute(
        text("UPDATE orgs SET tz = 'Pacific/Noumea' WHERE id = :id"), {"id": org_with_member["org_id"]}
    )
    transport = CollectingTransport()
    # 05:00 UTC is 16:00 in Nouméa: not the digest hour there, even though it is in Paris.
    result = run_daily_digest(
        connection,
        transport=transport,
        now=datetime(2026, 8, 13, 5, 0, tzinfo=UTC),
        org_ids=[org_with_member["org_id"]],
    )
    assert result["digests"] == 0
    assert result["skipped"] >= 1


def test_alerts_older_than_the_run_are_still_delivered(connection, org_with_member):
    """Delivery is driven by `sent_at IS NULL`, not by recency, so a backlog after an outage
    drains rather than being skipped."""
    connection.execute(
        text("SELECT set_config('app.org_id', :org, true)"), {"org": org_with_member["org_id"]}
    )
    connection.execute(
        text("UPDATE notifications SET created_at = now() - interval '3 days' WHERE id = :id"),
        {"id": org_with_member["notification_id"]},
    )
    transport = CollectingTransport()
    assert run_notify_send(connection, transport=transport, org_ids=[org_with_member["org_id"]])["sent"] >= 1
