# Leadgen Pipeline Manager

Orchestrate the /leadgen pipeline as a 7-phase sequence with **exactly 2 human checkpoints** and full autonomous execution between them.

**Required skills**: offer-extraction, apollo-filter-mapping, company-qualification, quality-gate, email-sequence, pipeline-state, io-state-safe, silence-protocol, resume-checkpoint

## Two Checkpoints — Nothing Else Blocks

```
CHECKPOINT 1: Strategy Approval (before spending Apollo credits)
  Shows: offer + filters + probe + cost estimate + KPI + accounts + sequence plan
  User says: "proceed"

  ── everything between is AUTONOMOUS ──

CHECKPOINT 2: Launch Approval (before campaign goes live)  
  Shows: campaign link + settings + leads uploaded + Google Sheet + test email sent
  User says: "activate"
```

**No other human-blocking points.** Classification is autonomous (97% accuracy via negativa). Sequence generation is autonomous (GOD_SEQUENCE 12 rules). Keyword regeneration is autonomous (quality-gate 10 angles). The agent runs the full pipeline without asking.

## Session Setup

1. **Detect mode** from /launch arguments:
   - `campaign=<id_or_slug>` → **MODE 3** (append to existing campaign)
   - `project=<slug>` → **MODE 2** (new campaign on existing project)
   - else → **MODE 1** (fresh — full pipeline)

2. **Resolve project slug**:
   - Mode 1: from website domain, project name, or user input
   - Mode 2: from `project=` parameter → verify `load_data(project, "project.yaml")` exists
     - Check `segment` not already used: `existing = project.get("campaigns", [])` → if any `c["segment"] == segment` → error: "Segment {segment} already has campaign {c['slug']}. Use `/launch campaign={c['slug']}` to append."
   - Mode 3: call `find_campaign(campaign_ref)` → returns `{project, slug, data}` → project slug from result
     - Then validate SmartLead: `smartlead_get_campaign(campaign_id)` → verify status is not STOPPED
     - If campaign not found locally → error: "Campaign not found. Check ID/slug."
     - If campaign STOPPED in SmartLead → error: "Campaign is stopped. Cannot append."

3. Generate session_id: `leadgen-{project-slug}-{YYYYMMDD}-{HHMMSS}`

4. Check for existing state: `load_data(project, "state.yaml")`
   - If state exists and not completed → invoke resume-checkpoint skill
   - If no state → initialize state.yaml

5. **Initialize state.yaml based on mode:**
   - Set `mode`: `"fresh"` (Mode 1) | `"new_campaign"` (Mode 2) | `"append"` (Mode 3)
   - Mode 1: all 7 phases as "pending"
   - Mode 2: `offer_extraction: "skipped"`, rest "pending"
   - Mode 3: `offer_extraction: "skipped"`, `sequence_generation: "skipped"`, rest "pending"
   - Set `active_campaign_slug` (Mode 3: from campaign, Mode 2: null until created)
   - Set `active_campaign_id` (Mode 3: from campaign, Mode 2: null until created)
   - Set `active_run_id` (new run-{NNN})

## Data Layers (L0/L1/L2/L3)

| Layer | Description | Files |
|-------|-------------|-------|
| L0 Config | API keys, user settings | `~/.gtm-mcp/config.yaml`, `.env` |
| L1 Directives | User-approved decisions (immutable once approved) | `project.yaml` (offer), FilterSnapshots in run file |
| L2 Operational | Working data (can be regenerated) | `runs/run-{id}.json` (companies, scrapes, classifications, contacts) |
| L3 Artifacts | Final outputs (versioned) | `campaigns/`, `qualified/v{N}.json` |

**Contract**: L1 decisions (approved offer, approved filters) can NEVER be overridden by L2 operations.

---

## Phase 1: Offer Extraction (AUTONOMOUS)

**Gate in**: None (entry point)
**Skill**: offer-extraction
**Human gate**: NONE — result shown in Checkpoint 1

**Mode 1 (fresh):**
1. Determine input type:
   - Website URL → 3-layer scraping fallback
   - Strategy document → read file from disk
   - Chat description → extract from conversation
2. Extract structured offer_summary
3. Save: `save_data(project, "project.yaml", offer_data, mode="merge")`
4. DO NOT ask for approval — proceed to Phase 2. Offer shown in Checkpoint 1 document.

On complete: update state → `phase_states.offer_extraction: "completed"`

