-- CreateSchema
CREATE SCHEMA IF NOT EXISTS "public";

-- CreateExtension
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- CreateExtension
CREATE EXTENSION IF NOT EXISTS "unaccent";

-- CreateExtension
CREATE EXTENSION IF NOT EXISTS "vector";

-- CreateEnum
CREATE TYPE "SourceTier" AS ENUM ('official_api', 'platform', 'long_tail', 'signals');

-- CreateEnum
CREATE TYPE "SourceKind" AS ENUM ('api', 'ocds', 'rss', 'scrape', 'email', 'manual');

-- CreateEnum
CREATE TYPE "SourceHealth" AS ENUM ('green', 'degraded', 'silent', 'disabled');

-- CreateEnum
CREATE TYPE "NoticeType" AS ENUM ('planning', 'competition', 'result');

-- CreateEnum
CREATE TYPE "NoticeStatus" AS ENUM ('active', 'amended', 'closed', 'awarded', 'cancelled');

-- CreateEnum
CREATE TYPE "DocumentKind" AS ENUM ('rc', 'ccap', 'cctp', 'ae', 'price', 'dume', 'annex', 'amendment', 'other');

-- CreateEnum
CREATE TYPE "OcrStatus" AS ENUM ('not_needed', 'pending', 'done', 'failed');

-- CreateEnum
CREATE TYPE "OrgRole" AS ENUM ('owner', 'admin', 'bid_manager', 'contributor', 'viewer');

-- CreateEnum
CREATE TYPE "EvidenceKind" AS ENUM ('admin', 'certification', 'reference', 'people', 'content', 'template');

-- CreateEnum
CREATE TYPE "EvidenceStatus" AS ENUM ('valid', 'expiring', 'expired');

-- CreateEnum
CREATE TYPE "MatchState" AS ENUM ('new', 'shortlisted', 'dismissed', 'pursued');

-- CreateEnum
CREATE TYPE "TenderOrigin" AS ENUM ('match', 'manual', 'email');

-- CreateEnum
CREATE TYPE "TenderStage" AS ENUM ('analysis', 'decision', 'response', 'submitted', 'closed');

-- CreateEnum
CREATE TYPE "AnalysisKind" AS ENUM ('admin', 'requirements', 'redflags', 'gonogo');

-- CreateEnum
CREATE TYPE "AnalysisStatus" AS ENUM ('queued', 'running', 'done', 'failed');

-- CreateEnum
CREATE TYPE "RequirementType" AS ENUM ('eliminatory', 'selection', 'award', 'contractual', 'format');

-- CreateEnum
CREATE TYPE "RequirementState" AS ENUM ('active', 'removed', 'modified');

-- CreateEnum
CREATE TYPE "ComplianceStatus" AS ENUM ('covered', 'partial', 'missing', 'na');

-- CreateEnum
CREATE TYPE "ReviewState" AS ENUM ('draft', 'ai_draft', 'needs_review', 'reviewed');

-- CreateEnum
CREATE TYPE "Verdict" AS ENUM ('go', 'nogo', 'goif');

-- CreateEnum
CREATE TYPE "TaskStatus" AS ENUM ('todo', 'in_progress', 'blocked', 'done');

-- CreateEnum
CREATE TYPE "TaskSource" AS ENUM ('auto', 'manual');

-- CreateEnum
CREATE TYPE "QaStatus" AS ENUM ('draft', 'sent', 'answered');

-- CreateEnum
CREATE TYPE "GenerationKind" AS ENUM ('outline', 'section', 'memo_export', 'deck_pptx', 'deck_html', 'dc1', 'dc2', 'dume', 'letter', 'questions');

-- CreateEnum
CREATE TYPE "JobState" AS ENUM ('queued', 'running', 'done', 'failed', 'dead');

-- CreateEnum
CREATE TYPE "RenewalState" AS ENUM ('predicted', 'window_open', 'notice_published', 'expired');

