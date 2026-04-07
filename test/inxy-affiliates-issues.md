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

## Status: IN PROGRESS — waiting for agent to continue after geo check
