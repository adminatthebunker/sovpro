-- Install the extensions required by the semantic layer.
--
-- • `vector`  — pgvector, for embedding columns + HNSW/IVFFlat indexes.
-- • `unaccent` — FR/multilingual tsvector normalisation.
--
-- IMPORTANT INFRA NOTE: the stock `postgis/postgis:16-3.4` image does
-- NOT ship pgvector. Applying this migration against that image will
-- fail with:
--
--   ERROR:  extension "vector" is not available
--
-- To unblock: either
--   (a) switch the `db` service to an image that bundles both postgis
--       and pgvector (e.g. a small custom Dockerfile extending
--       postgis/postgis:16-3.4 + `apt-get install postgresql-16-pgvector`),
--   (b) mount the pgvector .so + .control files into the existing image,
--       or
--   (c) rebuild from pgvector/pgvector:pg16 + install postgis.
--
-- Decision TBD with the user. Keeping this migration in place so the
-- moment infra lands, `psql -f 0014_pgvector_setup.sql` completes the
-- story. See docs/plans/semantic-layer.md § "Stack decisions" for
-- context.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
