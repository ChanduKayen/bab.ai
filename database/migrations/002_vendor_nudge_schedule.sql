BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS vendor_followup_nudges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_request_id UUID NOT NULL REFERENCES material_requests(id) ON DELETE CASCADE,
    vendor_id UUID NOT NULL REFERENCES vendors(vendor_id) ON DELETE CASCADE,
    invited_at TIMESTAMPTZ NOT NULL,
    next_nudge_at TIMESTAMPTZ NOT NULL,
    last_nudged_at TIMESTAMPTZ,
    nudge_stage SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (quote_request_id, vendor_id)
);

CREATE INDEX IF NOT EXISTS idx_vendor_followup_due
    ON vendor_followup_nudges (next_nudge_at);

COMMIT;
