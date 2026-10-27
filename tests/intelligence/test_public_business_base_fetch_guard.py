from bank_intel.collectors.sources._public_business_base import (
    is_block_page,
)


def test_legitimate_page_with_cloudflare_asset_is_not_blocked():
    html = """
    <html>
      <head>
        <title>Daily Mirror - Business</title>
        <script src="https://static.cloudflareinsights.com/beacon.min.js"></script>
      </head>
      <body>
        <h1>Business</h1>
        <p>Normal public news page content.</p>
      </body>
    </html>
    """
    assert is_block_page(html, 200) is False


def test_real_challenge_title_is_blocked():
    html = """
    <html>
      <head><title>Just a moment...</title></head>
      <body>Checking your browser before accessing the site.</body>
    </html>
    """
    assert is_block_page(html, 200) is True


def test_block_status_is_blocked():
    assert is_block_page("<html></html>", 403) is True