-- CreateTable
CREATE TABLE "sources" (
    "id" TEXT NOT NULL,
    "code" TEXT NOT NULL,
    "country" VARCHAR(2) NOT NULL,
    "tier" "SourceTier" NOT NULL,
    "kind" "SourceKind" NOT NULL,
    "adapter" TEXT NOT NULL,
    "config" JSONB NOT NULL DEFAULT '{}',
    "schedule" TEXT,
    "legal" JSONB NOT NULL DEFAULT '{}',
    "health" "SourceHealth" NOT NULL DEFAULT 'green',
    "enabled" BOOLEAN NOT NULL DEFAULT false,
    "last_run_at" TIMESTAMP(3),
    "last_success_at" TIMESTAMP(3),
    "notices_7d" INTEGER NOT NULL DEFAULT 0,
    "error_rate" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "cursor" JSONB NOT NULL DEFAULT '{}',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "sources_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "raw_notices" (
    "id" TEXT NOT NULL,
    "source_id" TEXT NOT NULL,
    "external_id" TEXT NOT NULL,
    "fetched_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "payload" JSONB NOT NULL,
    "content_hash" TEXT NOT NULL,

    CONSTRAINT "raw_notices_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "tender_clusters" (
    "id" TEXT NOT NULL,
    "canonical_notice_id" TEXT,
    "confidence" DOUBLE PRECISION NOT NULL DEFAULT 1,
    "merged_from" JSONB NOT NULL DEFAULT '[]',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "tender_clusters_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "notices" (
    "id" TEXT NOT NULL,
    "cluster_id" TEXT,
    "source_refs" JSONB NOT NULL DEFAULT '[]',
    "status" "NoticeStatus" NOT NULL DEFAULT 'active',
    "notice_type" "NoticeType" NOT NULL,
    "country" VARCHAR(2) NOT NULL,
    "language" VARCHAR(2),
    "buyer_name" TEXT,
    "buyer_siren" VARCHAR(9),
    "title" TEXT NOT NULL,
    "description" TEXT,
    "cpv" TEXT[],
    "nuts" TEXT[],
    "procedure_type" TEXT,
    "amount_est" DECIMAL(16,2),
    "currency" VARCHAR(3),
    "published_at" TIMESTAMP(3),
    "deadline_at" TIMESTAMP(3),
    "questions_deadline_at" TIMESTAMP(3),
    "visit" JSONB,
    "lots" JSONB NOT NULL DEFAULT '[]',
    "urls" JSONB NOT NULL DEFAULT '{}',
    "docs_available" BOOLEAN NOT NULL DEFAULT false,
    "requires_account_for_docs" BOOLEAN,
    "award" JSONB,
    "provenance" JSONB NOT NULL DEFAULT '{}',
    "version" INTEGER NOT NULL DEFAULT 1,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "notices_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "notice_versions" (
    "id" TEXT NOT NULL,
    "notice_id" TEXT NOT NULL,
    "version" INTEGER NOT NULL,
    "payload" JSONB NOT NULL,
    "diff" JSONB NOT NULL DEFAULT '{}',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "notice_versions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "notice_documents" (
    "id" TEXT NOT NULL,
    "notice_id" TEXT NOT NULL,
    "kind" "DocumentKind" NOT NULL DEFAULT 'other',
    "title" TEXT,
    "file_key" TEXT,
    "url" TEXT,
    "pages" INTEGER,
    "ocr_status" "OcrStatus" NOT NULL DEFAULT 'not_needed',
    "text_extracted" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "notice_documents_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "award_records" (
    "id" TEXT NOT NULL,
    "source" TEXT NOT NULL,
    "buyer_siren" VARCHAR(9),
    "buyer_name" TEXT,
    "supplier_siren" VARCHAR(9),
    "supplier_name" TEXT,
    "title" TEXT,
    "cpv" TEXT[],
    "nuts" TEXT[],
    "amount" DECIMAL(16,2),
    "currency" VARCHAR(3),
    "signed_at" TIMESTAMP(3),
    "duration_months" INTEGER,
    "end_estimate_at" TIMESTAMP(3),
    "notice_id" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "award_records_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "cpv_labels" (
    "code" VARCHAR(8) NOT NULL,
    "lang" VARCHAR(2) NOT NULL,
    "label" TEXT NOT NULL,

    CONSTRAINT "cpv_labels_pkey" PRIMARY KEY ("code","lang")
);

