/**
 * Inbound email webhook (SPEC §6.5).
 *
 * Every org has `sources+{org}@{INBOUND_EMAIL_DOMAIN}`. A user subscribes that address to a
 * portal's own alert emails, the provider (Postmark, SES, Resend) POSTs each message here, and
 * whatever that portal announces becomes visible in BidPilot - including portals behind a login,
 * using the user's own access. §21 requires it in Phase 1.
 *
 * This endpoint does as little as possible: authenticate the provider, take the message, hand it
 * to the queue. Parsing happens in the ingestion worker, where the rest of the sourcing engine
 * lives - and where a malformed message costs a retry instead of an HTTP error that a provider
 * would answer by re-sending the same broken mail.
 *
 * It carries no user session, because there is no user: the caller is a machine. Its credential
 * is a shared secret, and the endpoint fails closed if none is configured - an inbound route that
 * accepts anything is an open door into every tenant's workspace.
 */

import { randomUUID, timingSafeEqual } from "node:crypto";

import {
  Body,
  Controller,
  Headers,
  HttpCode,
  Post,
  ServiceUnavailableException,
  UnauthorizedException,
} from "@nestjs/common";
import { ApiExcludeEndpoint } from "@nestjs/swagger";

import { getPrisma } from "@bidpilot/db";

/** Header the provider is configured to send. */
const SECRET_HEADER = "x-bidpilot-inbound-secret";

/** Bodies above this are not alert emails; they are attachments we did not ask for. */
const MAX_BODY_CHARS = 512 * 1024;

export type InboundEmail = {
  to?: string;
  from?: string;
  subject?: string;
  text?: string;
  html?: string;
  messageId?: string;
};

export type InboundAccepted = { accepted: true; jobId: string | null };

/**
 * Compare without leaking length or content through timing.
 *
 * `timingSafeEqual` throws on length mismatch, which would itself be a length oracle, so both
 * sides are hashed to a fixed width first.
 */
function secretMatches(presented: string, expected: string): boolean {
  const a = Buffer.from(presented);
  const b = Buffer.from(expected);
  const width = Math.max(a.length, b.length);
  const padded = (buf: Buffer) => Buffer.concat([buf], width);
  return a.length === b.length && timingSafeEqual(padded(a), padded(b));
}

@Controller({ path: "inbound", version: "1" })
export class InboundController {
  @Post("email")
  @HttpCode(202)
  @ApiExcludeEndpoint()
  async receive(
    @Headers(SECRET_HEADER) presented: string | undefined,
    @Body() body: InboundEmail,
  ): Promise<InboundAccepted> {
    const expected = process.env.INBOUND_EMAIL_WEBHOOK_SECRET;
    if (!expected) {
      // Refusing is the safe default: without a secret this route would accept mail from
      // anyone, and the mail decides which org a candidate lands in.
      throw new ServiceUnavailableException("inbound email is not configured");
    }
    if (!presented || !secretMatches(presented, expected)) {
      throw new UnauthorizedException("invalid inbound secret");
    }

    const text = (body.text ?? "").slice(0, MAX_BODY_CHARS);
    const html = (body.html ?? "").slice(0, MAX_BODY_CHARS);
    if (!body.to) {
      // Nothing to attribute it to. 202 rather than 400: the provider cannot fix this by
      // retrying, and answering with an error would have it re-deliver the same message.
      return { accepted: true, jobId: null };
    }

    // The message id is the natural idempotency key - providers retry on any non-2xx, and a
    // retried delivery must not open a second workspace. Absent one, a random key means the
    // message is processed rather than silently dropped; the handler dedupes by (org, url).
    const messageId = body.messageId ?? `no-message-id:${randomUUID()}`;
    const jobId = `job_${randomUUID().replace(/-/g, "").slice(0, 24)}`;

    const inserted = await getPrisma().$queryRaw<Array<{ id: string }>>`
      INSERT INTO jobs (id, kind, payload, state, run_at, idempotency_key, created_at, updated_at)
      VALUES (
        ${jobId}, 'email.inbound',
        ${JSON.stringify({
          to: body.to,
          from: body.from ?? null,
          subject: body.subject ?? null,
          text,
          html,
          message_id: body.messageId ?? null,
        })}::jsonb,
        'queued', now(), ${`email.inbound:${messageId}:1`}, now(), now()
      )
      ON CONFLICT (idempotency_key) DO NOTHING
      RETURNING id
    `;

    return { accepted: true, jobId: inserted[0]?.id ?? null };
  }
}
