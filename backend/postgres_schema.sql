-- Shadow only: MongoDB remains the application source of truth.
CREATE SCHEMA IF NOT EXISTS imladri_shadow;
CREATE TABLE IF NOT EXISTS imladri_shadow.schema_version (
    version integer PRIMARY KEY CHECK (version = 1)
);
INSERT INTO imladri_shadow.schema_version(version) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS imladri_shadow.runs (
    run_id uuid PRIMARY KEY,
    namespace text NOT NULL,
    environment text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    status text NOT NULL,
    report jsonb
);
CREATE TABLE IF NOT EXISTS imladri_shadow.documents (
    namespace text NOT NULL,
    collection text NOT NULL,
    id_key text NOT NULL CHECK (length(id_key) = 64),
    id_bson bytea NOT NULL,
    raw_bson bytea NOT NULL,
    canonical_json text NOT NULL,
    document jsonb,
    source_sha256 text NOT NULL CHECK (length(source_sha256) = 64),
    stored_sha256 text NOT NULL CHECK (length(stored_sha256) = 64),
    privacy_redacted boolean NOT NULL DEFAULT false,
    seen_run uuid NOT NULL REFERENCES imladri_shadow.runs(run_id),
    PRIMARY KEY(namespace, collection, id_key)
);
CREATE INDEX IF NOT EXISTS shadow_documents_scan ON imladri_shadow.documents(namespace, collection, id_key);
CREATE TABLE IF NOT EXISTS imladri_shadow.checkpoints (
    namespace text NOT NULL,
    collection text NOT NULL,
    run_id uuid NOT NULL REFERENCES imladri_shadow.runs(run_id),
    source_count bigint NOT NULL,
    source_fingerprint text NOT NULL,
    status text NOT NULL,
    PRIMARY KEY(namespace, collection)
);
CREATE TABLE IF NOT EXISTS imladri_shadow.participants (
    namespace text NOT NULL,
    user_id text NOT NULL,
    environment text NOT NULL,
    role text,
    closing boolean NOT NULL,
    age integer,
    height_cm integer,
    research_version text,
    research_revision text,
    legacy_research_submitted boolean NOT NULL DEFAULT false,
    membership_plan text,
    subscription_status text,
    topup_balance bigint,
    assessment_publish boolean NOT NULL DEFAULT false,
    source_key text NOT NULL,
    PRIMARY KEY(namespace, user_id)
);
ALTER TABLE imladri_shadow.participants ADD COLUMN IF NOT EXISTS assessment_publish boolean NOT NULL DEFAULT false;
ALTER TABLE imladri_shadow.participants ADD COLUMN IF NOT EXISTS adult_eligible boolean NOT NULL DEFAULT false;
ALTER TABLE imladri_shadow.participants ADD COLUMN IF NOT EXISTS age_is_numeric boolean NOT NULL DEFAULT false;
ALTER TABLE imladri_shadow.participants ADD COLUMN IF NOT EXISTS height_is_numeric boolean NOT NULL DEFAULT false;
ALTER TABLE imladri_shadow.participants ADD COLUMN IF NOT EXISTS couples_adult_attested boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS imladri_shadow.couples (
    namespace text NOT NULL,
    pair_id text NOT NULL,
    environment text NOT NULL,
    state text,
    inviter text,
    invitee text,
    source_key text NOT NULL,
    PRIMARY KEY(namespace, pair_id),
    FOREIGN KEY(namespace,inviter) REFERENCES imladri_shadow.participants(namespace,user_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(namespace,invitee) REFERENCES imladri_shadow.participants(namespace,user_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK(inviter IS NULL OR invitee IS NULL OR inviter<>invitee)
);
-- Participants may have been erased while durable billing/closed-pair records remain.
-- The mirror preserves those records; projection issues block any cutover claim.
CREATE TABLE IF NOT EXISTS imladri_shadow.billing_records (
    namespace text NOT NULL,
    source_key text NOT NULL,
    user_id text,
    kind text,
    payment_status text,
    currency text,
    amount_cents numeric,
    subscription_revenue_cents numeric,
    PRIMARY KEY(namespace, source_key)
);
CREATE TABLE IF NOT EXISTS imladri_shadow.history_tombstones (
    namespace text NOT NULL,
    event_id text NOT NULL,
    PRIMARY KEY(namespace, event_id)
);
CREATE TABLE IF NOT EXISTS imladri_shadow.history_events (
    namespace text NOT NULL,
    event_id text NOT NULL,
    owner_id text,
    pair_id text,
    domain text,
    question_id text,
    signature text,
    recorded_at timestamptz,
    revision text,
    attempt_id text,
    baseline boolean NOT NULL,
    visibility text,
    environment text,
    event_role text,
    event_closing boolean NOT NULL DEFAULT false,
    event_research_version text,
    event_legacy_consent boolean NOT NULL DEFAULT false,
    demographic_snapshot_id text,
    payload jsonb NOT NULL,
    PRIMARY KEY(namespace, event_id)
);
CREATE TABLE IF NOT EXISTS imladri_shadow.demographic_snapshots (
    namespace text NOT NULL,
    event_id text NOT NULL,
    age integer,
    height_cm integer,
    gender text,
    sex text,
    ethnicity text,
    education text,
    PRIMARY KEY(namespace, event_id),
    FOREIGN KEY(namespace,event_id) REFERENCES imladri_shadow.history_events(namespace,event_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS imladri_shadow.answer_observations (
    namespace text NOT NULL,
    event_id text NOT NULL,
    question_id text NOT NULL,
    edition text,
    answer_code integer,
    skipped boolean NOT NULL,
    PRIMARY KEY(namespace,event_id,question_id),
    FOREIGN KEY(namespace,event_id) REFERENCES imladri_shadow.history_events(namespace,event_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS shadow_history_owner_questions ON imladri_shadow.history_events(namespace,owner_id,question_id,recorded_at DESC);
CREATE INDEX IF NOT EXISTS shadow_answers_questions ON imladri_shadow.answer_observations(namespace,question_id,edition,event_id);
CREATE INDEX IF NOT EXISTS shadow_participant_research ON imladri_shadow.participants(namespace,research_version,user_id);
CREATE INDEX IF NOT EXISTS shadow_active_couples ON imladri_shadow.couples(namespace,state,inviter,invitee);
ALTER TABLE imladri_shadow.demographic_snapshots ADD COLUMN IF NOT EXISTS adult_eligible boolean NOT NULL DEFAULT false;
CREATE OR REPLACE VIEW imladri_shadow.research_observations AS
SELECT h.namespace,h.event_id,h.owner_id,h.pair_id,h.domain,h.recorded_at,h.attempt_id,h.baseline,
       o.question_id,o.edition,o.answer_code,o.skipped,
       d.age,d.height_cm,d.gender,d.sex,d.ethnicity,d.education
FROM imladri_shadow.history_events h
JOIN imladri_shadow.answer_observations o USING(namespace,event_id)
JOIN imladri_shadow.participants p ON p.namespace=h.namespace AND p.user_id=h.owner_id
JOIN imladri_shadow.demographic_snapshots d ON d.namespace=h.namespace AND d.event_id=h.demographic_snapshot_id
JOIN imladri_shadow.history_events dh ON dh.namespace=d.namespace AND dh.event_id=d.event_id
    AND dh.owner_id=h.owner_id AND dh.domain='demographics'
WHERE NOT p.closing AND p.role='member' AND p.adult_eligible AND d.adult_eligible
  AND COALESCE(h.payload->>'research_eligible','true')<>'false'
  AND h.payload->>'observation_subject' IS DISTINCT FROM 'third_party'
  AND h.domain IN ('everyday','intimacy','couples','research','assessment_repeat')
  AND (h.domain<>'assessment_repeat' OR p.assessment_publish)
  AND NOT h.event_closing AND h.environment=p.environment
  AND o.edition IS NOT NULL
  AND (o.answer_code IS NOT NULL OR o.skipped)
  AND ((h.event_research_version='all-answers-v1' AND p.research_version='all-answers-v1' AND h.event_role='member')
       OR (h.domain='research' AND h.event_legacy_consent AND p.legacy_research_submitted))
  AND (h.pair_id IS NULL OR EXISTS (
       SELECT 1 FROM imladri_shadow.couples c
       JOIN imladri_shadow.participants a ON a.namespace=c.namespace AND a.user_id=c.inviter
       JOIN imladri_shadow.participants b ON b.namespace=c.namespace AND b.user_id=c.invitee
       WHERE c.namespace=h.namespace AND c.pair_id=h.pair_id AND c.state='active' AND c.environment=h.environment
         AND h.owner_id IN (c.inviter,c.invitee)
         AND a.research_version='all-answers-v1' AND b.research_version='all-answers-v1'
         AND a.role='member' AND b.role='member' AND NOT a.closing AND NOT b.closing
         AND a.adult_eligible AND b.adult_eligible AND a.couples_adult_attested AND b.couples_adult_attested
         AND a.environment=h.environment AND b.environment=h.environment));
