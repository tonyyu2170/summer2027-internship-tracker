# Program / Research / Competition Discovery Prompt

The one-time LLM discovery pass that seeds `sources/programs.yaml` (Phase E,
Task E2 of `docs/superpowers/plans/2026-07-28-tracker-trust-fixup.md`).

**This is not part of the scrape path.** Discovery runs once per cycle (or when
Tony explicitly asks to re-seed), in a subagent with web search — never wired
into `run_scrape_merge.py` or any recurring job. After seeding, every scrape
only re-checks the watch-list deterministically via `scripts/check_programs.py`.

Run the prompt three times, once per `kind` (`program`, `research`,
`competition`), each in its own web-search-capable subagent. Review every
result by hand before it lands: any entry whose `url` does not resolve to a
real page owned by the named org is **dropped, not "fixed."** Append survivors
to `sources/programs.yaml` and commit the watch-list separately from code.

---

## The prompt

````markdown
You are finding **structured early-career opportunities** for a US-based CS/quant
undergraduate targeting the **Summer 2027** cycle (currently a sophomore/junior).

Find opportunities of EXACTLY ONE kind — the caller specifies which:

- **program** — structured early-career pipeline programs and insight/diversity events.
  Seed examples: NVIDIA Ignite, Microsoft Explore, Optiver Future Focus, Google STEP,
  Meta University, Jane Street INSIGHT & FOCUS, Goldman Sachs Possibilities Summits,
  SEO Career, Citadel Discover, Bank of America Freshman/Sophomore programs, spring
  weeks, insight days, diversity conferences with recruiting tracks.
- **research** — fellowships, AI residencies, REUs, and research programs open to
  undergraduates. Seed examples: NSF REU sites, AI residency programs, lab-hosted
  summer research fellowships.
- **competition** — competitions, datathons, and hackathons that function as
  recruiting entry points. Seed examples: Jane Street ETC, Citadel Datathon,
  IMC Prosperity, Optiver trading competitions, major sponsored hackathons.

**EXCLUDE** ordinary internship postings — those are already tracked elsewhere. If
the thing is just "Software Engineer Intern, Summer 2027," it does not belong here.
The distinguishing feature is a *named program* with its own identity, page, and
application window.

**Rules:**
1. US-based or US-eligible only (remote/virtual counts if US students are eligible).
2. The `url` MUST be the program's own canonical page on the organization's domain
   — never an aggregator, listicle, Medium post, or job board.
3. If you cannot find a real, currently-resolving page for an opportunity, DO NOT
   include it. An omission is fine; a fabricated entry is not.
4. Prefer opportunities whose application window is plausibly still ahead for the
   Summer 2027 cycle. Include ones whose window has passed only if they recur
   annually — set `status: closed` and, if stated, next year's `opens`.
5. Do not invent dates. If a page says "applications open in the fall," record
   `opens: '2026-09'` only if a month is actually stated; otherwise `opens: null`
   and `status: unknown`.

**For each opportunity, ALSO extract an open/closed detection signal** so a later
deterministic script can re-check the page without an LLM:
- `check_url` — the page to fetch (usually the same as `url`)
- `open_signal` — a literal string or simple regex present ONLY when applications
  are open (e.g. `"Apply now"`, `"Applications are open"`)
- `closed_signal` — a string present ONLY when they are closed
  (e.g. `"Applications are closed"`, `"check back"`)
If the page has no reliable textual signal, set both to `null` — the entry is then
tracked with `status: unknown` and flagged for manual review. **Do not guess a
signal you have not actually seen on the page.**

**Output** — a YAML list only, no prose, matching this shape exactly:

```yaml
- name: NVIDIA Ignite
  org: NVIDIA
  kind: program
  category: ai_ml          # one of: swe, quant, data_science, ai_ml, hardware,
                           # actuarial — or null if it spans several (finance /
                           # consulting-flavored programs use null)
  url: https://…
  apply_url: https://…     # or null
  status: open             # open | upcoming | closed | unknown
  opens: '2026-09'         # 'YYYY-MM' | 'YYYY-MM-DD' | null
  closes: null
  eligibility: Sophomores and juniors, US-based
  location: Santa Clara, CA   # or null
  cycle: Summer 2027          # or null if year-round
  check_url: https://…
  open_signal: "Apply now"    # or null
  closed_signal: "Applications are closed"   # or null
  notes: null
```

Aim for breadth over certainty on *which* to include, but never on whether the
opportunity and its URL are real. State at the end how many you found, and list
any you deliberately excluded because you could not verify a real page.
````