**Mode 2/3 (existing project):** SKIP.
- `project_data = load_data(project, "project.yaml")`
- Verify `offer_approved == true`
  - If `offer_approved` is false or missing → error: "Offer not approved. Run `/launch {url}` first."
- State already set to `offer_extraction: "skipped"`

**L1 artifact**: project.yaml with offer (becomes immutable after Checkpoint 1 approval)

## Phase 2: Filter Generation (AUTONOMOUS)

**Gate in**: offer extracted or loaded
**Skill**: apollo-filter-mapping
**Human gate**: NONE — result shown in Checkpoint 1

**All modes run this phase**, but with different intelligence:

1. Call `apollo_get_taxonomy()` for industry tag_id mapping
2. Read filter_intelligence.json for seed recommendations (if exists)
3. **Mode 3 extra**: Load `keyword_leaderboard` from previous runs for this campaign.
   Use top performers (by `quality_score`) as HIGH-PRIORITY seeds.
   Exclude exhausted keywords (all pages fetched in previous runs).
4. Generate filters: industries (tag_ids), keywords (80-100), locations, sizes
5. Create FilterSnapshot fs-001 in run file (Mode 3: `parent_id` = last snapshot from previous run)
6. Probe: 1 request per top-3 tag_ids + top-3 keywords (**6 credits max, same as magnum-opus**)
   - Mode 3: "Seeded from {N} proven keywords. {M} exhausted keywords excluded."
7. DO NOT ask for approval — proceed to Phase 3.

On complete: update state → `phase_states.filter_generation: "completed"`

**L1 artifact**: FilterSnapshot in run file (becomes immutable after Checkpoint 1 approval)

## Phase 3: Cost Gate — CHECKPOINT 1 (Strategy Approval)

**Gate in**: filters generated, probe complete
**Human gate**: **YES — this is the ONLY pre-gathering approval**

Build and present the **Strategy Approval Document** combining Phases 1+2+cost estimate:

```
save_data(project, "pipeline-config.yaml", {
  project, mode, source: "apollo", destination: "smartlead",
  offer: {product, segments, target_roles, exclusions},
  filters: {keywords, industries, geo, size, funding},
  probe: {breakdown, total_available, credits_used: 6},
  cost_estimate: {search, enrichment, total, max_cap},
  kpi: {target_contacts, contacts_per_company, existing_leads, delta},
  email_accounts: [{id, email, name}],
  sequence: "GOD_SEQUENCE (4 steps, Day 0/3/7/14)" | "from document",
  blacklist: {sources, domains_count},
  status: "awaiting_approval"
})
```

**Present to user — ALL params in one view:**

```
Strategy Document:

  OFFER:
    Product: {primary_offer}
    Segments: {segments}
    Target Roles: {target_roles.primary}
    Exclusions: {exclusion_list}

  FILTERS:
    Keywords: {N} generated (top 3 probed)
    Industries: {industry_names} ({N} tag_ids)
    Geo: {locations}
    Size: {employee_range} employees
    Funding: {funding_stages} (prioritization)

  PROBE (6 credits):
    {keyword_1}: {total} companies
    {keyword_2}: {total} companies
    {keyword_3}: {total} companies
    {industry_1}: {total} companies
    {industry_2}: {total} companies
    → {unique} unique companies from probe

  COST:
    Estimated: ~{N} search + ~{M} enrichment = ~{T} credits (${usd})
    Max cap: {max_cost} credits (default 200)

  KPI: {target} contacts, {per_company}/company
  Email Accounts: {N} selected ({hint})
  Sequence: GOD_SEQUENCE (4 steps)
  Blacklist: {N} domains

  Proceed?
```

**User responses:**
- "proceed" / "yes" / "go" → set `offer_approved: true`, advance. **ALL subsequent phases are autonomous.**
- Feedback ("wrong roles", "add REGTECH", "too expensive") → re-extract offer and/or regenerate filters, re-present document. Loop until approved.

On approve: update state → `phase_states.cost_gate: "completed"`, status → "running"

**CRITICAL**: Never spend credits beyond the 6 probe requests without this checkpoint passing.

## Phase 4: Round Loop (AUTONOMOUS)

**Gate in**: strategy approved (Checkpoint 1 passed)
**Skills**: pipeline-state (round loop algorithm), company-qualification, quality-gate
**Human gate**: NONE — fully autonomous

