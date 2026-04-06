# GTM-MCP — B2B Lead Generation for AI Agents

An open-source MCP server that turns your AI agent into a full B2B lead generation pipeline. Tell it what you sell and who you're targeting — it finds companies, verifies contacts, and creates ready-to-send email campaigns.

Zero LLM calls inside the server. Your AI agent (Claude, GPT, etc.) does all the reasoning using domain knowledge encoded as skills.

## How It Works

```
You: /launch outreach-plan-fintech.md

Agent: extracts offer → generates Apollo filters → probes (6 credits)

Agent: "Here's your strategy document. 25 keywords, 3 industries,
        estimated ~130 credits for 100 contacts. Proceed?"

You: proceed

Agent: [autonomous — gathers 400 companies, scrapes websites,
        classifies via negativa, extracts 102 verified contacts,
        creates SmartLead campaign, sends test email, exports Google Sheet]

Agent: "Campaign ready (DRAFT). 102 contacts uploaded.
        Test email sent — check your inbox. Type 'activate' to launch."

You: activate
```

Two human checkpoints. Everything else autonomous.

## Prerequisites

**Data sources:**
- [Apollo](https://apollo.io) — company and contact search (required)
- More coming soon

**Scraping:**
- [Apify](https://apify.com) — residential proxy for website scraping (optional, improves success rate)

**Outreach platforms:**
- [SmartLead](https://smartlead.ai) — email campaigns (required for campaign creation)
- [GetSales](https://getsales.io) — LinkedIn automation (optional)
- More coming soon: Instantly, Heyreach

**Optional:**
- Google Sheets — contact CRM export with classification reasoning
- Telegram — notifications via MCP plugin

## Quick Start

```bash
git clone https://github.com/petrNikolaev1/gtm-mcp.git
cd gtm-mcp
cp .env.example .env    # fill in your API keys
pip install -e .         # or: uv sync
```

### Configure `.env`

```bash
# Required
GTM_MCP_APOLLO_API_KEY=your_apollo_key
GTM_MCP_SMARTLEAD_API_KEY=your_smartlead_key

# Your email — receives test emails before campaign activation
GTM_MCP_USER_EMAIL=you@company.com

# Optional
GTM_MCP_GETSALES_API_KEY=
GTM_MCP_GETSALES_TEAM_ID=
GTM_MCP_APIFY_PROXY_PASSWORD=

# Optional — Google Sheets export
GOOGLE_SERVICE_ACCOUNT_JSON=
GOOGLE_SHARED_DRIVE_ID=
```

### Connect to Claude Code

The server runs via stdio. Add to your Claude Code config or use the included `.mcp.json`:

```bash
# Open Claude Code in this directory
claude
# Then type:
/launch https://yourcompany.com SaaS companies in US
```

## Use Cases

### New project from a strategy document

```
/launch outreach-plan-fintech.md
```

Reads your document, extracts offer/ICP/segments/sequences, generates Apollo filters, gathers companies, classifies, extracts contacts, creates SmartLead campaign. Full pipeline.

### New project from a website

```
/launch https://acme.com payments in Miami
```

Scrapes the website, understands the product, generates keywords, runs the pipeline.

### New project from a description

```
/launch "We sell payroll software for SMBs, target US and UK"
```

Works with plain text too.

### Add more contacts to an existing campaign

```
/launch campaign=3070919 kpi=+100
```

Reuses the same project, same email accounts, same sequence. Gathers 100 MORE contacts using smarter keywords (seeded from previous run's best performers). Deduplicates against existing campaign leads. Pushes only new contacts.

Previous knowledge is fully reused:
- Keywords that found the most targets are tried first
- Companies already gathered are skipped (no wasted credits)
- Page offsets are preserved (no re-fetching)
- Email dedup against existing campaign leads

### New segment within an existing project

```
/launch project=easystaff segment=LENDING geo=UK accounts with Elnar
```

Reuses the approved offer (skip extraction). Generates new filters for LENDING in UK. Selects different email accounts. Creates a separate SmartLead campaign. The agent only needs:
- Segment name (must differ from existing campaigns)
- Geography
- Email account selection

### New project for a different product

```
/launch https://newproduct.com payments in Germany
```

Full pipeline from scratch. If `filter_intelligence.json` has data from similar segments in past projects, proven keywords are used as seeds.

## Architecture

```
Your AI Agent ──stdio──► gtm-mcp server (37 tools, 0 LLM calls)
                              │
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
                Apollo    SmartLead   GetSales
                (search)  (campaigns) (LinkedIn)
```

**Tools** (`src/gtm_mcp/`): Thin API wrappers. Zero LLM calls. Only data access.

**Skills** (`.claude/skills/`): Domain knowledge in markdown. Your AI agent reads these and reasons.

**Commands** (`.claude/commands/`): The `/launch` command — a flat 7-step orchestrator with concrete tool calls.

### Approach

Inspired by [claude-pipe](https://github.com/bluzir/claude-pipe) — the command IS the orchestrator. No framework, no daemon, no state machine. One flat command file with 7 steps. The AI agent reads it and executes. Skills provide domain knowledge at each step.

### Pipeline Steps

| Step | What happens | Human input |
|------|-------------|:-----------:|
| 1. Extract Offer | Scrape URL / read file / parse text → structured ICP | - |
| 2. Generate Filters | Apollo taxonomy + keywords + probe (6 credits) | - |
| 3. Strategy Approval | Show offer + filters + cost estimate | **Proceed?** |
| 4. Gather + Classify | Apollo search → scrape → via negativa classify | - |
| 5. Extract People | FREE search → PAID enrichment (1 credit/person) | - |
| 6. Generate Sequence | 12-rule GOD_SEQUENCE or from document | - |
| 7. Campaign Push | SmartLead DRAFT + test email + Google Sheet | **Activate?** |

### Key Rules

- **1 keyword per Apollo request** — 7x more unique companies vs combined
- **Via negativa classification** — exclude non-targets, don't define targets (97% accuracy)
- **Max 200 Apollo credits** per run (default, overridable)
- **100 verified contacts** KPI (default, overridable)
- **Plain text emails**, no tracking, 40% follow-up, Mon-Fri 9-18 target timezone

## Tools (37)

| Category | Count | Examples |
|----------|:-----:|---------|
| Config | 2 | `get_config`, `set_config` |
| Projects | 6 | `create_project`, `save_data`, `load_data`, `find_campaign`, `get_project_costs` |
| Blacklist | 3 | `blacklist_check`, `blacklist_add`, `blacklist_import` |
| Apollo | 6 | `apollo_search_companies`, `apollo_search_people`, `apollo_enrich_people`, `apollo_get_taxonomy` |
| Scraping | 1 | `scrape_website` |
| SmartLead | 12 | `smartlead_create_campaign`, `smartlead_add_leads`, `smartlead_activate_campaign`, `smartlead_send_test_email` |
| GetSales | 4 | `getsales_create_flow`, `getsales_add_leads`, `getsales_activate_flow` |
| Google Sheets | 3 | `sheets_create`, `sheets_export_contacts`, `sheets_read` |

## Data Storage

All project data in `~/.gtm-mcp/projects/<slug>/`:

```
~/.gtm-mcp/
├── config.yaml                  # API keys
├── blacklist.json               # global domain blacklist
├── filter_intelligence.json     # cross-run keyword quality scores
└── projects/
    └── easystaff-outreach/
        ├── project.yaml         # offer, segments, ICP
        ├── state.yaml           # pipeline phase progress
        ├── contacts.json        # extracted contacts
        ├── runs/
        │   └── run-001.json     # complete execution record
        └── campaigns/
            └── payments-us/
                ├── campaign.yaml
                ├── sequence.yaml
                └── replies.json
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
