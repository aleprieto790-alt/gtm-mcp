"""Pipeline execution — deterministic gather+scrape in one atomic tool call.

After Checkpoint 1, the agent has approved filters. This tool runs ALL
deterministic I/O in Python with asyncio streaming:

1. Apollo search: all keywords + industries in parallel (1 per request)
2. Dedup by domain as results arrive
3. Scrape: 100 concurrent Apify — starts AS SOON AS first domains arrive
4. Return: companies with scraped text, ready for LLM classification

The ONLY part the agent handles is classification (LLM) and people extraction.
This mirrors magnum-opus's streaming_pipeline.py but as a single MCP tool.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def pipeline_gather_and_scrape(
    keywords: list[str],
    industry_tag_ids: list[str],
    locations: list[str],
    employee_ranges: list[str],
    funding_stages: list[str] | None = None,
    max_companies: int = 400,
    scrape_concurrent: int = 100,
    max_pages_per_stream: int = 5,
    keyword_start_pages: dict[str, int] | None = None,
    *,
    project: str,
    run_id: str,
    config=None,
    workspace=None,
) -> dict:
    """Atomic gather + scrape pipeline. One tool call, full streaming inside.

    Fires all Apollo searches in parallel (1 keyword/industry per request).
    As domains arrive, immediately queues them for scraping (100 concurrent).
    Returns all companies with scraped text — ready for agent classification.

    Returns:
        companies: [{domain, name, apollo_data, scraped_text, scrape_status}]
        requests: [{type, filter_value, funded, page, raw_returned, new_unique, credits_used}]
        stats: {gather_seconds, scrape_seconds, total_seconds, credits_used, ...}
    """
    config = config or _default_config()
    api_key = config.get("apollo_api_key")
    apify_key = config.get("apify_proxy_password")
    if not api_key:
        return {"success": False, "error": "apollo_api_key not configured"}

    from gtm_mcp.tools.apollo import apollo_search_companies
    from gtm_mcp.tools.scraping import scrape_website

    started_at = datetime.now(timezone.utc)
    seen_domains: set[str] = set()

    # Load PROJECT-LEVEL blacklist (not global — different projects may target same contacts)
    if workspace and project:
        bl_data = workspace.load(project, "blacklist.json")
        if bl_data and isinstance(bl_data, dict):
            seen_domains.update(bl_data.keys())
            logger.info("Loaded %d blacklisted domains for project %s", len(bl_data), project)
        elif bl_data and isinstance(bl_data, list):
            seen_domains.update(bl_data)
            logger.info("Loaded %d blacklisted domains for project %s", len(bl_data), project)
    companies: dict[str, dict] = {}
    requests: list[dict] = []
    scrape_queue: asyncio.Queue = asyncio.Queue()
    scrape_results: dict[str, dict] = {}
    gather_done = asyncio.Event()
    req_counter = 0

    # --- Phase 1: Apollo gather (all parallel, feeds scrape queue) ---

    _start_pages = keyword_start_pages or {}

    async def search_one(filter_type: str, filter_value: str, funded: bool = False):
        nonlocal req_counter
        start = _start_pages.get(filter_value, 1)
        for page in range(start, start + max_pages_per_stream):
            if len(seen_domains) >= max_companies:
                break

            filters: dict[str, Any] = {
                "organization_locations": locations,
                "organization_num_employees_ranges": employee_ranges,
            }
            if filter_type == "keyword":
                filters["q_organization_keyword_tags"] = [filter_value]
            else:
                filters["organization_industry_tag_ids"] = [filter_value]
            if funded and funding_stages:
                filters["organization_latest_funding_stage_cd"] = funding_stages

            result = await apollo_search_companies(api_key, filters, page=page, per_page=100)
            if not result.get("success"):
                break

            raw_companies = result.get("companies", [])
            if not raw_companies:
                break  # exhausted

            new_unique = 0
            for c in raw_companies:
                domain = c.get("primary_domain", "") or c.get("domain", "")
                if not domain or domain in seen_domains:
                    continue
                if len(seen_domains) >= max_companies:
                    break
                seen_domains.add(domain)
                new_unique += 1
                companies[domain] = {
                    "domain": domain,
                    "name": c.get("name", ""),
                    "apollo_id": c.get("apollo_id", "") or c.get("id", ""),
                    "apollo_data": {
                        "industry": c.get("industry", ""),
                        "industry_tag_id": c.get("industry_tag_id", ""),
                        "employee_count": c.get("employee_count"),
                        "employee_range": c.get("employee_range", ""),
                        "country": c.get("country", ""),
                        "city": c.get("city", ""),
                        "state": c.get("state", ""),
                        "founded_year": c.get("founded_year"),
                        "linkedin_url": c.get("linkedin_url", ""),
                        "short_description": c.get("short_description", ""),
                        "keywords": c.get("keywords", []),
                        "funding_stage": c.get("funding_stage", ""),
                        "funding_amount": c.get("funding_amount"),
                        "revenue": c.get("revenue", ""),
                        "phone": c.get("phone", ""),
                    },
                    "discovery": {
                        "found_by": f"{filter_type}:{filter_value}",
                        "funded": funded,
                        "page": page,
                    },
                }
                # Feed to scrape queue immediately
                await scrape_queue.put(domain)

            req_counter += 1
            requests.append({
                "id": f"req-{req_counter:03d}",
                "type": filter_type,
                "filter_value": filter_value,
                "funded": funded,
                "page": page,
                "result": {
                    "raw_returned": len(raw_companies),
                    "new_unique": new_unique,
                    "duplicates": len(raw_companies) - new_unique,
                    "credits_used": 1,
                },
            })

            # Low yield: <10 on page 1 → stop
            if page == 1 and len(raw_companies) < 10:
                break
            # Exhausted: page returned companies but 0 new unique → stop
            if new_unique == 0:
                break

    gather_started = datetime.now(timezone.utc)

    gather_tasks = []
    for kw in keywords:
        gather_tasks.append(search_one("keyword", kw, funded=False))
        if funding_stages:
            gather_tasks.append(search_one("keyword", kw, funded=True))
    for tag_id in industry_tag_ids:
        gather_tasks.append(search_one("industry", tag_id, funded=False))
        if funding_stages:
            gather_tasks.append(search_one("industry", tag_id, funded=True))

    _gather_completed_at: list = []  # mutable container for closure

    async def run_gather():
        await asyncio.gather(*gather_tasks, return_exceptions=True)
        _gather_completed_at.append(datetime.now(timezone.utc))  # FIX #3: timestamp before scrape finishes
        # Send sentinel values to stop workers
        for _ in range(_WORKER_COUNT):
            await scrape_queue.put(None)

    # --- Phase 2: Scrape (workers with sentinel shutdown, semaphore for concurrency) ---

    scrape_sem = asyncio.Semaphore(scrape_concurrent)
    scrape_started = datetime.now(timezone.utc)
    _WORKER_COUNT = min(scrape_concurrent, 20)  # FIX #1: 20 workers, not 100. Semaphore limits actual concurrency.

    async def scrape_worker():
        while True:
            domain = await scrape_queue.get()  # FIX #1: no timeout polling. Sentinel (None) terminates.
            if domain is None:
                scrape_queue.task_done()
                break

            async with scrape_sem:
                url = f"https://{domain}"
                result = await scrape_website(url, apify_proxy_password=apify_key)
                scrape_results[domain] = {
                    "status": "success" if result.get("success") else "failed",
                    "text_length": len(result.get("text", "")),
                    "text": (result.get("text", ""))[:3000],  # FIX #4: 3K per company (400 × 3K = 1.2MB, fits MCP)
                }
                scrape_queue.task_done()

    # Run both phases concurrently — scraping starts as domains arrive from Apollo
    scrape_workers = [scrape_worker() for _ in range(_WORKER_COUNT)]

    await asyncio.gather(
        run_gather(),
        *scrape_workers,
        return_exceptions=True,
    )

    gather_completed = _gather_completed_at[0] if _gather_completed_at else datetime.now(timezone.utc)
    scrape_completed = datetime.now(timezone.utc)

    # --- Save scraped text to workspace file (not in MCP response — too large) ---
    # FIX #4: Response returns companies WITHOUT full text. Text saved to file.

    for domain, comp in companies.items():
        sr = scrape_results.get(domain, {"status": "not_scraped", "text_length": 0, "text": ""})
        comp["scrape"] = {
            "status": sr["status"],
            "text_length": sr["text_length"],
        }
        comp["_scraped_text"] = sr.get("text", "")  # kept in-memory for file save, stripped from response

    completed_at = datetime.now(timezone.utc)
    total_credits = sum(r["result"]["credits_used"] for r in requests)

    # Build response: companies WITHOUT full scraped text (too large for MCP response)
    # Agent uses save_data to persist, then reads text per-company for classification
    response_companies = {}
    for domain, comp in companies.items():
        rc = dict(comp)
        rc.pop("_scraped_text", None)  # strip from response
        response_companies[domain] = rc

    # Also build a separate dict for the agent to pass to classification agents
    # Key: domain, Value: first 2500 chars of scraped text
    scraped_texts = {
        d: comp.get("_scraped_text", "")[:2500]
        for d, comp in companies.items()
        if comp.get("scrape", {}).get("status") == "success"
    }

    # Auto-save companies + requests to run file (if project + run_id provided)
    # This ensures scrape metadata + discovery provenance persist even if agent
    # fails to save. Classification agents later MERGE into these company records.
    if project and run_id and workspace:
        run_path = f"runs/{run_id}.json"
        existing_run = workspace.load(project, run_path) or {}
        existing_run["companies"] = response_companies
        existing_run["requests"] = requests
        existing_run["totals"] = {
            **existing_run.get("totals", {}),
            "total_api_requests": len(requests),
            "total_credits_search": total_credits,
            "unique_companies": len(companies),
            "companies_scraped": sum(1 for c in companies.values() if c.get("scrape", {}).get("status") == "success"),
        }
        existing_run["rounds"] = existing_run.get("rounds", [])
        if not existing_run["rounds"]:
            existing_run["rounds"].append({})
        existing_run["rounds"][0] = {
            **existing_run["rounds"][0],
            "id": "round-001",
            "status": "completed",
            "timestamps": {
                "gather_started": gather_started.isoformat(),
                "gather_completed": gather_completed.isoformat(),
                "scrape_started": scrape_started.isoformat(),
                "scrape_completed": scrape_completed.isoformat(),
            },
            "gather_phase": {"total_requests": len(requests), "unique_companies": len(companies), "credits_used": total_credits},
            "scrape_phase": {
                "total": len(companies),
                "success": sum(1 for c in companies.values() if c.get("scrape", {}).get("status") == "success"),
                "failed": sum(1 for c in companies.values() if c.get("scrape", {}).get("status") != "success"),
                "concurrent": scrape_concurrent,
            },
        }
        workspace.save(project, run_path, existing_run)
        logger.info("Auto-saved %d companies + %d requests to %s", len(companies), len(requests), run_path)

    return {
        "success": True,
        "data": {
            "companies": response_companies,
            "scraped_texts": scraped_texts,
            "requests": requests,
            "stats": {
                "total_companies": len(companies),
                "scraped_success": sum(1 for d, c in companies.items() if c.get("scrape", {}).get("status") == "success"),
                "scraped_failed": sum(1 for d, c in companies.items() if c.get("scrape", {}).get("status") == "failed"),
                "total_requests": len(requests),
                "total_credits": total_credits,
                "gather_started": gather_started.isoformat(),
                "gather_completed": gather_completed.isoformat(),
                "scrape_started": scrape_started.isoformat(),
                "scrape_completed": scrape_completed.isoformat(),
                "gather_seconds": (gather_completed - gather_started).total_seconds(),
                "scrape_seconds": (scrape_completed - scrape_started).total_seconds(),
                "total_seconds": (completed_at - started_at).total_seconds(),
            },
        },
    }


async def pipeline_compute_leaderboard(
    project: str,
    run_id: str,
    *,
    workspace=None,
) -> dict:
    """Compute keyword + industry leaderboard from run's request + company data.

    For each keyword/industry: count unique companies, targets, target rate,
    credits, quality_score. Saves to run file. Zero LLM.
    """
    import math
    workspace = workspace or _default_workspace()

    run_path = f"runs/{run_id}.json"
    run_data = workspace.load(project, run_path)
    if not run_data:
        return {"success": False, "error": f"Run file {run_path} not found"}

    requests = run_data.get("requests", [])
    companies = run_data.get("companies", {})

    if not requests:
        return {"success": False, "error": "No requests tracked in run file"}

    # Build per-keyword stats
    keyword_stats: dict[str, dict] = {}
    for req in requests:
        key = f"{req['type']}:{req.get('filter_value', '')}"
        if key not in keyword_stats:
            keyword_stats[key] = {
                "type": req["type"],
                "filter_value": req.get("filter_value", ""),
                "unique_companies": 0,
                "targets": 0,
                "credits_used": 0,
                "last_page": 0,
                "exhausted": False,
            }
        s = keyword_stats[key]
        s["credits_used"] += req.get("result", {}).get("credits_used", 1)
        s["unique_companies"] += req.get("result", {}).get("new_unique", 0)
        page = req.get("page", 1)
        if page > s["last_page"]:
            s["last_page"] = page
        # Mark exhausted if page returned <100 results (no more pages to fetch)
        raw = req.get("result", {}).get("raw_returned", 0)
        if raw < 100:
            s["exhausted"] = True

    # Count targets per keyword using company.discovery.found_by
    for domain, comp in companies.items():
        found_by = comp.get("discovery", {}).get("found_by", "")
        if found_by and found_by in keyword_stats:
            is_target = comp.get("classification", {}).get("is_target", False)
            if is_target:
                keyword_stats[found_by]["targets"] += 1

    # Compute quality scores
    leaderboard = []
    for key, s in keyword_stats.items():
        uc = s["unique_companies"]
        targets = s["targets"]
        credits = max(s["credits_used"], 1)
        target_rate = targets / uc if uc > 0 else 0
        quality_score = target_rate * math.log(uc + 1) / credits if uc > 0 else 0

        leaderboard.append({
            **s,
            "target_rate": round(target_rate, 3),
            "quality_score": round(quality_score, 4),
            "next_page": s["last_page"] + 1 if not s["exhausted"] else None,
        })

    leaderboard.sort(key=lambda x: -x["quality_score"])

    # Save to run file
    run_data["keyword_leaderboard"] = leaderboard
    workspace.save(project, run_path, run_data)

    return {
        "success": True,
        "data": {
            "entries": len(leaderboard),
            "top_5": leaderboard[:5],
        },
    }


async def pipeline_import_blacklist(
    project: str,
    campaign_id: int,
    *,
    config=None,
    workspace=None,
) -> dict:
    """Export leads from SmartLead campaign + save as project-level blacklist. One call.

    Deterministic. Zero LLM. Guarantees blacklist exists before pipeline_gather_and_scrape.
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()

    from gtm_mcp.tools.smartlead import smartlead_export_leads
    result = await smartlead_export_leads(campaign_id, config=config)
    if not result.get("success"):
        return {"success": False, "error": f"Export failed: {result.get('error')}"}

    leads = result["data"].get("leads", [])
    domains = result["data"].get("domains", [])

    # Save as project-level blacklist
    now = datetime.now(timezone.utc).isoformat()
    bl = {d: {"source": "smartlead_campaign", "campaign_id": campaign_id, "blacklisted_at": now}
          for d in domains}
    workspace.save(project, "blacklist.json", bl)

    return {
        "success": True,
        "data": {
            "campaign_id": campaign_id,
            "leads_exported": len(leads),
            "domains_blacklisted": len(domains),
        },
    }


