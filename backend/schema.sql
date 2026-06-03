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
