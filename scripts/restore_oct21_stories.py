from pathlib import Path
from bs4 import BeautifulSoup
from html import escape
import csv

p = Path("data/processed/reports/2026-10-21/window_1d")
src = p / "Daily Financial Sector News Brief 2026-10-21 SEND.html"
review = p / "Daily Financial Sector News Brief 2026-10-21 Articles.csv"

assert src.is_file(), f"Missing: {src}"
assert review.is_file(), f"Missing: {review}"

with review.open(encoding="utf-8-sig", newline="") as f:
    records = list(csv.DictReader(f))

soup = BeautifulSoup(src.read_text(encoding="utf-8-sig"), "html.parser")

recover = [
    (
        "Banking Sector Developments",
        "Credit risk assessment method for financial institutions",
        "An article discusses credit-risk assessment for financial institutions seeking State guarantees. This is not evidence of a newly enacted regulation.",
        "Review guarantee exposure and credit-risk methodology."
    ),
    (
        "Banking Sector Developments",
        "Rs 3 Bn HLAB fund",
        "A Rs. 3 billion HLAB fund has reportedly been launched to help Sri Lankan businesses grow.",
        "Business and SME financing."
    ),
    (
        "Banking Sector Developments",
        "Sarvodaya Development Finance expands",
        "Sarvodaya Development Finance reports expanding its regional access to financial services.",
        "Financial inclusion and finance-company distribution."
    ),
    (
        "Monetary, Markets & External Sector",
        "Resus Energy Green Bond",
        "Resus Energy has reported a Luxembourg listing for its existing green bond, not a new issuance.",
        "Sustainable finance and capital markets."
    ),
    (
        "Monetary, Markets & External Sector",
        "Tea export earnings cross",
        "Tea export proceeds reportedly exceeded US$ 1 billion during January–September 2026.",
        "Foreign-exchange receipts and export performance."
    ),
    (
        "Macro & Systemic Context",
        "Access Engineering weighs fallout",
        "Access Engineering expects to recover approximately Rs. 1.45 billion of Rs. 1.76 billion invested in the cancelled port logistics venture, with an estimated Rs. 310 million loss.",
        "Financial consequences of the previously reported port project cancellation."
    ),
    (
        "Macro & Systemic Context",
        "Cabinet approves gazetting National Agency",
        "Cabinet has reportedly approved gazetting a Bill for a National Agency for PPPs. It has not thereby become law.",
        "Investment financing and public-sector contingent risk."
    ),
    (
        "Macro & Systemic Context",
        "Multilateral Development Banks strengthen",
        "Multilateral development bank heads discussed coordinated financing and mobilisation of private investment.",
        "International financing context; no Sri Lanka-specific commitment is implied."
    )
]

added = []

for section, pattern, summary, importance in recover:
    hits = [
        r for r in records
        if pattern.casefold() in r["headline"].casefold()
    ]
    assert len(hits) == 1, f"Missing/ambiguous article: {pattern}"
    r = hits[0]

    assert r["section"] == "EXCLUDE", f"Not excluded: {pattern}"

    if any(
        a.get("href", "").rstrip("/") == r["source_url"].rstrip("/")
        for a in soup.select("a[href]")
    ):
        continue

    target = next(
        (
            s for s in soup.select("section.report-section")
            if s.h2 and section.casefold()
            in s.h2.get_text(" ", strip=True).casefold()
        ),
        None
    )
    assert target, f"Section not found: {section}"

    html = f"""
    <article class="context-card" data-recovered="editorial">
        <div class="story-topline">
            {escape(r["source_name"])} •
            {escape(r["publication_date"])} •
            Editorial recovery
        </div>
        <h3>{escape(r["headline"])}</h3>
        <div class="summary-block">
            <div class="block-label">What happened</div>
            <p class="context-summary">{escape(summary)}</p>
        </div>
        <div class="context-bottom">
            <strong>Why it matters:</strong> {escape(importance)}
            <div class="source-list">
                <strong>Source:</strong>
                <a href="{escape(r["source_url"], quote=True)}"
                   target="_blank" rel="noopener noreferrer">
                    {escape(r["source_name"])} ↗
                </a>
            </div>
        </div>
    </article>
    """

    target.append(BeautifulSoup(html, "html.parser").article)
    added.append(r["headline"])

footer = soup.select_one(".report-footer")
if footer:
    note = BeautifulSoup(
        "<p><strong>Coverage note:</strong> Editorially recovered "
        "stories have been added from the article-review register. "
        "Ceylon Today and October 21 Daily Mirror coverage remain "
        "unverified. The original automated QA result does not "
        "certify this manually amended report.</p>",
        "html.parser"
    )
    footer.append(note.p)

titles = [
    a.h3.get_text(" ", strip=True)
    for a in soup.select("article")
    if a.h3
]

assert len(titles) == len(set(titles))
assert len(added) == 8, f"Only {len(added)} stories recovered"

dst = p / "Daily Financial Sector News Brief 2026-10-21 EXPANDED.html"
dst.write_text(str(soup), encoding="utf-8")

print("RECOVERY PASS")
print("Additional stories:", len(added))
print("Total stories:", len(titles))
print("Output:", dst.resolve())
