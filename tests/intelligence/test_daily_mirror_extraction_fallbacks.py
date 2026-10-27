from bs4 import BeautifulSoup

from bank_intel.collectors.sources._public_business_base import (
    extract_article_text,
    jsonld_candidates,
)


def test_jsonld_with_charset_is_detected():
    body = " ".join(["banking"] * 100)

    html = f"""
    <html>
      <head>
        <script type="application/ld+json; charset=UTF-8">
        {{
          "@context": "https://schema.org",
          "@type": "NewsArticle",
          "headline": "Test",
          "datePublished": "2026-10-06T10:00:00+05:30",
          "articleBody": "{body}"
        }}
        </script>
      </head>
    </html>
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    candidates = jsonld_candidates(
        soup
    )

    assert candidates
    assert (
        len(
            candidates[0]["articleBody"].split()
        )
        == 100
    )


def test_malformed_jsonld_article_body_is_recovered():
    body = " ".join(["finance"] * 100)

    # trailing comma makes this invalid strict JSON
    html = f"""
    <script type="application/ld+json">
    {{
      "@type": "NewsArticle",
      "headline": "Test",
      "articleBody": "{body}",
    }}
    </script>
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    candidates = jsonld_candidates(
        soup
    )

    assert candidates
    assert "finance finance" in (
        candidates[0]["articleBody"]
    )


def test_paragraph_cluster_fallback_extracts_prose():
    p1 = " ".join(
        ["prudential"] * 45
    )
    p2 = " ".join(
        ["supervision"] * 45
    )

    html = f"""
    <html>
      <body>
        <div class="mystery-wrapper">
          <p>{p1}</p>
          <p>{p2}</p>
        </div>
      </body>
    </html>
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    text = extract_article_text(
        soup,
        [],
    )

    assert len(text.split()) >= 90
    assert "prudential" in text
    assert "supervision" in text
