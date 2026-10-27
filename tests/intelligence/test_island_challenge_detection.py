from bank_intel.collectors.sources._public_business_base import (
    is_block_page,
)


def test_island_javascript_challenge_is_blocked():
    html = """
    <html>
      <head>
        <title>You are being redirected...</title>
      </head>
      <body>
        Javascript is required. Please enable javascript before
        you are allowed to see this page.
      </body>
    </html>
    """

    assert is_block_page(
        html,
        200,
    ) is True


def test_normal_island_business_page_is_not_blocked():
    html = """
    <html>
      <head>
        <title>Business - The Island</title>
      </head>
      <body>
        <h1>Business</h1>
        <a href="/sample-business-story/">
          Sample business story
        </a>
      </body>
    </html>
    """

    assert is_block_page(
        html,
        200,
    ) is False
