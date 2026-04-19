# LinkedIn embedding-pipeline-rebuild post

Draft copy for announcing the 10.8× throughput unlock that opened the full federal Hansard semantic-search corpus. Pairs with blog post at `/blog/faster-embeddings-months-to-hours`.

**LinkedIn does not render markdown.** The block below is plain text — arrows (→) and bullets (•) are unicode characters and render fine, but there is no bold/italic. For emphasis on LinkedIn, use line breaks or ALL CAPS. (If you want unicode-bold like 𝗧𝗵𝗶𝘀, paste into a bold-text converter before posting.)

---

📊 From months to hours: I rebuilt Canadian Political Data's search pipeline this weekend.

The goal: every speech ever made in the Canadian House of Commons, searchable by meaning — ask "housing affordability" and get back debate moments that argue about gatekeepers and missing-middles, not just ones that use the exact phrase.

The problem: my baseline pipeline was producing 4.7 embeddings per second. The historical backfill (38th–43rd Parliaments, ~1 million speech chunks going back to 2004) would have taken 59 hours of continuous GPU. Weeks of elapsed time.

What I did:
 · Ran a proper three-way eval of embedding models — BGE-M3 vs Qwen3-0.6B vs Qwen3-4B — on 40 hand-built queries and 5,000 real Hansard chunks.
 · Found Qwen3-0.6B with instruction prompting beat the incumbent by +13% NDCG@10 at ~2× throughput.
 · Swapped the serving layer to HuggingFace TEI (length-sorted unpadded batching).
 · Rewrote the DB writes from per-row UPDATE to batched UNNEST.

The result: 50.9 chunks/sec end-to-end on the same laptop GPU. 10.8× faster. The full 44th Parliament re-embedded in 1h19m with zero errors.

The honest tradeoff: I lost 22% on cross-lingual recall (French query returning English results, and vice versa). Users search in one language at a time, so I accepted it — but it's in the commit history and the blog post, not hidden.

The historical backfill is running now. By early next week, the entire federal Hansard from 2004 forward will be semantically searchable.

Full write-up (with the numbers and the tradeoff rationale):
https://canadianpoliticaldata.ca/blog/faster-embeddings-months-to-hours

#CivicTech #OpenGov #Canada #SemanticSearch #OpenSource

---

## Alternate hooks (if the opening is weak-feeling at post-time)

- "I had a million speeches to embed and 7 days of GPU time. Here's how I cut it to 7 hours."
- "Most of a modern ML pipeline's slowness isn't the model — it's the plumbing. A case study."
- "Re-embedded the entire 44th Parliament Hansard corpus in 1 hour 19 minutes. Here's what that took."

## Post-publication checklist

- [ ] Verify blog post is live at the linked URL
- [ ] Check GPU / embed pipeline is actually healthy before posting — "running now" shouldn't be lies
- [ ] Post mid-week morning Eastern for best reach
- [ ] Pin to profile for a few days if traction picks up
