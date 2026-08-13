"""The email inbox connector (SPEC §6.5).

The cheapest coverage in the product, and the only one that reaches portals we cannot: every
org gets `sources+{org}@{domain}`, subscribes it to any portal's own alert emails, and whatever
that portal can announce arrives here - including behind a login, using the user's own access.
§21 requires it in Phase 1 for exactly that reason.

What this module will and will not infer is the whole design:

  * **Links are extracted; facts are not invented** (P1). An alert email is prose written for a
    human, in a format that changes without notice. The one element that is structurally
    unambiguous is a URL, so a URL is what we take. Titles, buyers and deadlines are read only
    where the *link itself* carries them, never guessed from surrounding text.
  * **A recognised link resolves to the real notice.** TED and BOAMP URLs carry a publication
    number and an `idweb`, so an email about a notice we already ingested links to that notice
    rather than creating a second, thinner copy of it.
  * **An unrecognised link still counts.** It becomes a tender candidate carrying its URL, in
    `analysis`, for a human to take forward (P6). That is the point of the connector: the
    portals worth covering this way are precisely the ones we have no adapter for.
  * **Nothing here writes market data.** An email is one org's private mail; a notice is the
    commons (§17.6). Candidates are org-scoped tenders, so a forwarded email cannot put a row
    in front of another tenant.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .ids import new_id

log = logging.getLogger(__name__)

#: Local part of the per-org address: `sources+{org_id}@{domain}` (SPEC §6.5).
INBOUND_LOCAL_PART = "sources"

#: Anything longer is not a notice link; alert emails carry tracking URLs of absurd length.
MAX_URL_LENGTH = 2048

#: One email should not be able to open hundreds of workspaces. Portal digests list a handful;
#: a message with more links than this is a newsletter, and the excess is recorded, not silently
#: dropped - see `InboundResult.skipped_links`.
MAX_LINKS_PER_EMAIL = 25

_URL_RE = re.compile(r"""https?://[^\s<>"'\]\)]+""", re.IGNORECASE)

# Trailing punctuation belongs to the sentence, not the URL.
_TRAILING_PUNCTUATION = ".,;:!?)»\"'>"


@dataclass(frozen=True)
class RecognisedNotice:
    """A link we can tie to a source we already ingest."""

    source_code: str
    external_id: str


@dataclass
class InboundResult:
    org_id: str | None = None
    links: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    linked_to_notice: int = 0
    duplicates: int = 0
    skipped_links: int = 0
    boilerplate_links: int = 0
    rejected_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "links": len(self.links),
            "created": len(self.created),
            "linked_to_notice": self.linked_to_notice,
            "duplicates": self.duplicates,
            "skipped_links": self.skipped_links,
            "boilerplate_links": self.boilerplate_links,
            "rejected_reason": self.rejected_reason,
        }


def org_id_from_address(address: str) -> str | None:
    """Extract the org from `sources+{org_id}@domain`, or None.

    Returns None rather than raising, and never falls back to "some org": an address we cannot
    read is mail we cannot attribute, and attributing it to the wrong tenant is worse than
    dropping it (§17.6).
    """
    if not address:
        return None

    # `Name <addr@host>` is normal in a To: header.
    match = re.search(r"<([^>]+)>", address)
    candidate = (match.group(1) if match else address).strip()

    if "@" not in candidate:
        return None
    local, _, _domain = candidate.partition("@")
    if "+" not in local:
        return None
    prefix, _, tag = local.partition("+")
    # The prefix is ours and compared loosely; the tag is an *identifier* and its case is
    # preserved. ULIDs are upper-case Crockford base32 (§18), so lower-casing the whole address
    # - which is the obvious thing to do with an email - silently destroys every real org id.
    if prefix.lower() != INBOUND_LOCAL_PART or not tag:
        return None
    if not re.fullmatch(r"org_[A-Za-z0-9_]+", tag):
        return None
    return tag


def _clean_url(raw: str) -> str | None:
    url = html.unescape(raw).strip().rstrip(_TRAILING_PUNCTUATION)
    if len(url) > MAX_URL_LENGTH or not url.lower().startswith(("http://", "https://")):
        return None
    return url


def extract_links(text_body: str | None, html_body: str | None) -> list[str]:
    """Every distinct http(s) link in the message, in the order it first appears.

    Both parts are scanned because portals differ: some send text-only, some HTML-only, and the
    HTML part often carries the link the text part summarises. Order is preserved and duplicates
    dropped, so the first occurrence - usually the one the sentence is about - wins.
    """
    seen: set[str] = set()
    ordered: list[str] = []

    for body in (text_body or "", html_body or ""):
        if not body:
            continue
        # href="..." first: in HTML the attribute is the link, and the visible text is a label.
        candidates = re.findall(r'href=["\']([^"\']+)["\']', body, flags=re.IGNORECASE)
        candidates += _URL_RE.findall(body)
        for raw in candidates:
            url = _clean_url(raw)
            if url and url not in seen:
                seen.add(url)
                ordered.append(url)
    return ordered


