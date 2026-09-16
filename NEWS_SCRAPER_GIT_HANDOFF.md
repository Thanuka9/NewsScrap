# Sri Lanka News Scraper — Git Handoff Report

**Status date:** 2026-09-16  
**Diagnostic checkpoint:** `source_diagnostic_20260915_084456.json`  
**Purpose:** Preserve the exact project state before continuing code-level debugging and production hardening.

---

## 1. Project Goal

Build a reliable Sri Lankan financial/business news scraper that:

- discovers genuine article URLs from configured news sections;
- extracts headline, publication date/time, author when available, and article body;
- avoids navigation/category/author/archive pages;
- handles normal HTTP fetches and browser fallback only when required;
- validates article quality before accepting a result;
- records explicit errors/warnings for failed extraction;
- can later run safely as a repeatable production/news-ingestion pipeline.

Current priority sources:

1. Daily News
2. Daily Mirror
3. Ceylon Today
4. The Island
5. Daily FT

---

## 2. Last Confirmed Diagnostic State

### SRC003 — Ceylon Today

**Status: WORKING / reference implementation**

- Section fetch: successful
- Fetch method: `httpx`
- Candidate links found: 12
- Articles tested: 3
- Successful articles: 3/3
- Browser fallback: not required
- Valid extracted article lengths observed: approximately 394–549 words
- Publication timestamps extracted successfully
- No extraction errors in tested articles

**Action:** Do not rewrite this adapter unless a shared/core change requires regression testing.

---

### SRC001 — Daily News

**Status: BLOCKED AT ARTICLE FETCH**

Section discovery is working:

- Business section loads successfully
- Playwright/browser path is being used
- Candidate article links are being found
- Diagnostic found 17 candidate links

Actual article pages fail:

- HTTP/browser result returns `403`
- extracted headline degrades to domain text such as `dailynews.lk`
- publication date is missing
- article text is approximately 28 words
- validation reports `ARTICLE_TOO_SHORT:28`
- warning includes `PUBLISHED_DATE_MISSING`

Example correctly discovered article shape:

```text
https://dailynews.lk/2026/09/15/business/<article-id>/<article-slug>
```

**Primary task:** fix article-page access/fetch strategy without breaking section discovery.

**Do not treat this as a discovery bug.**

---

### SRC002 — Daily Mirror

**Status: DISCOVERY FILTER BUG + ARTICLE FETCH BLOCK**

Section page:

- Business section loads
- Playwright is being used
- up to 50 candidate links are collected

Problem 1 — candidate filtering is too broad.

The diagnostic incorrectly selected navigation/category-like URLs such as:

```text
/news/155   -> Top Stories
/news/342   -> Breaking News
/news/110   -> Pictorial News
```

These are not the intended business article pages.

Problem 2 — selected pages then return `403` and produce the same short anti-bot/body response pattern:

- word count ~28
- `ARTICLE_TOO_SHORT:28`
- `PUBLISHED_DATE_MISSING`

**Fix order:**

1. tighten genuine-article URL detection;
2. reject navigation/category/archive/index pages;
3. then fix browser/article access for true article pages;
4. re-run article metadata/body validation.

---

### SRC004 — The Island

**Status: SECTION/DISCOVERY FAILURE**

Observed diagnostic state:

- section URL reached browser path
- status observed: `307`
- candidate article links found: 0
- articles tested: 0
- diagnostic note: `NO_ARTICLE_LINKS_DISCOVERED`
- browser fallback required/used

**Primary task:** determine the final redirected section URL / page structure and fix link discovery before working on article extraction.

**Fix order:**

1. capture final redirect URL;
2. inspect rendered DOM;
3. identify genuine Business article link pattern;
4. add source-specific discovery rule;
5. test article extraction only after links are valid.

---

### SRC005 — Daily FT

**Status: ARTICLE DISCOVERY FILTER BUG**

The site is accessible with normal HTTP:

- section fetch: successful
- fetch method: `httpx`
- status: `200`
- candidate links found: 50
- browser fallback not required

However, the discovery logic is selecting non-article content, including pages such as:

```text
/columns/4                  -> Columnists
/ajantha-dharmasiri/32      -> author page
/ajith-de-alwis/33          -> author page
```

Those pages return HTTP 200 but produce no article body, causing:

```text
ARTICLE_TEXT_MISSING
```

**Primary task:** correct Daily FT article URL classification/filtering.

**Important:** This is not currently an anti-bot/access problem.

---

