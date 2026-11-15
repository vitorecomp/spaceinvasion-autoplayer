-- Statement statistics for the refactor baseline (plan phase 0.8, decision D15).
--
-- The official postgres image runs this file once, when it initializes an empty data
-- directory. The library itself is loaded by `shared_preload_libraries=pg_stat_statements`
-- in podman-compose.yml; this statement only exposes its view in the application database.
-- A database that already exists gets the same statement from `scripts/enable_pg_stats.py`.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
