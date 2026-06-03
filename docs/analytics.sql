-- NCCU Course Recommender — Product Analytics Queries
-- Run against Railway Postgres query_log table.

-- Top 10 most queried careers (with avg latency)
SELECT career, COUNT(*) AS total, ROUND(AVG(latency_ms)) AS avg_ms
FROM query_log
GROUP BY career
ORDER BY total DESC
LIMIT 10;

-- Error rate per career
SELECT career,
       COUNT(*) AS total,
       SUM(CASE WHEN success THEN 0 ELSE 1 END) AS errors,
       ROUND(100.0 * SUM(CASE WHEN success THEN 0 ELSE 1 END) / COUNT(*), 1) AS error_pct
FROM query_log
GROUP BY career
ORDER BY error_pct DESC;

-- Error breakdown by type
SELECT error_type, COUNT(*) AS count, MAX(created_at) AS last_seen
FROM query_log
WHERE success = false
GROUP BY error_type
ORDER BY count DESC;

-- Latency percentiles (last 7 days, successful only)
SELECT
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_ms,
    ROUND(AVG(latency_ms)) AS avg_ms,
    COUNT(*) AS total
FROM query_log
WHERE created_at > NOW() - INTERVAL '7 days' AND success = true;

-- Stage1 low-hit careers (may need better skill mapping)
SELECT career, ROUND(AVG(stage1_count), 1) AS avg_stage1_hits
FROM query_log
WHERE success = true
GROUP BY career
HAVING AVG(stage1_count) < 5
ORDER BY avg_stage1_hits;

-- LLM judge quality scores per career (avg of each dimension)
SELECT career,
       COUNT(judge_overall_score) AS judged,
       ROUND(AVG(judge_relevance_score), 2) AS relevance,
       ROUND(AVG(judge_grouping_score), 2) AS grouping,
       ROUND(AVG(judge_reason_score), 2) AS reason,
       ROUND(AVG(judge_diversity_score), 2) AS diversity,
       ROUND(AVG(judge_overall_score), 2) AS overall
FROM query_log
WHERE judge_overall_score IS NOT NULL
GROUP BY career
ORDER BY overall ASC;  -- worst-rated first, for improvement targeting

-- Lowest-quality individual recommendations (judge critiques for review)
SELECT created_at, career, judge_overall_score, judge_critique
FROM query_log
WHERE judge_overall_score IS NOT NULL
ORDER BY judge_overall_score ASC, created_at DESC
LIMIT 20;
