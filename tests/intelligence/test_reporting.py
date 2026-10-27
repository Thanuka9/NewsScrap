from bank_intel.intelligence.reporting import (
    DigestArticle,
    render_email_html,
    render_html,
    render_qa_html,
)


def test_email_renders_stakeholder_content_and_escapes_source_text():
    article = DigestArticle(
        publication_date="2026-10-07",
        source_name="News & Markets",
        headline='<script>alert("headline")</script>',
        source_url='https://example.com/news?a=1&b="two"',
        news_summary="Deposits < lending & funding.",
        primary_topic="LIQUIDITY_FUNDING",
        topic_labels=("LIQUIDITY_FUNDING",),
        topic_display_names=("Liquidity / Funding",),
        supervisory_lens="Monitor funding stability.",
        primary_entities=("Bank A",),
        primary_entity_types=("Licensed Commercial Bank",),
        all_entities=("Bank A", "Secondary Bank"),
        has_primary_regulated_entity=True,
        stage_b_label="RELEVANT",
        stage_b_confidence=0.9,
        hdbscan_cluster=-1,
        hdbscan_outlier=True,
        related_story_count=0,
        event_group_ids=(),
        phase2_review_priority="HIGH",
        stage1_stage_a_status="DISAGREE",
        supervisory_importance_score=45,
        supervisory_importance_components=(),
    )
    html = render_email_html(
        title="Daily brief",
        period_label="2026-10-07",
        priority_articles=[article],
        macro_market_articles=[],
        other_articles=[],
    )
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert 'href="https://example.com/news?a=1&amp;b=&quot;two&quot;"' in html
    assert "Deposits &lt; lending &amp; funding." in html
    assert "Bank A" in html
    assert "Licensed Commercial Bank" in html
    assert "Monitor funding stability." in html
    assert "Secondary Bank" not in html
    assert "DISAGREE" not in html
    assert "No developments in this section." in html
    brief = render_html(
        title="Daily Financial Brief", period_label="2026-10-07", generated_at="2026-10-13",
        metrics={"all_phase2_article_count": 32, "stage_a_keep_article_count": 14},
        priority_articles=[article], macro_market_articles=[], other_articles=[article],
        review_rows=[], event_groups=[], config={},
    )
    assert "<nav" not in brief
    assert "Who it affects" in brief
    assert "What happened" in brief
    assert "Why it matters" in brief
    assert "&lt;script&gt;" in brief
    assert "analysed</span>" not in brief
    assert "retained</span>" not in brief
    assert "BANK SUPERVISION" not in brief


def test_qa_renders_review_signals_and_total_flag_count():
    html = render_qa_html(
        title="Daily brief",
        period_label="2026-10-07",
        generated_at="2026-10-13",
        review_rows=[{
            "headline": "Bank <review>",
            "source_name": "News & Markets",
            "source_url": "https://example.com/news",
            "stage_a_label": "NOISE",
            "stage_b_label": "NOT_EXECUTED",
            "phase2_review_priority": "HIGH",
            "reasons": ["Stage-1 / Stage-A disagreement", "Risk <flag>"],
        }],
        metrics={"review_flag_count": 31},
        config={"version": "reporting-v0.3.0"},
    )
    assert "This page shows a selection" in html
    assert "<nav" not in html
    assert "Bank &lt;review&gt;" in html
    assert "NOISE" in html
    assert "NOT_EXECUTED" in html
    assert "The initial screening and relevance check disagree." in html
    assert "Risk &lt;flag&gt;" in html
    assert "Processing metrics" not in html
    assert 'href="model_learning_review.csv"' in html


def test_qa_handles_empty_review_window():
    html = render_qa_html(
        title="Daily brief",
        period_label="2026-10-07",
        generated_at="2026-10-13",
        review_rows=[],
        metrics={"review_flag_count": 0},
        config={},
    )
    assert "No stories were flagged for review in this period." in html
    assert "Nothing to review" in html
