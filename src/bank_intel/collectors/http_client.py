from __future__ import annotations

from dataclasses import dataclass
import time

import httpx


# ============================================================
# RESULT
# ============================================================


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    html: str
    content_type: str | None
    fetch_method: str = "httpx"


# ============================================================
# HTTP CLIENT
# ============================================================


class HttpClient:
    """
    Standard HTTP acquisition client for approved/public
    newspaper endpoints.

    Features:
        - redirects enabled
        - normal browser-like headers
        - TLS verification
        - transient network retry
        - transient HTTP-status retry
        - no retry/bypass for access-control responses
          such as 401/403
    """

    RETRYABLE_STATUS_CODES = {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
    }

    RETRYABLE_EXCEPTIONS = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.RemoteProtocolError,
        httpx.NetworkError,
    )

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        retry_base_delay_seconds: float = 1.5,
    ):
        self.timeout_seconds = (
            timeout_seconds
        )

        self.max_attempts = max(
            1,
            max_attempts,
        )

        self.retry_base_delay_seconds = (
            retry_base_delay_seconds
        )

        self.client: (
            httpx.Client | None
        ) = None

    # ========================================================
    # CONTEXT MANAGER
    # ========================================================

    def __enter__(
        self,
    ) -> "HttpClient":

        self.client = httpx.Client(
            timeout=httpx.Timeout(
                self.timeout_seconds
            ),
            follow_redirects=True,
            verify=True,
            trust_env=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/152.0.0.0 "
                    "Safari/537.36"
                ),

                "Accept": (
                    "text/html,"
                    "application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "image/avif,"
                    "image/webp,"
                    "*/*;q=0.8"
                ),

                "Accept-Language": (
                    "en-US,en;q=0.9"
                ),

                "Cache-Control": (
                    "no-cache"
                ),

                "Pragma": (
                    "no-cache"
                ),
            },
        )

        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:

        if self.client is not None:

            self.client.close()

            self.client = None

    # ========================================================
    # RETRY WAIT
    # ========================================================

    def _retry_delay(
        self,
        attempt_number: int,
    ) -> float:
        """
        Exponential backoff:

            retry 1 -> 1.5 sec
            retry 2 -> 3.0 sec
            retry 3 -> 6.0 sec
        """

        return (
            self.retry_base_delay_seconds
            * (
                2
                ** (
                    attempt_number - 1
                )
            )
        )

    # ========================================================
    # GET
    # ========================================================

    def get(
        self,
        url: str,
    ) -> FetchResult:

        if self.client is None:

            raise RuntimeError(
                "HttpClient must be used inside "
                "a 'with HttpClient() as client:' block."
            )

        last_exception = None

        for attempt in range(
            1,
            self.max_attempts + 1,
        ):

            try:

                response = (
                    self.client.get(
                        url
                    )
                )

                # ============================================
                # TRANSIENT HTTP STATUS
                # ============================================

                if (
                    response.status_code
                    in self.RETRYABLE_STATUS_CODES
                ):

                    if (
                        attempt
                        < self.max_attempts
                    ):

                        delay = (
                            self._retry_delay(
                                attempt
                            )
                        )

                        print(
                            "[HTTP RETRY] "
                            f"{response.status_code} "
                            f"for {url}"
                        )

                        print(
                            "[HTTP RETRY] "
                            f"Attempt "
                            f"{attempt}/"
                            f"{self.max_attempts}. "
                            f"Retrying in "
                            f"{delay:.1f}s..."
                        )

                        time.sleep(
                            delay
                        )

                        continue

                # ============================================
                # PERMANENT / FINAL HTTP ERROR
                # ============================================

                response.raise_for_status()

                # ============================================
                # SUCCESS
                # ============================================

                return FetchResult(
                    requested_url=url,
                    final_url=str(
                        response.url
                    ),
                    status_code=(
                        response.status_code
                    ),
                    html=(
                        response.text
                    ),
                    content_type=(
                        response.headers.get(
                            "content-type"
                        )
                    ),
                    fetch_method="httpx",
                )

            # ================================================
            # TRANSIENT NETWORK / TLS FAILURE
            # ================================================

            except (
                self.RETRYABLE_EXCEPTIONS
            ) as exc:

                last_exception = exc

                if (
                    attempt
                    >= self.max_attempts
                ):

                    break

                delay = (
                    self._retry_delay(
                        attempt
                    )
                )

                print(
                    "[HTTP RETRY] "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                print(
                    "[HTTP RETRY] "
                    f"Attempt "
                    f"{attempt}/"
                    f"{self.max_attempts}. "
                    f"Retrying in "
                    f"{delay:.1f}s..."
                )

                time.sleep(
                    delay
                )

            # ================================================
            # NON-RETRYABLE HTTP ERROR
            #
            # Examples:
            #     401
            #     403
            #     404
            #
            # Do not attempt to bypass access controls.
            # ================================================

            except httpx.HTTPStatusError:

                raise

        # ====================================================
        # ALL RETRIES FAILED
        # ====================================================

        if last_exception is not None:

            raise last_exception

        raise RuntimeError(
            f"HTTP request failed after "
            f"{self.max_attempts} attempts: "
            f"{url}"
        )