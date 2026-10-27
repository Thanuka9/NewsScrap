"""Correct October 21 brief without rerunning collection or ML."""
from pathlib import Path
from bs4 import BeautifulSoup
from html import escape
import re
import sys

src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "data/processed/reports/2026-10-21/window_1d/"
    "Daily Financial Sector News Brief 2026-10-21.html"
)

assert src.is_file(), f"Source HTML not found: {src}"

dst = src.with_name(
    "Daily Financial Sector News Brief 2026-10-21 SEND.html"
)

soup = BeautifulSoup(
    src.read_text(encoding="utf-8-sig"), "html.parser"
)

def find_card(part):
    matches = [
        article for article in soup.select("article")
        if article.h3 and part.casefold()
        in article.h3.get_text(" ", strip=True).casefold()
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one story matching {part!r}; found {len(matches)}"
        )
    return matches[0]

def remove_story(part):
    article = find_card(part)
    print("[REMOVED]", article.h3.get_text(" ", strip=True))
    article.decompose()

# Remove stories already sent yesterday.
remove_story("Global Finance World’s Best Digital Bank")
remove_story("LOLC Microfinance Bank Pakistan named as Best Turnaround Bank")

# Remove unrelated health article.
remove_story("Feminisation of ageing")

# Consolidate two reports of the same CSE trading session.
secondary = find_card(
    "CSE tests 20,000 as selective buying support emerges"
)
primary = find_card("CSE ends down 0.38%")

other_link = secondary.select_one("a[href]")
bottom = primary.select_one(".context-bottom")
summary = primary.select_one(".context-summary")

assert other_link and bottom and summary
assert "dailymirror.lk" in other_link["href"]

source_html = (
    '<div class="source-list">'
    '<strong>Additional report:</strong> '
    '<a href="' + escape(other_link["href"], quote=True) + '" '
    'target="_blank" rel="noopener noreferrer">'
    'Daily Mirror ↗</a></div>'
)
bottom.append(
    BeautifulSoup(source_html, "html.parser").div
)

original = summary.get_text(" ", strip=True)
corrected = re.sub(
    r"Turnover was below Rs\.\s*750 million "
    r"on over 25\.8 million shares traded\.",
    "Over 25.8 million shares traded.",
    original,
    flags=re.I
)
if corrected == original:
    raise RuntimeError("Expected CSE turnover statement not found")

summary.string = corrected
secondary.decompose()
print("[PASS] CSE duplicate consolidated")

# Promote offshore lending and Stage 2 credit-risk news.
offshore = find_card("Banks’ offshore lending jumps 52%")
article_summary = offshore.select_one(".context-summary")
article_link = offshore.select_one("a[href]")

assert article_summary and article_link

headline = offshore.h3.get_text(" ", strip=True)
body = article_summary.get_text(" ", strip=True)
url = article_link["href"]

section = next(
    (
        s for s in soup.select("section.report-section")
        if s.h2 and "CBSL & Bank Supervisory"
        in s.h2.get_text(" ", strip=True)
    ),
    None
)
assert section, "Supervisory section not found"

fragment = f"""
<article class="priority-card">
  <div class="priority-number">01</div>
  <div class="priority-body">
    <div class="story-topline">
      Daily Mirror <span class="dot">•</span> 2026-10-20
    </div>
    <h3>{escape(headline)}</h3>
    <div class="meta-row supervisory-focus-row">
      <div class="meta-main">
        <div class="meta-label">Supervisory focus</div>
        <div class="entity-name">
          Foreign-currency credit risk, Stage 2 exposures
        </div>
      </div>
    </div>
    <div class="summary-block">
      <div class="block-label">What happened</div>
      <p>{escape(body)}</p>
    </div>
    <div class="relevance-block">
      <div class="block-label">Why it matters</div>
      <p>
        Directly relevant to bank asset quality: overseas lending
        rose 51.9% year on year and accounted for 37.1% of
        foreign-currency loans, while Stage 2 loans increased
        10.4% quarter on quarter in Q2. Supervisory attention
        should focus on overseas and sector concentrations,
        borrower stress, credit staging and impairment adequacy.
        Stage 2 does not itself mean a loan has defaulted.
      </p>
    </div>
    <div class="story-footer">
      <div class="context-wrap"></div>
      <div class="source-link">
        <a href="{escape(url, quote=True)}"
           target="_blank" rel="noopener noreferrer">
          Daily Mirror ↗
        </a>
      </div>
    </div>
  </div>
</article>
"""

new_article = BeautifulSoup(fragment, "html.parser").article
section.select_one(".section-title-row").insert_after(new_article)
offshore.decompose()

for number, article in enumerate(
    section.select("article.priority-card"), 1
):
    label = article.select_one(".priority-number")
    if label:
        label.string = f"{number:02d}"

print("[PASS] Offshore credit-risk story moved to supervisory highlights")

# Improve Sampath Bank supervisory explanation.
debenture = find_card("Sampath Bank debenture issue oversubscribed")
why = debenture.select_one(".relevance-block p")
if why:
    why.string = (
        "Potential capital and funding implications for Sampath Bank. "
        "Monitor the final amount issued, subordination terms and "
        "eligibility for regulatory capital recognition under "
        "the applicable rules."
    )

# Final validation before saving.
titles = [
    article.h3.get_text(" ", strip=True)
    for article in soup.select("article")
    if article.h3
]

assert len(titles) == 12, f"Unexpected article count: {len(titles)}"
assert len(titles) == len(set(titles)), "Duplicate headlines found"
assert len(soup.select("article.priority-card")) == 2
assert any("Stage 2" in title for title in titles)
assert "Feminisation of ageing" not in " ".join(titles)

dst.write_text(str(soup), encoding="utf-8")

print("=" * 65)
print("FINAL CORRECTION: PASS")
print("Total stories:", len(titles))
print("Supervisory highlights:", 2)
print("Output:", dst.resolve())
print("Original HTML preserved.")
print("COVERAGE NOTE: Ceylon Today coverage remains unverified.")