#: Link shapes that are part of the email, not part of the announcement.
#:
#: Every alert mail carries an unsubscribe link, a preferences page and often social buttons. Left
#: in, each one opens a workspace, and a connector that turns one alert into four consultations is
#: worse than no connector - the noise is what users would have to clean up by hand.
#:
#: Kept to unambiguous boilerplate. The bias is deliberate: a stray candidate can be dismissed in
#: one click, whereas a real tender filtered out silently is exactly the miss this product exists
#: to prevent (P1). Anything filtered is counted, never quietly dropped.
_BOILERPLATE_PATTERNS = (
    "unsubscribe",
    "desinscri",  # désinscription, se désinscrire
    "se-desabonner",
    "desabonnement",
    "optout",
    "opt-out",
    "preferences",
    "email-preferences",
    "manage-subscription",
    "gerer-abonnement",
    "mentions-legales",
    "politique-de-confidentialite",
    "privacy-policy",
    "/cgu",
    "/cgv",
    # "View this email in your browser" - present in almost every HTML alert, and it points at
    # the message itself rather than at any consultation in it.
    "view-online",
    "view-in-browser",
    "voir-en-ligne",
    "web-version",
    "webversion",
    "/newsletter/",
)

_BOILERPLATE_HOSTS = (
    "twitter.com",
    "x.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
)


#: A label shorter than this is "click here", not a tender name.
MIN_LABEL_LENGTH = 12

#: And a paragraph wrapped in an anchor is not a name either.
MAX_LABEL_LENGTH = 200

_ANCHOR_RE = re.compile(r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)

_TAG_RE = re.compile(r"<[^>]+>")


def link_labels(html_body: str | None) -> dict[str, str]:
    """The visible text a portal used for each link.

    Worth having because it is usually the consultation's actual name, and it is *the portal's
    own words for that exact link* - not something inferred from the surrounding prose, which is
    the line P1 draws. Where it is missing or useless ("cliquez ici"), the caller falls back to
    the subject rather than inventing a title.
    """
    if not html_body:
        return {}

    labels: dict[str, str] = {}
    for href, inner in _ANCHOR_RE.findall(html_body):
        url = _clean_url(href)
        if not url or url in labels:
            continue
        label = html.unescape(_TAG_RE.sub(" ", inner))
        label = re.sub(r"\s+", " ", label).strip()
        if MIN_LABEL_LENGTH <= len(label) <= MAX_LABEL_LENGTH:
            labels[url] = label
    return labels


