---
description: Launch a SmartLead campaign — from user input to DRAFT campaign with zero interaction during execution
argument-hint: "[website/document/description] — e.g. 'https://acme.com payments in Miami' or 'project=easystaff campaign=3070919 kpi=200'"
---

# /launch $ARGUMENTS

Full pipeline: user input → pre-flight resolution → gather → qualify → campaign.

**Once all params resolved and user confirms, pipeline runs with ZERO interaction until SmartLead campaign is created.**

---

## Step 0: Mode Detection

Parse `$ARGUMENTS` to determine which mode to run.

```
If campaign=<id_or_slug> found:
  → MODE 3 (append to existing campaign)
  → Call find_campaign(campaign_ref) to resolve project + campaign data
  → Validate via smartlead_get_campaign(id) that campaign exists in SmartLead
  → Extract: project, segment, country, sending_account_ids from campaign data
  → Skip: offer extraction, sequence generation, campaign creation

Else if project=<slug> found:
  → MODE 2 (new campaign on existing project)
  → Load project.yaml — verify offer_approved == true
  → Require: segment (must differ from existing project campaigns)
  → Skip: offer extraction

Else:
  → MODE 1 (fresh — full pipeline)
  → Parse URL/file/text from remaining arguments
```

---

## Step 1: Parse Input

Parse `$ARGUMENTS` to extract whatever user provided:

```
Input types:
- URL (starts with http) → will scrape for offer
- File path (ends with .md/.txt/.pdf) → will read for offer + filters + sequences
- Free text ("We sell payroll for SMBs") → offer description
- Combined ("easystaff.io IT consulting in Miami") → URL + segment + geo

Named parameters:
- project=<slug>          → existing project (Mode 2 or resolved from campaign)
- campaign=<id_or_slug>   → existing campaign (Mode 3)
- segment=<name>          → target segment (e.g. PAYMENTS)
- geo=<location>          → target geography (e.g. US, UK, "Miami")
- kpi=<number>            → target contacts (default 100, "+N" = N more on append)
- max_cost=<credits>      → credit cap (default 200)

Extract from free text:
- offer_source: URL | file | text
- segments: if mentioned (e.g. "IT consulting")
- geo: if mentioned (e.g. "in Miami")
- email_account_hint: if mentioned (e.g. "accounts with Renat")
- sequence_source: if file contains sequences
- blacklist_hint: if mentioned (e.g. "blacklist ES Global campaigns")
```

---

## Step 2: Offer Extraction

**MODE 1 only. MODE 2 and 3 skip this — offer already approved.**

Use **offer-extraction** skill:

- If URL → call `scrape_website(url)`, then extract from scraped text
- If file → read file from disk, extract from document text
- If text → extract from user's description
- If nothing → ask: "What's your website or what do you sell?"

Save extraction to project: `save_data(project, "project.yaml", extracted_data, mode="merge")`

**MODE 2/3**: Load existing `project.yaml` and confirm: "Using existing offer for {project_name}. Segments: {list}."

---

## Step 3: Resolve Mandatory Filters

**Segments** (MANDATORY):
- Mode 1: from extraction `segments[].name` or user input
- Mode 2: from `segment=` parameter (MUST be provided, MUST differ from existing campaigns)
- Mode 3: from campaign.yaml `segment` field (already set)
- If missing → ask: "What type of companies should I search for?"

**Geo** (MANDATORY):
- Mode 1: from extraction `apollo_filters.locations` or user input
- Mode 2: from `geo=` parameter or user input
- Mode 3: from campaign.yaml `country` field (already set)
- If missing → ask: "Which locations should I target?"

**DO NOT ASK about** (inferred automatically):
- Size → inferred from offer
- Industries → picked from 84 Apollo industries via apollo-filter-mapping skill
- Keywords → generated 20-30 from segments + seeds via apollo-filter-mapping skill
- Funding → from document if mentioned (prioritization filter)