async def pipeline_save_intelligence(
    project: str,
    run_id: str,
    *,
    workspace=None,
) -> dict:
    """Save cross-run intelligence from run's keyword leaderboard. Zero LLM.

    Updates ~/.gtm-mcp/filter_intelligence.json with keyword quality scores
    and segment playbooks. Future runs start with proven keywords.
    """
    workspace = workspace or _default_workspace()

    run_path = f"runs/{run_id}.json"
    run_data = workspace.load(project, run_path)
    if not run_data:
        return {"success": False, "error": f"Run file not found"}

    leaderboard = run_data.get("keyword_leaderboard", [])
    if not leaderboard:
        return {"success": False, "error": "No keyword_leaderboard in run file. Call pipeline_compute_leaderboard first."}

    # Load or create global intelligence file
    import json
    intel_path = workspace.base / "filter_intelligence.json"
    if intel_path.exists():
        intel = json.loads(intel_path.read_text())
    else:
        intel = {"keyword_knowledge": {}, "segment_playbooks": {}}

    # Update keyword knowledge
    kk = intel.get("keyword_knowledge", {})
    for entry in leaderboard:
        key = entry.get("filter_value", "")
        if not key:
            continue
        if key in kk:
            old = kk[key]
            old["times_used"] = old.get("times_used", 0) + 1
            old["avg_target_rate"] = (old.get("avg_target_rate", 0) + entry.get("target_rate", 0)) / 2
            if entry.get("quality_score", 0) > old.get("best_quality_score", 0):
                old["best_quality_score"] = entry["quality_score"]
        else:
            kk[key] = {
                "type": entry.get("type", "keyword"),
                "times_used": 1,
                "avg_target_rate": entry.get("target_rate", 0),
                "best_quality_score": entry.get("quality_score", 0),
                "unique_companies": entry.get("unique_companies", 0),
            }
    intel["keyword_knowledge"] = kk

    # Update segment playbook
    project_data = workspace.load(project, "project.yaml")
    if project_data:
        segments = project_data.get("offer", project_data).get("segments", [])
        if segments:
            seg_name = segments[0].get("name", "UNKNOWN") if isinstance(segments[0], dict) else str(segments[0])
            top_keywords = [e["filter_value"] for e in leaderboard[:10] if e.get("quality_score", 0) > 0]
            intel.setdefault("segment_playbooks", {})[seg_name] = {
                "best_keywords": top_keywords,
                "avg_target_rate": sum(e.get("target_rate", 0) for e in leaderboard) / max(len(leaderboard), 1),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

    intel_path.write_text(json.dumps(intel, indent=2, ensure_ascii=False))

    return {
        "success": True,
        "data": {
            "keywords_updated": len(kk),
            "segment_playbooks": list(intel.get("segment_playbooks", {}).keys()),
        },
    }


async def pipeline_save_contacts(
    project: str,
    run_id: str,
    contacts: list[dict],
    search_credits: int = 0,
    people_credits: int = 0,
    *,
    workspace=None,
) -> dict:
    """Deterministic save: contacts to BOTH contacts.json AND run file.

    Also updates run totals (credits, kpi_met). One tool call, no LLM needed.
    Fixes the persistent bug where contacts were in contacts.json but not run file.
    """
    import json
    workspace = workspace or _default_workspace()

    # 1. Save contacts.json
    workspace.save(project, "contacts.json", contacts)

    # 2. Load run file, update contacts + totals, write back
    run_path = f"runs/{run_id}.json"
    run_data = workspace.load(project, run_path)
    if not run_data:
        return {"success": False, "error": f"Run file {run_path} not found"}

    run_data["contacts"] = contacts
    kpi_target = run_data.get("kpi", {}).get("target_people", 100)

    # Mark companies that had people extracted (for Phase 0 reuse in future runs)
    enriched_domains = {c.get("company_domain") for c in contacts if c.get("company_domain")}
    companies = run_data.get("companies", {})
    for domain in enriched_domains:
        if domain in companies:
            companies[domain]["people_extracted"] = True

    run_data["totals"] = {
        **run_data.get("totals", {}),
        "contacts_extracted": len(contacts),
        "kpi_met": len(contacts) >= kpi_target,
        "total_credits_people": people_credits,
        "total_credits": search_credits + people_credits,
    }

    workspace.save(project, run_path, run_data)

    return {
        "success": True,
        "data": {
            "contacts_saved": len(contacts),
            "kpi_met": len(contacts) >= kpi_target,
            "kpi_target": kpi_target,
            "total_credits": search_credits + people_credits,
        },
    }


async def pipeline_people_to_push(
    project: str,
    run_id: str,
    campaign_name: str,
    sending_account_ids: list[int],
    country: str,
    segment: str,
    sequence_steps: list[dict],
    test_email: str = "",
    max_people_per_company: int = 3,
    person_seniorities: list[str] | None = None,
    create_sheet: bool = True,
    mode: str = "create",
    existing_campaign_id: int | None = None,
    *,
    config=None,
    workspace=None,
) -> dict:
    """Atomic post-classification → SmartLead ready. ONE call, ZERO LLM.

    After classification is done (targets in run file), this tool does EVERYTHING:
    1. Load target domains from run file
    2. apollo_search_people_batch — search all targets (FREE)
    3. apollo_enrich_people — bulk enrich (1 credit per verified email)
    4. Save contacts to contacts.json + run file + update totals
    5. Export to Google Sheet (optional)
    6. Push to SmartLead — create campaign OR append to existing
    7. Update campaign.yaml + run file with campaign data

    mode: "create" → new campaign via campaign_push
          "append" → add leads to existing_campaign_id via smartlead_add_leads

    Replaces 6+ separate tool calls. Eliminates all agent decisions post-classification.
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()

    # 1. Load targets from run file
    run_path = f"runs/{run_id}.json"
    run_data = workspace.load(project, run_path)
    if not run_data:
        return {"success": False, "error": f"Run file {run_path} not found"}

    companies = run_data.get("companies", {})
    target_domains = [d for d, c in companies.items()
                      if c.get("classification", {}).get("is_target")]

    if not target_domains:
        return {"success": False, "error": "No target companies found in run file"}

    logger.info("pipeline_people_to_push: %d targets from %d companies", len(target_domains), len(companies))

    # 2. Search people (FREE — no credits)
    from gtm_mcp.tools.apollo import apollo_search_people_batch, apollo_enrich_people

    api_key = config.get("apollo_api_key")
    if not api_key:
        return {"success": False, "error": "apollo_api_key not configured"}

    seniorities = person_seniorities or ["c_suite", "vp", "head", "director", "manager"]
    search_result = await apollo_search_people_batch(
        api_key, target_domains, person_seniorities=seniorities, per_page=10,
    )
    if not search_result.get("success"):
        return {"success": False, "error": f"People search failed: {search_result.get('error')}", "step": "search"}

    # 3. Collect top N person IDs per company, then enrich
    all_person_ids = []
    for entry in search_result.get("results", []):
        people = entry.get("people", [])
        top_n = [p.get("id") for p in people[:max_people_per_company] if p.get("id")]
        all_person_ids.extend(top_n)

    if not all_person_ids:
        return {"success": False, "error": "No people found across target companies", "step": "search",
                "targets_searched": len(target_domains)}

    enrich_result = await apollo_enrich_people(api_key, all_person_ids)
    if not enrich_result.get("success"):
        return {"success": False, "error": f"Enrichment failed: {enrich_result.get('error')}", "step": "enrich"}

    # Build contacts list
    contacts = []
    for person in enrich_result.get("people", []):
        if not person.get("email"):
            continue
        contacts.append({
            "email": person["email"],
            "first_name": person.get("first_name", ""),
            "last_name": person.get("last_name", ""),
            "name": f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
            "title": person.get("title", ""),
            "seniority": person.get("seniority", ""),
            "linkedin_url": person.get("linkedin_url", ""),
            "phone": person.get("phone", ""),
            "company_domain": person.get("company_domain", ""),
            "company_name_normalized": person.get("company_name", ""),
            "segment": companies.get(person.get("company_domain", ""), {}).get(
                "classification", {}).get("segment", segment),
        })

    people_credits = len(contacts)
    search_credits = run_data.get("totals", {}).get("total_credits_search", 0)
    logger.info("pipeline_people_to_push: %d contacts enriched (%d credits)", len(contacts), people_credits)

    # 4. Save contacts (atomic — both files + totals + kpi_met + people_extracted)
    await pipeline_save_contacts(project, run_id, contacts, search_credits, people_credits,
                                 workspace=workspace)

    # 5. Google Sheet export (optional)
    sheet_url = ""
    if create_sheet and contacts:
        from gtm_mcp.tools.sheets import sheets_export_contacts
        sheet_result = await sheets_export_contacts(project, config=config, workspace=workspace)
        if sheet_result.get("success"):
            sheet_url = sheet_result["data"].get("sheet_url", "")

    # 6. Save leads file for push
    import json
    leads = []
    for c in contacts:
        leads.append({
            "email": c["email"],
            "first_name": c.get("first_name", ""),
            "last_name": c.get("last_name", ""),
            "company_name": c.get("company_name_normalized", ""),
            "linkedin_url": c.get("linkedin_url", ""),
            "phone": c.get("phone", ""),
        })
    workspace.save(project, "leads_for_push.json", leads)

    # 7. Campaign push
    campaign_id = None
    campaign_slug = None
    leads_uploaded = 0

    if mode == "create":
        from gtm_mcp.tools.campaign_push import campaign_push
        push_result = await campaign_push(
            project, campaign_name, sending_account_ids, country, segment,
            sequence_steps, "leads_for_push.json", test_email, run_id=run_id,
            config=config, workspace=workspace,
        )
        if push_result.get("success"):
            campaign_id = push_result["data"]["campaign_id"]
            campaign_slug = push_result["data"]["campaign_slug"]
            leads_uploaded = push_result["data"]["leads_uploaded"]
    elif mode == "append" and existing_campaign_id:
        from gtm_mcp.tools.smartlead import smartlead_add_leads
        add_result = await smartlead_add_leads(existing_campaign_id, leads, config=config)
        if add_result.get("success"):
            campaign_id = existing_campaign_id
            leads_uploaded = len(leads)
            # Update campaign.yaml
            import re
            slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.lower()).strip("-")
            campaign_slug = slug
            existing_camp = workspace.load(project, f"campaigns/{slug}/campaign.yaml")
            if existing_camp:
                existing_camp["total_leads_pushed"] = existing_camp.get("total_leads_pushed", 0) + len(leads)
                existing_run_ids = existing_camp.get("run_ids", [])
                if run_id not in existing_run_ids:
                    existing_run_ids.append(run_id)
                existing_camp["run_ids"] = existing_run_ids
                workspace.save(project, f"campaigns/{slug}/campaign.yaml", existing_camp)

        # Update run file with campaign link
        run_data = workspace.load(project, run_path) or {}
        run_data["campaign_id"] = campaign_id
        run_data["campaign_slug"] = campaign_slug
        from datetime import datetime, timezone as tz
        run_data["campaign"] = {
            "campaign_id": campaign_id,
            "leads_pushed": leads_uploaded,
            "pushed_at": datetime.now(tz.utc).isoformat(),
        }
        workspace.save(project, run_path, run_data)

    return {
        "success": True,
        "data": {
            "targets": len(target_domains),
            "contacts": len(contacts),
            "people_credits": people_credits,
            "total_credits": search_credits + people_credits,
            "kpi_met": len(contacts) >= run_data.get("kpi", {}).get("target_people", 100),
            "campaign_id": campaign_id,
            "campaign_slug": campaign_slug,
            "leads_uploaded": leads_uploaded,
            "sheet_url": sheet_url,
            "mode": mode,
        },
    }


def _default_config():
    from gtm_mcp.config import ConfigManager
    return ConfigManager()
