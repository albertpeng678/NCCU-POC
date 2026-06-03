-- NCCU Course Recommender — query_log table
-- Run once in Railway Postgres Query tab after provisioning the Postgres service.

CREATE TABLE IF NOT EXISTS query_log (
    id                      SERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ DEFAULT NOW(),

    -- Phase 1: Request metadata (written immediately)
    career                  VARCHAR(100) NOT NULL,
    success                 BOOLEAN NOT NULL,
    latency_ms              INTEGER,
    stage1_count            INTEGER,
    result_core_count       INTEGER,
    result_supporting_count INTEGER,
    result_extended_count   INTEGER,
    error_type              VARCHAR(50),
    error_message           TEXT,

    -- Phase 2: LLM judge scores (written async, ~5-10s later)
    judge_relevance_score   SMALLINT,
    judge_grouping_score    SMALLINT,
    judge_reason_score      SMALLINT,
    judge_diversity_score   SMALLINT,
    judge_overall_score     SMALLINT,
    judge_critique          TEXT,
    judge_evaluated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_query_log_career ON query_log(career);
CREATE INDEX IF NOT EXISTS idx_query_log_created_at ON query_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_query_log_success ON query_log(success);
CREATE INDEX IF NOT EXISTS idx_query_log_judge_overall ON query_log(judge_overall_score);

CREATE TABLE IF NOT EXISTS qa_session (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_interaction_id TEXT,
    turn_count          INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS qa_turn (
    id              SERIAL PRIMARY KEY,
    session_id      UUID REFERENCES qa_session(id) ON DELETE CASCADE,
    turn_number     INTEGER NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    question        TEXT NOT NULL,
    answer          TEXT,
    citation_count  INTEGER,
    citations_json  JSONB,
    followup_json   JSONB,
    latency_ms      INTEGER,
    success         BOOLEAN NOT NULL,
    error_type      VARCHAR(50),
    error_message   TEXT,
    judge_faithfulness  SMALLINT,
    judge_relevancy     SMALLINT,
    judge_context_prec  SMALLINT,
    judge_overall       SMALLINT,
    judge_critique      TEXT,
    judge_evaluated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_qa_turn_session ON qa_turn(session_id);
CREATE INDEX IF NOT EXISTS idx_qa_turn_created ON qa_turn(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_turn_judge ON qa_turn(judge_overall);
