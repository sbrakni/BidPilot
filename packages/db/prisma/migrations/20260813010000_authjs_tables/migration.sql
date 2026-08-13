-- Auth.js storage (SPEC §17.1).
--
-- Taken from `prisma migrate diff` and then trimmed: the generated script also wanted to drop
-- every `vector` column, the `tsv` generated column and their indexes, because those are
-- hand-written SQL that the Prisma schema does not model (ADR-0004). Applying it verbatim would
-- have silently deleted the search and embedding infrastructure.

-- AlterTable
ALTER TABLE "users" ADD COLUMN     "email_verified" TIMESTAMPTZ,
ADD COLUMN     "image" TEXT;

-- CreateTable
CREATE TABLE "accounts" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "type" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "provider_account_id" TEXT NOT NULL,
    "refresh_token" TEXT,
    "access_token" TEXT,
    "expires_at" INTEGER,
    "token_type" TEXT,
    "scope" TEXT,
    "id_token" TEXT,
    "session_state" TEXT,

    CONSTRAINT "accounts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "sessions" (
    "session_token" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "expires" TIMESTAMPTZ NOT NULL,

    CONSTRAINT "sessions_pkey" PRIMARY KEY ("session_token")
);

-- CreateTable
CREATE TABLE "verification_tokens" (
    "identifier" TEXT NOT NULL,
    "token" TEXT NOT NULL,
    "expires" TIMESTAMPTZ NOT NULL
);

-- CreateIndex
CREATE INDEX "accounts_user_id_idx" ON "accounts"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "accounts_provider_provider_account_id_key" ON "accounts"("provider", "provider_account_id");

-- CreateIndex
CREATE INDEX "sessions_user_id_idx" ON "sessions"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "verification_tokens_identifier_token_key" ON "verification_tokens"("identifier", "token");

-- AddForeignKey
ALTER TABLE "accounts" ADD CONSTRAINT "accounts_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- Expiry is checked on every session verification and every magic-link redemption, and both
-- tables accumulate rows that are dead but still scanned. These make the expiry predicate - and
-- any later sweep of stale rows - an index lookup rather than a scan.
CREATE INDEX "sessions_expires_idx" ON "sessions"("expires");
CREATE INDEX "verification_tokens_expires_idx" ON "verification_tokens"("expires");