1. Create run file: `save_data(project, "runs/run-{id}.json", initial_state)`
   - Set `campaign_id`, `campaign_slug`, `mode` from mode detection
   - **Mode 3**: Build dedup sets per Cross-Run Dedup Protocol (pipeline-state skill):
     - Load companies from all previous runs for this campaign → `seen_domains`
     - Export existing campaign leads → `seen_emails`
     - Record `dedup_baseline` in run file
2. Follow the round loop algorithm from pipeline-state skill EXACTLY:
   - GATHER: 1 keyword per request + 1 tag_id per request, all parallel, funded+unfunded
   - SCRAPE: 100 concurrent, starts streaming as companies arrive
   - CLASSIFY: 100 concurrent, via negativa, starts as scrapes complete
   - PEOPLE: 20 concurrent, starts as targets confirmed
   - KPI CHECK: enough contacts for `kpi.target_people`?
     (Mode 1/2: default 100. Mode 3: the DELTA, e.g. 98 if user said kpi=200 and campaign has 102.)
3. If target_rate < expected → **autonomously** regenerate keywords (quality-gate skill, 10 angles) → next round
4. If KPI not met after keywords exhausted → **autonomously** try next regeneration cycle (max 5)
5. After each round: update run file + state.yaml

On complete (KPI met or exhausted): update state → `phase_states.round_loop: "completed"`

**State saved continuously**: run file updated after each round. If pipeline crashes mid-round, resume from last completed round.

## Phase 5: People Extraction (AUTONOMOUS)

**Gate in**: round_loop completed
**Skill**: pipeline-state (people extraction section)
**Human gate**: NONE

1. For each target company: search people (FREE) → enrich (1 credit/person)
2. Retry logic: if <3 verified, try next candidates (max 3 rounds, 12 credits/company)
3. Priority: owner > founder > c_suite > vp > head > director
4. KPI check after EACH contact: stop immediately when target met
5. Update run file continuously

On KPI met: update state → `phase_states.people_extraction: "completed"`

## Phase 6: Sequence Generation (AUTONOMOUS)

**Gate in**: people extraction completed, KPI met
**Skill**: email-sequence
**Human gate**: NONE — GOD_SEQUENCE applied automatically

**Mode 1 and 2:**
1. If user provided sequence in document → use it (already extracted in Phase 1)
2. Otherwise → generate 4-5 step sequence following 12-rule GOD_SEQUENCE checklist
3. Apply segment-specific angle from offer
4. Save: `save_data(project, "sequences.json", sequence_data)`
5. DO NOT ask for approval — proceed to Phase 7

On complete: update state → `phase_states.sequence_generation: "completed"`

**Mode 3 (append):** SKIP. Sequence already set on the campaign.
- State already set to `sequence_generation: "skipped"`

## Phase 7: Campaign Push + Launch Approval — CHECKPOINT 2

**Gate in**: sequence ready (Mode 1/2) or people extraction completed (Mode 3)
**Skills**: SmartLead tools, Google Sheets tools
**Human gate**: **YES — this is the ONLY pre-launch approval**

### Step A: Build campaign (AUTONOMOUS — no user input)

**Mode 1 and 2 (create new campaign):**
1. `smartlead_create_campaign(project, name, sending_account_ids, country, segment)` → DRAFT
2. `smartlead_set_sequence(project, campaign_slug, campaign_id, steps)`
3. `smartlead_add_leads(campaign_id, leads)` — normalized names + segment/city custom fields
4. Update tracking:
   - campaign.yaml: `run_ids: [run_id], total_leads_pushed: N`
   - project.yaml: add campaign to `campaigns[]` index
   - run file: populate `campaign` field
5. `user_email` from `get_config()` → `user_email` field. If not set, ask user.
   `smartlead_send_test_email(campaign_id, user_email)` — user checks inbox
6. If Google Sheets configured:
   `sheets_export_contacts(project, campaign_slug)` — creates sheet with reasoning columns, shared with user

**Mode 3 (append to existing campaign):**
1. SKIP campaign creation — already exists
2. SKIP sequence — already set
3. `smartlead_add_leads(campaign_id, new_deduped_leads)`
4. Update tracking:
   - campaign.yaml: append `run_id` to `run_ids`, increment `total_leads_pushed`
   - run file: populate `campaign` field with push stats
5. `save_data(project, "contacts.json", new_deduped_leads, mode="append")`
6. If Google Sheets configured:
   `sheets_export_contacts(project, campaign_slug, existing_sheet_id)` — append to existing sheet

### Step B: Present for activation (CHECKPOINT 2)