## 3. Current Source Summary

| Source | Discovery | Fetch | Extraction | Primary problem |
|---|---|---|---|---|
| Ceylon Today | PASS | PASS | PASS | None; use as baseline |
| Daily News | PASS | FAIL | FAIL | Article pages return 403 |
| Daily Mirror | FAIL/PARTIAL | FAIL | FAIL | Wrong URLs + 403 |
| The Island | FAIL | N/A | N/A | Redirect/discovery |
| Daily FT | FAIL/PARTIAL | PASS | FAIL | Wrong URLs selected |

---

## 4. Required Engineering Direction

Do **not** restart or redesign the scraper from zero.

The current architecture already proves that the general extraction/validation flow works because Ceylon Today passes end-to-end.

Continue with **source-specific adapters/rules** while keeping shared logic common where possible.

Recommended separation:

```text
core/
    fetch.py
    browser.py
    extraction.py
    validation.py
    models.py

sources/
    daily_news.py
    daily_mirror.py
    ceylon_today.py
    island.py
    daily_ft.py
```

Each source adapter should define, as needed:

- section URL(s)
- accepted article URL pattern
- rejected URL patterns
- discovery selector(s)
- preferred fetch method
- browser fallback rules
- headline selector / metadata fallback
- date selector / metadata fallback
- article-body selector(s)
- source-specific cleanup

Shared validation should remain centralized.

---

## 5. URL Classification Requirements

Before fetching a candidate as an article, the scraper should classify it.

Minimum rejection classes:

- home page
- section/category page
- author page
- columnist index
- archive page
- tag page
- pagination link
- photo gallery/index
- navigation item
- social/share URL
- login/account URL

For each source, prefer an **allow-list article URL pattern** rather than only a generic deny-list.

A candidate should not be promoted to article-fetch stage merely because:

- it is same-domain;
- it contains `/news/`;
- anchor text is non-empty;
- HTTP status is 200.

---

## 6. Fetch Strategy Requirements

Use the least expensive reliable method:

```text
httpx -> browser fallback only when necessary
```

Do not automatically use Playwright for every source.

Expected current behavior:

- Ceylon Today: `httpx`
- Daily FT: `httpx`
- Daily News: likely source-specific browser/session handling required
- Daily Mirror: likely browser/session handling after discovery is fixed
- The Island: browser/redirect inspection currently required

For browser sources, log:

- requested URL
- final URL
- HTTP/network status where available
- page title
- body character count
- whether challenge/block page was detected
- cookies/session state if relevant (never log sensitive values)

---

## 7. Anti-Bot / Block Page Detection

Do not validate a block/challenge page as an article.

Add explicit block-page detection using signals such as:

- HTTP 401/403/429
- extremely short repeated body across unrelated URLs
- page title reduced to domain name
- challenge/access-denied text
- missing article DOM despite expected article URL

Recommended explicit diagnostic result:

```text
FETCH_BLOCKED
```

rather than allowing the response to fail later only as:

```text
ARTICLE_TOO_SHORT
```

Both can be retained, but the root cause should be visible.

---

## 8. Validation Rules to Preserve

Continue validating at least:

- headline present
- body present
- minimum article length
- publication date when the source exposes it
- URL is classified as article
- article body is not duplicated site chrome/navigation

Existing useful diagnostic labels include:

```text
ARTICLE_TOO_SHORT
ARTICLE_TEXT_MISSING
PUBLISHED_DATE_MISSING
AUTHOR_NOT_AVAILABLE
NO_ARTICLE_LINKS_DISCOVERED
BROWSER_FALLBACK_REQUIRED_OR_USED
```

Add root-cause errors where useful rather than replacing these blindly.

---

## 9. Recommended Fix Order

### Phase 1 — Daily FT

Reason: easiest deterministic fix; site is already accessible.

1. inspect actual article links on Financial Services section;
2. derive allow-list URL pattern;
3. reject `/columns/` and author/profile pages;
4. test at least 10 genuine articles;
5. confirm body/date/headline extraction.

### Phase 2 — Daily Mirror discovery

1. fix URL classification;
2. verify candidates are true article pages;
3. only then debug fetch/access behavior.

### Phase 3 — Daily News access

1. keep known-good discovery;
2. inspect network/browser response;
3. identify why browser fetch still receives 403;
4. implement source-specific session/browser handling;
5. verify 5–10 articles.

### Phase 4 — The Island

1. resolve 307 redirect behavior;
2. inspect final rendered section page;
3. implement discovery;
4. test article fetch/extraction.

