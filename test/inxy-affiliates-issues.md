# Inxy Affiliates Test — Issues Log
Date: 2026-04-07

## What Happened

### Good
- Agent correctly detected Mode 3: "append to campaign 3137079 with affiliate networks for inxy.io"
- `find_campaign("3137079")` → not found locally (correct — new project)
- `smartlead_get_campaign(3137079)` → found: "Inxy – Affiliate Network", ACTIVE, 46 accounts, 3-step sequence
- `scrape_website("https://inxy.io")` → success, extracted offer text
- Agent tried to enrich example companies in parallel — right idea
- **GEO CHECK**: Agent found "Excl. sanctioned countries, UK and US" — critical, this was the Run #5 failure

### Issues

#### ISSUE 1: `apollo_enrich_companies` ERROR — 'NoneType' object has no attribute 'get'
**Severity: HIGH**
Agent called `apollo_enrich_companies(domains: ["adsterra.com","propellerads.com","clickadu.com","imonetize.com","trafficstars.com","adverticals.com"])`
→ Error: `'NoneType' object has no attribute 'get'`

The tool crashed. The 9 example companies were NOT enriched. This means:
- No Apollo keywords extracted from examples
- No industry_tag_ids from examples  
- Filter generation will rely on LLM only (no Apollo data to seed)
- The whole "both sources combined" approach fails

Root cause: need to check `apollo_enrich_companies` code for the NoneType bug.

#### ISSUE 2: Only 6 of 9 domains passed to enrichment
Agent passed: adsterra.com, propellerads.com, clickadu.com, imonetize.com, trafficstars.com, adverticals.com
Missing: trafficinmedia.com, sundesiremedia.com, excellerate.com
Why? Agent may have truncated the list or made an error parsing the user's input.

#### ISSUE 3: TrafficStps instead of TrafficStars in user input
User typed "TrafficStps://excellerate.com" — typo. Agent shows "TrafficStps://excellerate.com" in the prompt. Not an MCP issue but may confuse domain extraction.

## Checkpoint 1 Results
- Geo confirmed: Europe, Asia, LatAm (excl. US, UK, sanctioned) — CORRECT
- Project created: "inxy-affiliate"
- Blacklist imported: 307 domains from campaign 3137079
- Keywords: 163 generated (CPA network, affiliate network, etc.) — CORRECT theme
- 4 classification agents spawned (71+71+71+72 = 285 companies)

## Run-001 Status
- 424 companies gathered, 285 scraped (67%)
- 296 classified, 27 targets (9% target rate — low but expected for niche)
- Segments: AFFILIATE_NETWORKS=27
- Credits: probe=6, search=17, people=0 (not yet extracted)
- Contacts: 0 (people extraction pending)

## Issues

#### ISSUE 1: `apollo_enrich_companies` crashed (FIXED)
See above. Null org in response. Fix committed (13b706e), needs MCP restart.

#### ISSUE 2: classify_chunk_2 missing .json extension (RECURRING)
Agent saved as `classify_chunk_2` instead of `classify_chunk_2.json`.
Merge script looks for `classify_chunk_2.json` → FileNotFoundError.
Agent recovered by reading the extensionless file and proceeding.
**This happened in run #8 too.** The Haiku agent for chunk 2 consistently drops the extension.
Root cause: agent behavior, not tool bug. But we could make the merge code try both.

#### ISSUE 3: 4 example companies blacklisted (can't be targets)
adsterra.com, propellerads.com, clickadu.com, trafficstars.com, sundesiremedia.com — 
already in campaign 3137079 → blacklisted → excluded from gather.
Only 4 of 9 examples are NOT blacklisted. This means Apollo enrichment of examples 
gave less data for filter seeding. Expected behavior but reduces quality.

#### ISSUE 4: 9 example companies NOT in companies dict
None of the 9 domains appear in gathered companies. Two reasons:
- 5 are blacklisted (excluded from gather)
- 4 are not in Apollo's company index (small affiliate networks)
The examples served only as ICP reference, not as gathered targets. OK but suboptimal.

#### ISSUE 5: Chunks in project root, not tmp/
10 chunk files in project root. tmp/ dir not used. Old MCP code still running.

#### ISSUE 6: Missing files
- pipeline-config.yaml: MISSING (agent didn't save approved config)
- selected_accounts.json: MISSING (using campaign 3137079's existing accounts)
- leads_for_push.json: MISSING (people extraction not done yet)

#### ISSUE 7: Leaderboard empty
keyword_leaderboard has 0 entries. `pipeline_compute_leaderboard` not called yet (or failed).

#### ISSUE 8: 9.5% target rate — very low
27 targets from 296 classified. Affiliate network niche is narrow — Apollo returns mostly
ad tech, SaaS, martech companies that aren't affiliate networks. This is a data quality issue,
not a code bug. The via negativa classifier correctly rejected ~270 non-affiliates.

## Status: People extraction pending