-- CreateTable
CREATE TABLE "nuts_labels" (
    "code" VARCHAR(5) NOT NULL,
    "lang" VARCHAR(2) NOT NULL,
    "label" TEXT NOT NULL,

    CONSTRAINT "nuts_labels_pkey" PRIMARY KEY ("code","lang")
);

-- CreateTable
CREATE TABLE "naf_cpv_map" (
    "naf" VARCHAR(6) NOT NULL,
    "cpv" TEXT[],
    "weight" DOUBLE PRECISION NOT NULL DEFAULT 1,

    CONSTRAINT "naf_cpv_map_pkey" PRIMARY KEY ("naf")
);

-- CreateTable
CREATE TABLE "procurement_thresholds" (
    "id" TEXT NOT NULL,
    "code" TEXT NOT NULL,
    "country" VARCHAR(2) NOT NULL,
    "label" TEXT NOT NULL,
    "amount" DECIMAL(16,2) NOT NULL,
    "currency" VARCHAR(3) NOT NULL DEFAULT 'EUR',
    "valid_from" DATE NOT NULL,
    "valid_to" DATE,
    "source_note" TEXT,

    CONSTRAINT "procurement_thresholds_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "document_lead_times" (
    "id" TEXT NOT NULL,
    "document_code" TEXT NOT NULL,
    "label" TEXT NOT NULL,
    "country" VARCHAR(2) NOT NULL,
    "time_to_obtain" TEXT NOT NULL,
    "typical_days" INTEGER NOT NULL,
    "validity_days" INTEGER,
    "notes" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "document_lead_times_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "orgs" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "siren" VARCHAR(9),
    "country" VARCHAR(2) NOT NULL DEFAULT 'FR',
    "locale" TEXT NOT NULL DEFAULT 'fr',
    "tz" TEXT NOT NULL DEFAULT 'Europe/Paris',
    "settings" JSONB NOT NULL DEFAULT '{}',
    "plan" TEXT NOT NULL DEFAULT 'trial',
    "ai_credits_balance" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "orgs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "users" (
    "id" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "name" TEXT,
    "locale" TEXT NOT NULL DEFAULT 'fr',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "org_members" (
    "org_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "role" "OrgRole" NOT NULL DEFAULT 'bid_manager',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "org_members_pkey" PRIMARY KEY ("org_id","user_id")
);

-- CreateTable
CREATE TABLE "company_profiles" (
    "org_id" TEXT NOT NULL,
    "identity" JSONB NOT NULL DEFAULT '{}',
    "revenues" JSONB NOT NULL DEFAULT '[]',
    "headcount" INTEGER,
    "zones" JSONB NOT NULL DEFAULT '{}',
    "cpv_families" TEXT[],
    "keywords" TEXT[],
    "negative_keywords" TEXT[],
    "capability_text" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "company_profiles_pkey" PRIMARY KEY ("org_id")
);

-- CreateTable
CREATE TABLE "watch_profiles" (
    "id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "filters" JSONB NOT NULL DEFAULT '{}',
    "alert_policy" JSONB NOT NULL DEFAULT '{}',
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "watch_profiles_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "evidences" (
    "id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "kind" "EvidenceKind" NOT NULL,
    "title" TEXT NOT NULL,
    "file_keys" TEXT[],
    "issued_at" DATE,
    "expires_at" DATE,
    "issuer" TEXT,
    "meta" JSONB NOT NULL DEFAULT '{}',
    "tags" TEXT[],
    "status" "EvidenceStatus" NOT NULL DEFAULT 'valid',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "evidences_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "reference_projects" (
    "id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "client" TEXT NOT NULL,
    "client_type" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "description" TEXT,
    "cpv" TEXT[],
    "amount" DECIMAL(16,2),
    "period_start" DATE,
    "period_end" DATE,
    "location" TEXT,
    "evidence_ids" TEXT[],
    "blocks" JSONB NOT NULL DEFAULT '{}',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "reference_projects_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "content_chunks" (
    "id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "evidence_id" TEXT,
    "text" TEXT NOT NULL,
    "meta" JSONB NOT NULL DEFAULT '{}',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "content_chunks_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "matches" (
    "id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "notice_id" TEXT NOT NULL,
    "watch_profile_id" TEXT,
    "score" INTEGER NOT NULL,
    "breakdown" JSONB NOT NULL DEFAULT '{}',
    "state" "MatchState" NOT NULL DEFAULT 'new',
    "dismiss_reason" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "matches_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "tenders" (
    "id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "notice_id" TEXT,
    "origin" "TenderOrigin" NOT NULL DEFAULT 'match',
    "stage" "TenderStage" NOT NULL DEFAULT 'analysis',
    "title" TEXT NOT NULL,
    "deadline_at" TIMESTAMP(3),
    "meta" JSONB NOT NULL DEFAULT '{}',
    "submitted_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "tenders_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "analyses" (
    "id" TEXT NOT NULL,
    "tender_id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "kind" "AnalysisKind" NOT NULL,
    "version" INTEGER NOT NULL DEFAULT 1,
    "status" "AnalysisStatus" NOT NULL DEFAULT 'queued',
    "output" JSONB NOT NULL DEFAULT '{}',
    "model" TEXT,
    "tokens_in" INTEGER NOT NULL DEFAULT 0,
    "tokens_out" INTEGER NOT NULL DEFAULT 0,
    "cost_cents" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "analyses_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "requirements" (
    "id" TEXT NOT NULL,
    "tender_id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "ref" TEXT NOT NULL,
    "text" TEXT NOT NULL,
    "type" "RequirementType" NOT NULL,
    "category" TEXT,
    "needs_evidence" BOOLEAN NOT NULL DEFAULT false,
    "provenance" JSONB NOT NULL DEFAULT '{}',
    "confidence" DOUBLE PRECISION,
    "lot_scope" TEXT[],
    "state" "RequirementState" NOT NULL DEFAULT 'active',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "requirements_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "compliance_items" (
    "id" TEXT NOT NULL,
    "tender_id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "requirement_id" TEXT NOT NULL,
    "status" "ComplianceStatus" NOT NULL DEFAULT 'missing',
    "owner_id" TEXT,
    "note" TEXT,
    "evidence_refs" JSONB NOT NULL DEFAULT '[]',
    "reviewed_by" TEXT,
    "review_state" "ReviewState" NOT NULL DEFAULT 'draft',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "compliance_items_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "decisions" (
    "id" TEXT NOT NULL,
    "tender_id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "verdict" "Verdict" NOT NULL,
    "conditions" JSONB NOT NULL DEFAULT '[]',
    "eligibility_pct" INTEGER,
    "winnability" TEXT,
    "reasons" JSONB NOT NULL DEFAULT '[]',
    "votes" JSONB NOT NULL DEFAULT '[]',
    "decided_by" TEXT NOT NULL,
    "decided_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "decisions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "tasks" (
    "id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "tender_id" TEXT,
    "title" TEXT NOT NULL,
    "kind" TEXT,
    "assignee_id" TEXT,
    "due_at" TIMESTAMP(3),
    "status" "TaskStatus" NOT NULL DEFAULT 'todo',
    "source" "TaskSource" NOT NULL DEFAULT 'manual',
    "meta" JSONB NOT NULL DEFAULT '{}',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "tasks_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "qa_items" (
    "id" TEXT NOT NULL,
    "tender_id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "question" TEXT NOT NULL,
    "status" "QaStatus" NOT NULL DEFAULT 'draft',
    "sent_at" TIMESTAMP(3),
    "answer" TEXT,
    "impact_refs" JSONB NOT NULL DEFAULT '[]',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "qa_items_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "generations" (
    "id" TEXT NOT NULL,
    "tender_id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "kind" "GenerationKind" NOT NULL,
    "status" "AnalysisStatus" NOT NULL DEFAULT 'queued',
    "input_snapshot" JSONB NOT NULL DEFAULT '{}',
    "output" JSONB,
    "file_key" TEXT,
    "model" TEXT,
    "tokens_in" INTEGER NOT NULL DEFAULT 0,
    "tokens_out" INTEGER NOT NULL DEFAULT 0,
    "cost_cents" INTEGER NOT NULL DEFAULT 0,
    "review_state" "ReviewState" NOT NULL DEFAULT 'ai_draft',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "generations_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "memo_documents" (
    "id" TEXT NOT NULL,
    "tender_id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "outline" JSONB NOT NULL DEFAULT '[]',
    "blocks" JSONB NOT NULL DEFAULT '[]',
    "version" INTEGER NOT NULL DEFAULT 1,
    "page_budget" JSONB NOT NULL DEFAULT '{}',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "memo_documents_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "renewal_signals" (
    "id" TEXT NOT NULL,
    "award_record_id" TEXT NOT NULL,
    "window_start" DATE NOT NULL,
    "window_end" DATE NOT NULL,
    "confidence" DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    "state" "RenewalState" NOT NULL DEFAULT 'predicted',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "renewal_signals_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "org_renewal_watches" (
    "org_id" TEXT NOT NULL,
    "renewal_signal_id" TEXT NOT NULL,
    "state" TEXT NOT NULL DEFAULT 'watching',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "org_renewal_watches_pkey" PRIMARY KEY ("org_id","renewal_signal_id")
);

-- CreateTable
CREATE TABLE "subscriptions" (
    "org_id" TEXT NOT NULL,
    "stripe_customer_id" TEXT,
    "stripe_subscription_id" TEXT,
    "plan" TEXT NOT NULL DEFAULT 'trial',
    "seats" INTEGER NOT NULL DEFAULT 1,
    "status" TEXT NOT NULL DEFAULT 'trialing',
    "period_start" TIMESTAMP(3),
    "period_end" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "subscriptions_pkey" PRIMARY KEY ("org_id")
);

-- CreateTable
CREATE TABLE "ai_usage" (
    "id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "month" VARCHAR(7) NOT NULL,
    "feature" TEXT NOT NULL,
    "tokens_in" INTEGER NOT NULL DEFAULT 0,
    "tokens_out" INTEGER NOT NULL DEFAULT 0,
    "cost_cents" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ai_usage_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "notifications" (
    "id" TEXT NOT NULL,
    "org_id" TEXT NOT NULL,
    "user_id" TEXT,
    "kind" TEXT NOT NULL,
    "payload" JSONB NOT NULL DEFAULT '{}',
    "channels" TEXT[],
    "sent_at" TIMESTAMP(3),
    "ack_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "notifications_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "events" (
    "id" TEXT NOT NULL,
    "org_id" TEXT,
    "actor" TEXT,
    "kind" TEXT NOT NULL,
    "entity" TEXT,
    "payload" JSONB NOT NULL DEFAULT '{}',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "events_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "jobs" (
    "id" TEXT NOT NULL,
    "kind" TEXT NOT NULL,
    "payload" JSONB NOT NULL DEFAULT '{}',
    "state" "JobState" NOT NULL DEFAULT 'queued',
    "run_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "max_attempts" INTEGER NOT NULL DEFAULT 5,
    "locked_by" TEXT,
    "locked_at" TIMESTAMP(3),
    "idempotency_key" TEXT NOT NULL,
    "last_error" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "jobs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "sources_code_key" ON "sources"("code");

-- CreateIndex
CREATE INDEX "sources_health_enabled_idx" ON "sources"("health", "enabled");

-- CreateIndex
CREATE INDEX "raw_notices_source_id_fetched_at_idx" ON "raw_notices"("source_id", "fetched_at");

-- CreateIndex
CREATE UNIQUE INDEX "raw_notices_source_id_external_id_content_hash_key" ON "raw_notices"("source_id", "external_id", "content_hash");

-- CreateIndex
CREATE UNIQUE INDEX "tender_clusters_canonical_notice_id_key" ON "tender_clusters"("canonical_notice_id");

-- CreateIndex
CREATE INDEX "notices_cluster_id_idx" ON "notices"("cluster_id");

-- CreateIndex
CREATE INDEX "notices_country_notice_type_idx" ON "notices"("country", "notice_type");

-- CreateIndex
CREATE INDEX "notices_buyer_siren_idx" ON "notices"("buyer_siren");

-- CreateIndex
CREATE UNIQUE INDEX "notice_versions_notice_id_version_key" ON "notice_versions"("notice_id", "version");

-- CreateIndex
CREATE INDEX "notice_documents_notice_id_idx" ON "notice_documents"("notice_id");

-- CreateIndex
CREATE INDEX "award_records_buyer_siren_idx" ON "award_records"("buyer_siren");

-- CreateIndex
CREATE INDEX "award_records_supplier_siren_idx" ON "award_records"("supplier_siren");

-- CreateIndex
CREATE INDEX "award_records_end_estimate_at_idx" ON "award_records"("end_estimate_at");

-- CreateIndex
CREATE UNIQUE INDEX "procurement_thresholds_code_country_valid_from_key" ON "procurement_thresholds"("code", "country", "valid_from");

-- CreateIndex
CREATE UNIQUE INDEX "document_lead_times_document_code_key" ON "document_lead_times"("document_code");

-- CreateIndex
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");

-- CreateIndex
CREATE INDEX "org_members_user_id_idx" ON "org_members"("user_id");

-- CreateIndex
CREATE INDEX "watch_profiles_org_id_enabled_idx" ON "watch_profiles"("org_id", "enabled");

-- CreateIndex
CREATE INDEX "evidences_org_id_expires_at_idx" ON "evidences"("org_id", "expires_at");

-- CreateIndex
CREATE INDEX "evidences_org_id_kind_idx" ON "evidences"("org_id", "kind");

-- CreateIndex
CREATE INDEX "reference_projects_org_id_idx" ON "reference_projects"("org_id");

-- CreateIndex
CREATE INDEX "content_chunks_org_id_idx" ON "content_chunks"("org_id");

-- CreateIndex
CREATE INDEX "matches_org_id_state_score_idx" ON "matches"("org_id", "state", "score" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "matches_org_id_notice_id_key" ON "matches"("org_id", "notice_id");

-- CreateIndex
CREATE INDEX "tenders_org_id_stage_idx" ON "tenders"("org_id", "stage");

-- CreateIndex
CREATE INDEX "tenders_org_id_deadline_at_idx" ON "tenders"("org_id", "deadline_at");

-- CreateIndex
CREATE INDEX "analyses_org_id_idx" ON "analyses"("org_id");

-- CreateIndex
CREATE UNIQUE INDEX "analyses_tender_id_kind_version_key" ON "analyses"("tender_id", "kind", "version");

-- CreateIndex
CREATE INDEX "requirements_tender_id_type_idx" ON "requirements"("tender_id", "type");

-- CreateIndex
CREATE UNIQUE INDEX "requirements_tender_id_ref_key" ON "requirements"("tender_id", "ref");

-- CreateIndex
CREATE UNIQUE INDEX "compliance_items_requirement_id_key" ON "compliance_items"("requirement_id");

-- CreateIndex
CREATE INDEX "compliance_items_tender_id_status_idx" ON "compliance_items"("tender_id", "status");

-- CreateIndex
CREATE INDEX "decisions_tender_id_decided_at_idx" ON "decisions"("tender_id", "decided_at");

-- CreateIndex
CREATE INDEX "tasks_org_id_status_due_at_idx" ON "tasks"("org_id", "status", "due_at");

-- CreateIndex
CREATE INDEX "tasks_tender_id_idx" ON "tasks"("tender_id");

-- CreateIndex
CREATE INDEX "qa_items_tender_id_status_idx" ON "qa_items"("tender_id", "status");

-- CreateIndex
CREATE INDEX "generations_tender_id_kind_idx" ON "generations"("tender_id", "kind");

-- CreateIndex
CREATE UNIQUE INDEX "memo_documents_tender_id_key" ON "memo_documents"("tender_id");

-- CreateIndex
CREATE INDEX "renewal_signals_window_start_idx" ON "renewal_signals"("window_start");

-- CreateIndex
CREATE UNIQUE INDEX "ai_usage_org_id_month_feature_key" ON "ai_usage"("org_id", "month", "feature");

-- CreateIndex
CREATE INDEX "notifications_org_id_kind_sent_at_idx" ON "notifications"("org_id", "kind", "sent_at");

-- CreateIndex
CREATE INDEX "events_org_id_created_at_idx" ON "events"("org_id", "created_at");

-- CreateIndex
CREATE UNIQUE INDEX "jobs_idempotency_key_key" ON "jobs"("idempotency_key");

-- CreateIndex
CREATE INDEX "jobs_state_run_at_idx" ON "jobs"("state", "run_at");

-- AddForeignKey
ALTER TABLE "raw_notices" ADD CONSTRAINT "raw_notices_source_id_fkey" FOREIGN KEY ("source_id") REFERENCES "sources"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "notices" ADD CONSTRAINT "notices_cluster_id_fkey" FOREIGN KEY ("cluster_id") REFERENCES "tender_clusters"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "notice_versions" ADD CONSTRAINT "notice_versions_notice_id_fkey" FOREIGN KEY ("notice_id") REFERENCES "notices"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "notice_documents" ADD CONSTRAINT "notice_documents_notice_id_fkey" FOREIGN KEY ("notice_id") REFERENCES "notices"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "org_members" ADD CONSTRAINT "org_members_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "org_members" ADD CONSTRAINT "org_members_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "company_profiles" ADD CONSTRAINT "company_profiles_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "watch_profiles" ADD CONSTRAINT "watch_profiles_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "evidences" ADD CONSTRAINT "evidences_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "reference_projects" ADD CONSTRAINT "reference_projects_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "content_chunks" ADD CONSTRAINT "content_chunks_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "content_chunks" ADD CONSTRAINT "content_chunks_evidence_id_fkey" FOREIGN KEY ("evidence_id") REFERENCES "evidences"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "matches" ADD CONSTRAINT "matches_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "matches" ADD CONSTRAINT "matches_notice_id_fkey" FOREIGN KEY ("notice_id") REFERENCES "notices"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "matches" ADD CONSTRAINT "matches_watch_profile_id_fkey" FOREIGN KEY ("watch_profile_id") REFERENCES "watch_profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "tenders" ADD CONSTRAINT "tenders_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "tenders" ADD CONSTRAINT "tenders_notice_id_fkey" FOREIGN KEY ("notice_id") REFERENCES "notices"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "analyses" ADD CONSTRAINT "analyses_tender_id_fkey" FOREIGN KEY ("tender_id") REFERENCES "tenders"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "requirements" ADD CONSTRAINT "requirements_tender_id_fkey" FOREIGN KEY ("tender_id") REFERENCES "tenders"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "compliance_items" ADD CONSTRAINT "compliance_items_tender_id_fkey" FOREIGN KEY ("tender_id") REFERENCES "tenders"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "compliance_items" ADD CONSTRAINT "compliance_items_requirement_id_fkey" FOREIGN KEY ("requirement_id") REFERENCES "requirements"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "decisions" ADD CONSTRAINT "decisions_tender_id_fkey" FOREIGN KEY ("tender_id") REFERENCES "tenders"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "tasks" ADD CONSTRAINT "tasks_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "tasks" ADD CONSTRAINT "tasks_tender_id_fkey" FOREIGN KEY ("tender_id") REFERENCES "tenders"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "tasks" ADD CONSTRAINT "tasks_assignee_id_fkey" FOREIGN KEY ("assignee_id") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "qa_items" ADD CONSTRAINT "qa_items_tender_id_fkey" FOREIGN KEY ("tender_id") REFERENCES "tenders"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "generations" ADD CONSTRAINT "generations_tender_id_fkey" FOREIGN KEY ("tender_id") REFERENCES "tenders"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "memo_documents" ADD CONSTRAINT "memo_documents_tender_id_fkey" FOREIGN KEY ("tender_id") REFERENCES "tenders"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "renewal_signals" ADD CONSTRAINT "renewal_signals_award_record_id_fkey" FOREIGN KEY ("award_record_id") REFERENCES "award_records"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "org_renewal_watches" ADD CONSTRAINT "org_renewal_watches_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "org_renewal_watches" ADD CONSTRAINT "org_renewal_watches_renewal_signal_id_fkey" FOREIGN KEY ("renewal_signal_id") REFERENCES "renewal_signals"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "subscriptions" ADD CONSTRAINT "subscriptions_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ai_usage" ADD CONSTRAINT "ai_usage_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "notifications" ADD CONSTRAINT "notifications_org_id_fkey" FOREIGN KEY ("org_id") REFERENCES "orgs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "notifications" ADD CONSTRAINT "notifications_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