### Phase 5 — Regression run

Re-run all sources including Ceylon Today.

---

## 10. Acceptance Criteria Before Production Run

For every enabled source:

### Discovery

- at least 95% of sampled discovered URLs are real article URLs;
- no category/author/navigation URLs in accepted article set.

### Fetch

- successful access to normal article pages;
- block pages explicitly identified;
- browser fallback only where required.

### Extraction

For a manual sample of at least 10 articles per source:

- headline correct;
- publication timestamp correct when published by source;
- body is article content, not chrome/navigation;
- body length realistic;
- no obvious truncation;
- URL preserved;
- author extracted where available.

### Reliability

- one failed article does not terminate the whole source run;
- one failed source does not terminate the whole scraper run;
- diagnostic output identifies source, URL, stage, fetch method, and root error.

---

## 11. Regression Tests That Should Be Added

At minimum, create tests for:

```text
Daily FT:
- author page rejected
- /columns/ page rejected
- genuine article accepted

Daily Mirror:
- Top Stories category rejected
- Breaking News category rejected
- Pictorial News category rejected
- genuine business article accepted

Daily News:
- known article URL recognized as article
- 403/block response classified as FETCH_BLOCKED

The Island:
- section redirect resolves correctly
- article discovery returns >0 valid candidates

Ceylon Today:
- known-good article still extracts successfully
```

Also add a generic test ensuring the same 20–30 word block-page body cannot be accepted as valid article content.

---

## 12. Diagnostic Output Expected After Fixes

The next diagnostic report should include, per source:

```json
{
  "source": "...",
  "section_fetch": {
    "method": "httpx|playwright",
    "status": 200,
    "final_url": "..."
  },
  "candidate_links_total": 0,
  "accepted_article_links": 0,
  "rejected_links": {
    "category": 0,
    "author": 0,
    "navigation": 0,
    "other": 0
  },
  "articles_tested": 0,
  "articles_valid": 0,
  "fetch_blocked": 0,
  "extraction_failed": 0,
  "missing_dates": 0
}
```

Keep individual article diagnostics as well.

---

## 13. Files / Code Needed for the Next Review

When handing the repository/code over for debugging, include the following if present:

```text
README.md
pyproject.toml or requirements.txt
main scraper entrypoint
source configuration
all source adapters/parsers
HTTP fetch utility
Playwright/browser utility
article discovery code
article extraction code
validation code
diagnostic runner
existing tests
latest diagnostic JSON
sample output files/logs
```

Especially important files are whichever currently implement:

```text
discover_links(...)
fetch_page(...)
fetch_with_browser(...)
extract_article(...)
extract_published_date(...)
validate_article(...)
```

---

## 14. Git Information to Include With the Code

Before the next debugging pass, provide:

```bash
git status
git branch --show-current
git log -10 --oneline
```

If the project is on GitHub, provide the repository/branch or upload a ZIP of the repository.

Do not remove failing diagnostics before review. They are useful evidence.

---

## 15. Immediate Next Objective

**First code change should be the Daily FT article URL filter.**

Why:

- network access already works;
- section retrieval already works;
- candidate discovery already works mechanically;
- failure is isolated to URL classification;
- it provides a clean way to harden the shared article-link classifier before applying similar improvements to Daily Mirror.

After Daily FT passes, move to Daily Mirror discovery, then Daily News 403 handling, then The Island redirect/discovery.

---

## 16. Do Not Do

Do not:

- rewrite the whole scraper without evidence;
- replace Ceylon Today logic while it is working;
- blindly force Playwright for every source;
- accept all same-domain links as articles;
- treat HTTP 200 as proof that a page is an article;
- hide 403/block responses under only `ARTICLE_TOO_SHORT`;
- weaken validation simply to increase success counts;
- run a full production ingestion before source diagnostics pass.

---

## 17. Handoff Summary

```text
CURRENT CHECKPOINT

Ceylon Today  : PASS — preserve and regression-test
Daily FT      : FIX ARTICLE URL FILTER FIRST
Daily Mirror  : FIX ARTICLE URL FILTER, THEN 403
Daily News    : DISCOVERY OK, FIX ARTICLE 403
The Island    : FIX REDIRECT + DISCOVERY

NEXT STEP
Receive repository/code -> reproduce current diagnostic ->
fix Daily FT -> run tests -> fix Daily Mirror -> Daily News -> The Island ->
full regression diagnostic -> production readiness review.
```
