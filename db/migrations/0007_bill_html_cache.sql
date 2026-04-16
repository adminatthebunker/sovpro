-- Offline HTML cache for bill pages.
--
-- Socrata gives us bill number / title / current status / reading PDFs,
-- but NOT sponsor / party / full event history — those only live on
-- per-bill HTML pages at nslegislature.ca (URL already stored in
-- bills.source_url). We fetch each page once, store the raw HTML here,
-- and do all parsing offline. That makes the parser fast to iterate on
-- (milliseconds vs. re-hitting the network for ~1 hour per run) and
-- decouples extraction logic from rate-limited HTTP work.
--
-- Matches the federal openparliament cache pattern (0004): persist raw
-- upstream payload, parse from the cache, track last_error for
-- observability without blocking ingestion.

ALTER TABLE bills
  ADD COLUMN IF NOT EXISTS raw_html          TEXT,
  ADD COLUMN IF NOT EXISTS html_fetched_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS html_last_error   TEXT,
  ADD COLUMN IF NOT EXISTS html_last_error_at TIMESTAMPTZ;

-- Fast lookup for "which bills still need HTML fetched / re-fetched?"
CREATE INDEX IF NOT EXISTS idx_bills_html_needed
    ON bills (id)
    WHERE raw_html IS NULL;
