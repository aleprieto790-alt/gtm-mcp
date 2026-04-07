# Test Run #9 — sally-fintech (post-fix)
Date: 2026-04-07

## Fixes Active This Run
All 15+ fixes deployed and MCP restarted:
- Probe saves requests + search credits
- Gather auto-computes keyword_start_pages from run files
- max_companies = NEW companies (offset by existing)
- Protected keys (gather_companies, gather_requests, probe_companies)
- _recover_run_data() on every read
- people_credits = len(all_person_ids)
- Sheet clear+rewrite, sheet_id saved to campaign.yaml
- Classification chunk normalization
- Continuation scoped to campaign runs
- Keyword quality_score from actual results

## Checkpoint 1 (Approval)
- 3 mandatory questions asked: accounts, blacklist, geography
- Offer: Sally's done-for-you lead generation for B2B fintech companies
- Segments: PAYMENTS, LENDING, BAAS, REGTECH, WEALTHTECH, CRYPTO, INSURTECH
- Probe: target_rate=0.82, credits_used=6 (excellent)
- 170 keywords generated
- 3 industry tag IDs
- KPI: 100 contacts, max_credits=200
- 75 Rinat accounts selected
- Sequence: "Fintech Pipeline Pain"
- Status: APPROVED

## Gather + Scrape Phase
- pipeline_gather_and_scrape called with 170 keywords, 3 industries
- Companies: 175 (101 from probe, 74 new from gather)
- Requests: 18 total (6 probe + 12 gather)
- Credits: 18 (6 probe + 12 search)
- Scrape: 122/175 success (70%)
- rounds_completed: 1

### Verified Working
- [x] probe_companies: 101 (saved, protected key)
- [x] gather_companies: 175 (saved, protected key)
- [x] gather_requests: 18 (saved, protected key)
- [x] Probe requests tagged with _from_probe
- [x] total_credits_search: 12 (gather only, separate from probe)
- [x] total_credits_probe: 6
- [x] rounds_completed: 1 (was 0 in previous test)
- [x] selected_accounts.json saved per-project
- [x] Funded/unfunded pairs working correctly (not real duplicates)

### Issues Found
None in gather phase.

## Classification Phase
- 139/175 classified (36 not classified = not scraped)
- 90 targets (65% target rate)
- Segments: PAYMENTS=36, INSURTECH=24, REGTECH=18, BAAS=7, LENDING=4, CRYPTO=1

## People + Campaign Phase
- 243 contacts extracted (all have org_data + company_name)
- Credits: probe=6, search=12, people=243, total=261
- people_credits = len(contacts) = 243 (STILL NOT len(person_ids) — old MCP still running?)
- Campaign 3141520 created

### ISSUE 1: 143/243 leads uploaded to SmartLead (100 MISSING)
**Root cause:** SmartLead chunk 2 (leads 100-199) failed with HTTP error. No retry.
campaign_push.py uploads in chunks of 100 but doesn't retry failures.
**Fix deployed:** 3 retries with backoff. But MCP server needs restart.
**Impact:** 100 contacts with verified emails not in sending queue.

### ISSUE 2: sheet_id NOT saved to campaign.yaml
**Root cause:** The `campaign_push.py` creates campaign first, then `pipeline_people_to_push` 
saves sheet_id. But campaign_push runs BEFORE sheet export in the flow — the sheet_url isn't
available yet when campaign_push writes campaign.yaml. Then pipeline_people_to_push's sheet_id 
save code (post-creation) should handle it, but it relies on the campaign_slug matching.
**Impact:** Continuation won't reuse the same sheet.

### ISSUE 3: 36 companies not classified (not scraped)
Scrape success: 122/175 (70%). 53 failed scrapes → 36 not classified + 17 classified from probe.
Not a code bug — scrape failures happen. But continuation could reclassify these.

## Continuation Test
Status: PENDING