def is_boilerplate(url: str) -> bool:
    """True for links that belong to the email's furniture rather than its announcement."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    if any(_host_is(host, domain) for domain in _BOILERPLATE_HOSTS):
        return True
    haystack = f"{parts.path}?{parts.query}".lower()
    return any(pattern in haystack for pattern in _BOILERPLATE_PATTERNS)


def _host_is(host: str, domain: str) -> bool:
    """Exact host, or a subdomain of it.

    A bare `endswith` is wrong in a way that matters here: `notted.europa.eu` ends with
    `ted.europa.eu`, so an attacker-registered lookalike in a forwarded email would be attributed
    to a real TED notice. The boundary has to be a dot or the start of the string.
    """
    return host == domain or host.endswith(f".{domain}")


def recognise(url: str) -> RecognisedNotice | None:
    """Tie a URL to a source and external id where its shape is unambiguous.

    Deliberately pattern-based rather than a fetch: recognising a link must be cheap, offline
    and side-effect free, because it runs on every link in every message. A URL that does not
    match is not an error - it is the long tail this connector exists to reach.
    """
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    path = unquote(parts.path)
    query = parse_qs(parts.query)

    if _host_is(host, "ted.europa.eu"):
        # https://ted.europa.eu/en/notice/-/detail/00512345-2026  and  /notice/00512345-2026
        match = re.search(r"/notice/(?:-/detail/)?(\d{5,8}-\d{4})", path)
        if match:
            return RecognisedNotice("eu-ted", match.group(1))
        for key in ("publicationNumber", "publication-number"):
            if query.get(key):
                return RecognisedNotice("eu-ted", query[key][0])

    if _host_is(host, "boamp.fr"):
        # https://www.boamp.fr/pages/avis/?idweb=26-12345  and  /avis/detail/26-12345
        for key in ("idweb", "id"):
            if query.get(key):
                return RecognisedNotice("fr-boamp", query[key][0])
        match = re.search(r"/avis/(?:detail/)?([0-9]{2}-[0-9]+)", path)
        if match:
            return RecognisedNotice("fr-boamp", match.group(1))

    return None


def candidate_title(label: str | None, subject: str | None, url: str, index: int, total: int) -> str:
    """The best title the message actually supports, in that order of preference.

    The link's own label first: it is the portal's name for that consultation. Then the subject,
    which describes the *alert* rather than the tender - so when a message carried several links
    it is numbered, because three workspaces all called "3 new consultations" are three rows
    nobody can tell apart. Finally the host, which at least says where it came from.

    None of these is invented. That matters more than elegance here: a title that reads like the
    notice's own, but was assembled from prose, is precisely the hallucination P1 forbids.
    """
    if label:
        return label
    base = (subject or "").strip() or urlsplit(url).netloc
    return f"{base} ({index + 1}/{total})" if total > 1 else base


def _resolve_org(connection: Connection, tag: str) -> str | None:
    """The canonical org id for an address tag, or None.

    Two steps, because mail is not a reliable carrier of case. The common path is an exact
    match, checked inside the org's own context so RLS confirms existence the same way every
    other read does. Only if that misses do we fall back to a case-insensitive comparison
    against the platform's org list - some MTAs normalise the local part to lower case, and a
    ULID that has been lower-cased would otherwise be an org that mysteriously does not exist.

    The fallback resolves to the *stored* id, so everything downstream uses the canonical value.
    """
    from .alerts import all_org_ids, set_org_context

    set_org_context(connection, tag)
    if connection.execute(text("SELECT 1 FROM orgs WHERE id = :org"), {"org": tag}).first():
        return tag

    folded = tag.lower()
    for candidate in all_org_ids(connection):
        if candidate.lower() == folded:
            set_org_context(connection, candidate)
            return candidate

    return None


def process_inbound_email(connection: Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Turn one received email into org-scoped tender candidates.

    Idempotent by `(org, url)`: portals resend alerts and webhooks retry, and neither should
    open a second workspace for the same consultation.
    """
    from .alerts import set_org_context

    result = InboundResult()

    org_id = org_id_from_address(payload.get("to") or "")
    if not org_id:
        result.rejected_reason = "unaddressable"
        log.warning("inbound email rejected: cannot read an org from %r", payload.get("to"))
        return result.as_dict()
    result.org_id = org_id

    org_id = _resolve_org(connection, org_id)
    if not org_id:
        result.rejected_reason = "unknown_org"
        log.warning("inbound email rejected: org %s does not exist", result.org_id)
        set_org_context(connection, None)
        return result.as_dict()
    result.org_id = org_id

    found = extract_links(payload.get("text"), payload.get("html"))
    labels = link_labels(payload.get("html"))
    links = [url for url in found if not is_boilerplate(url)]
    result.boilerplate_links = len(found) - len(links)
    result.skipped_links = max(0, len(links) - MAX_LINKS_PER_EMAIL)
    links = links[:MAX_LINKS_PER_EMAIL]
    result.links = links

    subject = payload.get("subject")
    for index, url in enumerate(links):
        recognised = recognise(url)
        notice_id: str | None = None
        if recognised:
            row = connection.execute(
                text(
                    """
                    SELECT n.id
                      FROM notices n
                     WHERE n.source_refs @> CAST(:ref AS jsonb)
                     LIMIT 1
                    """
                ),
                {
                    # Serialised rather than interpolated: an external id comes from a URL in a
                    # third party's email, and hand-built JSON would break on the first quote.
                    "ref": _json(
                        [
                            {
                                "source": recognised.source_code,
                                "external_id": recognised.external_id,
                            }
                        ]
                    )
                },
            ).first()
            notice_id = row[0] if row else None

        meta = {
            "discovered_via": "email",
            "source_url": url,
            "message_id": payload.get("message_id"),
            "from": payload.get("from"),
            **(
                {"source_code": recognised.source_code, "external_id": recognised.external_id}
                if recognised
                else {}
            ),
        }

        inserted = connection.execute(
            text(
                """
                INSERT INTO tenders (id, org_id, notice_id, title, stage, origin, meta,
                                     created_at, updated_at)
                SELECT :id, :org, :notice_id, :title, 'analysis', 'email',
                       CAST(:meta AS jsonb), now(), now()
                 WHERE NOT EXISTS (
                       SELECT 1 FROM tenders
                        WHERE org_id = :org AND meta->>'source_url' = :url
                 )
                RETURNING id
                """
            ),
            {
                "id": new_id("tnd"),
                "org": org_id,
                "notice_id": notice_id,
                "title": candidate_title(labels.get(url), subject, url, index, len(links)),
                "meta": _json(meta),
                "url": url,
            },
        ).first()

        if inserted:
            result.created.append(inserted[0])
            if notice_id:
                result.linked_to_notice += 1
        else:
            result.duplicates += 1

    connection.execute(
        text(
            """
            INSERT INTO events (id, org_id, kind, payload, created_at)
            VALUES (:id, :org, 'email.inbound', CAST(:payload AS jsonb), now())
            """
        ),
        {
            "id": new_id("evt"),
            "org": org_id,
            "payload": _json(
                {
                    "from": payload.get("from"),
                    "subject": subject,
                    "message_id": payload.get("message_id"),
                    "links": len(links),
                    "created": len(result.created),
                    "duplicates": result.duplicates,
                    "skipped_links": result.skipped_links,
                    "boilerplate_links": result.boilerplate_links,
                }
            ),
        },
    )

    set_org_context(connection, None)
    return result.as_dict()


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