Use **apollo-filter-mapping** skill to generate:
- 2-3 industry tag_ids
- 20-30 keywords (in Mode 3: seeded from previous run's best performers)
- Employee ranges

**Mode 3 intelligence reuse**: Load `keyword_leaderboard` from previous runs for this campaign. Use top performers as HIGH-PRIORITY seeds. Exclude exhausted keywords (all pages fetched).

---

## Step 4: Email Accounts

**Mode 1 and 2:**

```
Call smartlead_list_accounts()
```

All SmartLead accounts loaded (paginated, 100/page).

If user provided hint ("accounts with Renat"):
- Filter by name/email substring
- Show matches: "Found 3 accounts matching 'Renat': [list]. Use these?"

If user provided nothing:
- Show all accounts
- Ask: "Which email accounts should I use? (name filter, domain, or IDs)"

Save selected accounts to config.

**Mode 3: SKIP** — accounts already assigned to the campaign. Show: "Campaign {name} uses {N} accounts: {list}."

---

## Step 5: Sequence

**Mode 1 and 2:**

Priority:
1. User's document contains sequences → extract, validate, use
2. User provided separate sequence file → read, validate, use
3. Nothing provided → use **GOD_SEQUENCE** (default from email-sequence skill)

Validate against email-sequence skill checklist:
- ≤120 words per email
- SmartLead variables: `{{first_name}}`, `{{company_name}}`, `{{city}}`, `{{signature}}`
- A/B subjects on Email 1
- `<br>` for line breaks, no em dashes

**Mode 3: SKIP** — sequence already set on the campaign. Show: "Campaign {name} already has {N}-step sequence."

---

## Step 6: Blacklist

**Mode 1:**
- If user mentioned blacklist source → import:
  - SmartLead campaigns → `smartlead_export_leads(campaign_id)` → extract domains
  - Google Sheet URL → extract sheet_id, call `sheets_read(sheet_id)` → extract domain column
- If nothing → ask: "Any previous campaigns to blacklist? (say 'skip' if none)"

**Mode 2:**
- Auto-blacklist ALL leads from existing project campaigns:
  ```
  project_data = load_data(project, "project.yaml")
  for campaign in project_data.get("campaigns", []):
    existing = smartlead_export_leads(campaign["campaign_id"])
    blacklist_add(existing.data.domains)
  ```
- "Auto-blacklisted {N} domains from {M} existing campaigns in this project."
- Ask about additional sources

**Mode 3:**
- Auto-blacklist ALL leads from this campaign (export via `smartlead_export_leads(campaign_id)`)
- Auto-blacklist ALL companies from previous runs for this campaign
- "Loaded {N} existing leads + {M} companies from previous runs for dedup."
- No need to ask — dedup is automatic

Import blacklisted domains: `blacklist_add(domains)`

---

## Step 7: KPI & Cost Estimate

**Mode 1 and 2:**

**KPI** (defaults, user can override):
- target_contacts: 100
- contacts_per_company: 3
- min_target_companies: ceil(100/3) = 34

**Mode 3 (append):**

Parse KPI from `$ARGUMENTS`:
- `kpi=200` → absolute target. Offset = 200 - existing_leads. Pipeline targets the delta.
- `kpi=+100` → relative. Pipeline targets exactly 100 more.
- No kpi → default +100 more contacts.

```
existing_leads = smartlead_export_leads(campaign_id).count
kpi_target = parsed_kpi - existing_leads  (if absolute)
           = parsed_kpi                   (if relative "+N")
Show: "Campaign has {existing_leads} contacts. Will gather {kpi_target} more."
```

**Cost estimate** (all modes):
```
Call apollo_estimate_cost(filters, target_contacts=kpi_target, contacts_per_company=3)
```

Show: "Estimated cost: ~N search credits + ~M enrichment credits = ~T total"

**Max cost**: default 200 credits (user can override)

---

## Step 8: Approval Document

Create `pipeline-config.yaml` in project workspace:

```
save_data(project, "pipeline-config.yaml", {
  project: project_name,
  mode: "fresh" | "new_campaign" | "append",
  source: "apollo",
  destination: "smartlead",
  campaign: {id: campaign_id, slug: slug, name: name} | null,
  filters: {segments, geo, size, industries, keywords, funding},
  email_accounts: [{id, email, name}, ...],
  sequence: "GOD_SEQUENCE" | "existing" | extracted_sequence,
  blacklist: {sources: [...], domains_count: N},
  kpi: {target_contacts, contacts_per_company, existing_leads, delta},
  cost_estimate: {search, enrichment, total},
  status: "awaiting_approval"
})
```

Show summary to user — ALL params in one view:

```
Pipeline Ready (MODE {1|2|3}):
  Project: {name} {NEW | EXISTING}
  Campaign: {name} {NEW | APPEND to existing with N leads}

  Segments: PAYMENTS, LENDING, REGTECH
  Geo: United States, United Kingdom
  Size: 20-500 employees
  Keywords: 25 generated (5 seeded from previous runs)
  Industries: financial services (tag_id: 5567cdd6...)
  Funding: Series A-D (prioritization)

  Email Accounts: 3 selected (Renat accounts) {or "existing on campaign"}
  Sequence: GOD_SEQUENCE (4 steps) {or "existing on campaign"}
  Blacklist: 1,847 domains {+ N from existing campaign dedup}

  KPI: {target} contacts {+ existing = total}
  Estimated Cost: ~130 credits

  Proceed?
```

**ONE confirmation.** User says "proceed" → pipeline starts.

---

## Step 9: Pipeline Execution (ZERO INTERACTION)

Update status: `awaiting_approval → approved → running`

**Create run file with campaign link:**
```
run_data = {
  run_id: "run-{NNN}",
  project: project_slug,
  campaign_id: campaign_id | null,        ← set if Mode 3, null if Mode 1/2
  campaign_slug: campaign_slug | null,
  mode: "fresh" | "append",
  dedup_baseline: {                       ← Mode 3: snapshot of pre-existing state
    previous_run_companies: N,
    previous_run_contacts: N,
    seen_domains_count: N,
    seen_emails_count: N
  },
  kpi: {target_people, max_per_company, max_credits},
  ...
}
save_data(project, "runs/run-{NNN}.json", run_data)
```

**Mode 3 dedup setup:**
```
1. Load companies from ALL previous runs for this campaign:
   for run_id in campaign.run_ids:
     prev_run = load_data(project, f"runs/{run_id}.json")
     seen_domains.update(prev_run.companies.keys())

2. Load existing campaign leads:
   existing = smartlead_export_leads(campaign_id)
   seen_emails.update(lead.email for lead in existing.leads)

3. Pass seen_domains + seen_emails to round loop for dedup
```

Execute per **pipeline-state** skill round loop:

```
ROUND 1:
  For each keyword (1 per request, parallel):
    apollo_search_companies(filters={q_organization_keyword_tags: [kw],
      organization_locations: [...], organization_num_employees_ranges: [...]})
  For each industry tag_id (1 per request, parallel):
    apollo_search_companies(filters={organization_industry_tag_ids: [tag],
      organization_locations: [...], organization_num_employees_ranges: [...]})
  If funding: add organization_latest_funding_stage_cd to filters (parallel variants)

  → Dedup by domain (against ALL seen_domains from previous runs + current)
  → Scrape: scrape_website(url) for each company
  → Classify: use company-qualification skill (via negativa)
  → Check KPI: enough targets?
    YES → extract people
    NO → regenerate keywords (quality-gate skill, 10 angles) → ROUND 2

PEOPLE EXTRACTION (when enough targets):
  For each target company:
    Step 1 (FREE): apollo_search_people(domain=domain, person_seniorities=[...])
    Step 2 (PAID): apollo_enrich_people(person_ids=[...]) → verified emails

  → Dedup contacts by email (against seen_emails from campaign export)
  → Stop when: total_new_verified_contacts >= kpi_target
```

Track everything in run file per pipeline-state skill.

---

## Step 10: Campaign Creation / Lead Push

**Mode 1 (fresh) and Mode 2 (new campaign):**

```
1. smartlead_create_campaign(
     project=project_slug,
     name="{Segment} — {Geo}",
     sending_account_ids=[14, 27, 33],
     country="US",
     segment="PAYMENTS"
   )
   → Returns campaign_id + campaign_slug

2. smartlead_set_sequence(
     project=project_slug,
     campaign_slug=campaign_slug,
     campaign_id=campaign_id,
     steps=[{step: 1, day: 0, subject: "...", body: "...", subject_b: "..."}, ...]
   )

3. smartlead_add_leads(campaign_id=campaign_id, leads=[
     {email: "...", first_name: "...", last_name: "...",
      company_name: "Acme Inc" (normalized),
      custom_fields: {segment: "PAYMENTS", city: "Miami"}}
   ])

4. Update campaign.yaml:
   campaign.run_ids.append(run_id)
   campaign.total_leads_pushed += len(new_leads)
   save_data(project, f"campaigns/{slug}/campaign.yaml", campaign_data)

5. Update project.yaml campaigns index:
   project.campaigns.append({slug, campaign_id, segment, country, status})
   save_data(project, "project.yaml", project_data, mode="merge")

6. Update run file:
   run.campaign_id = campaign_id
   run.campaign_slug = campaign_slug
   run.campaign = {campaign_id, leads_pushed: N, pushed_at: timestamp}

7. save_data(project_slug, "contacts.json", all_contacts, mode="write")

8. user_email = get_config().configured["user_email"] value via get_config(), or ask user
   smartlead_send_test_email(campaign_id, user_email)
   → User checks inbox before activating

9. If Google Sheets configured (GOOGLE_SERVICE_ACCOUNT_JSON set):
   sheets_export_contacts(project_slug, campaign_slug)
   → Creates Google Sheet on Shared Drive with all contacts
   → Returns sheet_url for CRM link
```

**Mode 3 (append to existing campaign):**

```
1. SKIP campaign creation — campaign already exists
2. SKIP sequence — already set
3. smartlead_add_leads(campaign_id=existing_campaign_id, leads=new_deduped_leads)

4. Update campaign.yaml:
   campaign.run_ids.append(run_id)
   campaign.total_leads_pushed += len(new_leads)

5. Update run file:
   run.campaign = {campaign_id, leads_pushed: N, pushed_at: timestamp}

6. save_data(project, "contacts.json", new_deduped_leads, mode="append")
```

Campaign is **DRAFT** (Mode 1/2) or keeps existing status (Mode 3) — NEVER activated without explicit "activate" command.

---

## Step 11: Output

Update status: `running → completed`

Present to user:

**Mode 1 and 2:**
```
Campaign Created (DRAFT):
  Name: Payments — US
  SmartLead: https://app.smartlead.ai/app/email-campaigns-v2/{id}/analytics
  Contacts: 102 verified (saved to contacts.json)
  Google Sheet: https://docs.google.com/spreadsheets/d/{sheet_id}  ← if configured

  Cost: 23 search + 102 enrichment = 125 credits
  Stats: 487 companies → 52 targets (10.7%) → 102 contacts

  Top Keywords:
    payment gateway: 198 companies, 62 targets (31%)
    lending platform: 52 companies, 18 targets (35%)

  Say "activate" to start sending, or review the campaign in SmartLead first.
```

**Mode 3:**
```
Contacts Added to Existing Campaign:
  Campaign: Payments — US (ID: 3070919)
  SmartLead: https://app.smartlead.ai/app/email-campaigns-v2/3070919/analytics
  NEW Contacts: 48 verified (deduped against 102 existing)
  TOTAL Contacts: 150

  Cost: 15 search + 48 enrichment = 63 credits
  Stats: 312 new companies → 28 targets → 48 contacts
  Dedup: 74 companies skipped (already in campaign), 6 emails skipped (duplicates)

  Campaign is ACTIVE — new leads will enter the sending queue automatically.
  {or: Say "activate" to start sending.}
```

---

## Resume / Find More

If user says "find more" or "continue":
1. Load latest run file for this project
2. Determine which campaign it belongs to (from run.campaign_id)
3. Add new round (don't create new run)
4. Use regenerated keywords (different angle), seeded from leaderboard
5. Dedup against ALL companies in current run + previous runs for same campaign
6. Continue gather → scrape → classify → people
7. Push new contacts: `smartlead_add_leads(campaign_id, new_deduped_leads)`
8. Update campaign.yaml: `total_leads_pushed += N`
9. Show incremental stats: "+N new contacts (total: M)"

If user says "activate":
```
smartlead_activate_campaign(campaign_id, confirmation="I confirm")
```
→ Campaign goes LIVE. Reply monitoring begins.

---

## Mode Quick Reference

| Step | Mode 1 (Fresh) | Mode 2 (New Campaign) | Mode 3 (Append) |
|------|:-:|:-:|:-:|
| 0. Mode detection | default | `project=` | `campaign=` |
| 1. Parse input | full | project reuse | campaign reuse |
| 2. Offer extraction | RUN | SKIP | SKIP |
| 3. Filter generation | RUN | RUN (new segment) | RUN (seeded from prev) |
| 4. Email accounts | SELECT | SELECT | SKIP (existing) |
| 5. Sequence | GENERATE | GENERATE | SKIP (existing) |
| 6. Blacklist | ASK | AUTO + ASK | AUTO (full dedup) |
| 7. KPI + cost | RUN | RUN | RUN (with offset) |
| 8. Approval document | RUN | RUN | RUN |
| 9. Pipeline execution | RUN | RUN | RUN (with dedup) |
| 10. Campaign push | CREATE new | CREATE new | ADD to existing |
| 11. Output | campaign link | campaign link | incremental stats |
