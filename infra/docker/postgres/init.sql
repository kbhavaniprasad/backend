-- ============================================================
-- AI Lead Engagement Platform — PostgreSQL Schema
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Tenants ────────────────────────────────────────────────
CREATE TYPE plan_type AS ENUM ('free', 'starter', 'professional', 'enterprise');

CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(255) NOT NULL,
    slug            VARCHAR(100) NOT NULL UNIQUE,
    plan            plan_type NOT NULL DEFAULT 'free',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    max_calls_day   INTEGER NOT NULL DEFAULT 100,
    retell_agent_id VARCHAR(255),         -- Tenant-specific Retell agent (optional)
    twilio_number   VARCHAR(20),          -- Tenant's dedicated phone number
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tenants_slug ON tenants(slug);
CREATE INDEX idx_tenants_is_active ON tenants(is_active);

-- ── Users ──────────────────────────────────────────────────
CREATE TYPE user_role AS ENUM (
    'super_admin', 'business_owner', 'sales_manager', 'agent_viewer'
);

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           VARCHAR(255) NOT NULL UNIQUE,
    hashed_password VARCHAR(255),
    full_name       VARCHAR(255) NOT NULL,
    role            user_role NOT NULL DEFAULT 'agent_viewer',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    is_verified     BOOLEAN NOT NULL DEFAULT FALSE,
    google_id       VARCHAR(255),
    avatar_url      TEXT,
    last_login      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_tenant_id ON users(tenant_id);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);

-- ── Refresh Tokens ────────────────────────────────────────
CREATE TABLE refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token       VARCHAR(512) NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens(user_id);
CREATE INDEX idx_refresh_tokens_token ON refresh_tokens(token);

-- ── Leads ─────────────────────────────────────────────────
CREATE TYPE lead_source AS ENUM (
    'facebook_ads', 'google_ads', 'linkedin_ads',
    'website_form', 'whatsapp', 'instagram_dm', 'crm', 'manual'
);

CREATE TYPE lead_status AS ENUM (
    'new', 'contacted', 'interested', 'follow_up_required',
    'meeting_booked', 'not_interested', 'no_response', 'qualified', 'disqualified'
);

CREATE TABLE leads (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_id         VARCHAR(255),             -- ID in source system (e.g. FB leadgen_id)
    source              lead_source NOT NULL,
    status              lead_status NOT NULL DEFAULT 'new',

    -- Contact info
    first_name          VARCHAR(100),
    last_name           VARCHAR(100),
    email               VARCHAR(255),
    phone               VARCHAR(30),
    company             VARCHAR(255),
    job_title           VARCHAR(255),
    city                VARCHAR(100),
    country             VARCHAR(100),

    -- Ad attribution
    ad_campaign_id      VARCHAR(255),
    ad_set_id           VARCHAR(255),
    ad_id               VARCHAR(255),
    utm_source          VARCHAR(100),
    utm_medium          VARCHAR(100),
    utm_campaign        VARCHAR(255),

    -- AI fields
    qualification_score FLOAT,                    -- 0.0–1.0 set by Agent A
    is_qualified        BOOLEAN,
    assigned_agent_id   UUID REFERENCES users(id),

    -- Meta
    raw_data            JSONB DEFAULT '{}',
    notes               TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    contacted_at        TIMESTAMPTZ,
    qualified_at        TIMESTAMPTZ
);

CREATE INDEX idx_leads_tenant_id ON leads(tenant_id);
CREATE INDEX idx_leads_status ON leads(status);
CREATE INDEX idx_leads_source ON leads(source);
CREATE INDEX idx_leads_phone ON leads(phone);
CREATE INDEX idx_leads_email ON leads(email);
CREATE INDEX idx_leads_created_at ON leads(created_at DESC);
CREATE INDEX idx_leads_tenant_status ON leads(tenant_id, status);

-- ── Lead Status History ───────────────────────────────────
CREATE TABLE lead_status_history (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id     UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    old_status  lead_status,
    new_status  lead_status NOT NULL,
    changed_by  UUID REFERENCES users(id),   -- NULL = system/AI
    reason      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_lead_status_history_lead_id ON lead_status_history(lead_id);

-- ── Meetings ──────────────────────────────────────────────
CREATE TYPE meeting_status AS ENUM ('scheduled', 'confirmed', 'completed', 'cancelled', 'rescheduled');
CREATE TYPE calendar_type  AS ENUM ('google', 'outlook', 'calendly', 'manual');

CREATE TABLE meetings (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    lead_id             UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    scheduled_by        UUID REFERENCES users(id),  -- NULL = booked by AI

    title               VARCHAR(500) NOT NULL,
    description         TEXT,
    meeting_link        TEXT,
    location            VARCHAR(500),
    status              meeting_status NOT NULL DEFAULT 'scheduled',
    calendar_type       calendar_type  NOT NULL DEFAULT 'google',
    calendar_event_id   VARCHAR(500),        -- ID in Google/Outlook Calendar

    starts_at           TIMESTAMPTZ NOT NULL,
    ends_at             TIMESTAMPTZ NOT NULL,
    duration_minutes    INTEGER NOT NULL DEFAULT 30,
    timezone            VARCHAR(100) DEFAULT 'UTC',

    ai_booked           BOOLEAN NOT NULL DEFAULT FALSE,   -- TRUE = booked by Agent A
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_meetings_tenant_id ON meetings(tenant_id);
CREATE INDEX idx_meetings_lead_id   ON meetings(lead_id);
CREATE INDEX idx_meetings_starts_at ON meetings(starts_at);
CREATE INDEX idx_meetings_status    ON meetings(status);

-- ── Calendar Integrations ─────────────────────────────────
CREATE TABLE calendar_integrations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    calendar_type   calendar_type NOT NULL,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT,
    token_expires   TIMESTAMPTZ,
    calendar_id     VARCHAR(500),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Agent Configurations ──────────────────────────────────
CREATE TABLE agent_configurations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_type      VARCHAR(20) NOT NULL DEFAULT 'A',   -- 'A' or 'B'
    retell_agent_id VARCHAR(255),
    retell_llm_id   VARCHAR(255),
    prompt_version  VARCHAR(50) NOT NULL DEFAULT '1.0.0',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    config          JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_agent_configurations_tenant_id ON agent_configurations(tenant_id);

-- ── Billing / Subscriptions ───────────────────────────────
CREATE TYPE billing_period AS ENUM ('monthly', 'annual');

CREATE TABLE subscriptions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    plan                plan_type NOT NULL,
    billing_period      billing_period NOT NULL DEFAULT 'monthly',
    stripe_customer_id  VARCHAR(255),
    stripe_sub_id       VARCHAR(255),
    current_period_start TIMESTAMPTZ NOT NULL,
    current_period_end   TIMESTAMPTZ NOT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Audit Log ─────────────────────────────────────────────
CREATE TABLE audit_logs (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID REFERENCES tenants(id) ON DELETE SET NULL,
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    action      VARCHAR(255) NOT NULL,
    resource    VARCHAR(100),
    resource_id UUID,
    ip_address  INET,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_tenant_id ON audit_logs(tenant_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);

-- ── Triggers: auto-update updated_at ─────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tenants_updated_at
    BEFORE UPDATE ON tenants FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_leads_updated_at
    BEFORE UPDATE ON leads FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_meetings_updated_at
    BEFORE UPDATE ON meetings FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── Seed: Default Super Admin Tenant ─────────────────────
INSERT INTO tenants (id, name, slug, plan)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Platform Admin',
    'platform-admin',
    'enterprise'
) ON CONFLICT DO NOTHING;