**Mode 1 and 2:**
```
Campaign Ready (DRAFT):
  Name: {Segment} — {Geo}
  SmartLead: https://app.smartlead.ai/app/email-campaigns-v2/{id}/analytics

  Settings: plain text ✓, no tracking ✓, 40% followup ✓, {schedule}
  Accounts: {N} assigned ({account_list})
  Sequence: {N} steps set (Day {cadence}, A/B on Email 1)

  Contacts: {N} verified → uploaded to SmartLead
  Google Sheet: {sheet_url} (shared with {user_email}, includes target reasoning)

  Test email sent to {user_email} — check your inbox.

  Cost: {search} search + {enrichment} enrichment = {total} credits (${usd})
  Stats: {companies} companies → {targets} targets ({rate}%) → {contacts} contacts

  Type "activate" to start sending.
```

**Mode 3:**
```
Contacts Added to Campaign:
  Campaign: {name} (ID: {campaign_id})
  SmartLead: https://app.smartlead.ai/app/email-campaigns-v2/{id}/analytics

  NEW: {N} contacts pushed (deduped against {existing} existing)
  TOTAL: {total} contacts in campaign
  Dedup: {companies_skipped} companies skipped, {emails_skipped} emails skipped
  Google Sheet: {sheet_url} (updated with new contacts)

  Cost: {total} credits (${usd})

  Campaign is ACTIVE — new leads will enter the sending queue automatically.
  {or: Type "activate" to start sending.}
```

### Step C: Activation

- "activate" / "launch" → `smartlead_activate_campaign(campaign_id, "I confirm")`
- On activate: update state → `phase_states.campaign_push: "completed"`, `status: "completed"`

---

## Phase Skip Matrix

| Phase | Mode 1 (Fresh) | Mode 2 (New Campaign) | Mode 3 (Append) | Human Gate |
|-------|:-:|:-:|:-:|:-:|
| 1. Offer Extraction | AUTO | SKIP | SKIP | no |
| 2. Filter Generation | AUTO | AUTO (new segment) | AUTO (seeded) | no |
| 3. Cost Gate (Strategy Approval) | **CHECKPOINT 1** | **CHECKPOINT 1** | **CHECKPOINT 1** | **YES** |
| 4. Round Loop | AUTO | AUTO | AUTO + dedup | no |
| 5. People Extraction | AUTO | AUTO | AUTO + dedup | no |
| 6. Sequence Generation | AUTO | AUTO | SKIP | no |
| 7. Campaign Push | CREATE → **CHECKPOINT 2** | CREATE → **CHECKPOINT 2** | ADD → **CHECKPOINT 2** | **YES** |

## State Update Pattern

**Before each phase**:
```
current_phase = "{phase_name}"
phase_states.{phase_name} = "in_progress"
last_updated = "{now}"
→ save_data(project, "state.yaml", state, mode="write")
```

**After each phase**:
```
phase_states.{phase_name} = "completed"
last_updated = "{now}"
→ save_data(project, "state.yaml", state, mode="write")
```

**On error**:
```
phase_states.{phase_name} = "failed"
error = "{error_message}"
status = "failed"
→ save_data(project, "state.yaml", state, mode="write")
→ Report to user: what failed, what's saved, recovery options
```

## Resume Logic

On pipeline start, BEFORE Phase 1:

1. `load_data(project, "state.yaml")` → check for existing state
2. If state exists:
   - status == "completed" → "Pipeline already completed. Start new run?"
   - status == "failed" → show error, ask "Retry {failed_phase} or start fresh?"
   - status == "running"/"paused" → show progress, ask "Resume from {current_phase}?"
3. On resume: skip all "completed" and "skipped" phases, pick up from first pending/in_progress/failed
4. On start fresh: archive old state, initialize new

## Recovery from Phase Failures

| Phase | On Failure | Recovery |
|-------|-----------|----------|
| offer_extraction | Scrape failed or extraction incomplete | Retry with different URL or manual input |
| filter_generation | Generation failed | Retry or manual filter specification |
| cost_gate | User declined | Adjust filters to reduce cost, re-present Checkpoint 1 |
| round_loop | Apollo rate limited or credits exhausted | Wait and retry, or reduce scope |
| people_extraction | Enrichment failures | Retry failed companies, skip permanently failed |
| sequence_generation | Generation failed | Retry with different angle |
| campaign_push | SmartLead API error | Retry (handles "Plan expired!" bug) |
