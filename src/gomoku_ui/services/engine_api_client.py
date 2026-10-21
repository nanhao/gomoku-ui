"""HTTP client for the gomoku-engine REST API.

This is the client's only REST boundary. It performs exactly the two calls the
window needs -- load a game and make a move -- with bounded timeouts, and it
translates every failure mode (non-2xx response, timeout, connection refusal,
malformed JSON) into :class:`EngineApiError`. The controller therefore never
sees ``httpx`` types, and the toolkit-agnostic layers stay free of transport
details.

Requests are only issued from the controller's single-worker executor, so one
``httpx.Client`` can be shared safely and reused across calls.
"""

from collections.abc import Mapping
from typing import Any, Final

import httpx

from gomoku_ui.models.game_snapshot import GameSnapshot
from gomoku_ui.models.make_move_request import MakeMoveRequest
from gomoku_ui.services.engine_api_error import EngineApiError


class EngineApiClient:
    """REST client for the engine's game endpoints.

    Attributes:
        base_url: Engine base URL without a trailing slash, e.g.
            ``http://127.0.0.1:8000``.
    """

    # Path of the game resource; ``/{game_id}`` and ``/moves`` are appended.
    GAMES_PATH: Final[str] = "/api/v1/games"

    # Upper bound for a whole request, and for establishing the connection.
    REQUEST_TIMEOUT_SECONDS: Final[float] = 10.0
    CONNECT_TIMEOUT_SECONDS: Final[float] = 5.0

    def __init__(self, base_url: str) -> None:
        """Create a client bound to one engine instance.

        Args:
            base_url: Base URL of the engine, e.g. ``http://127.0.0.1:8000``.
        """
        self._base_url: str = base_url.rstrip("/")
        self._client: httpx.Client = httpx.Client(
            timeout=httpx.Timeout(
                self.REQUEST_TIMEOUT_SECONDS,
                connect=self.CONNECT_TIMEOUT_SECONDS,
            )
        )

    @property
    def base_url(self) -> str:
        """Return the engine base URL this client talks to."""
        return self._base_url

    def get_game(self, game_id: str) -> GameSnapshot:
        """Load the full current state of one game.

        Args:
            game_id: uuid of the game to load.

        Returns:
            The engine's authoritative snapshot of the game.

        Raises:
            EngineApiError: If the request fails or the payload is invalid.
        """
        return self._request_snapshot("GET", self._game_path(game_id))

    def make_move(self, game_id: str, row: int, col: int) -> GameSnapshot:
        """Submit one move and return the engine's updated snapshot.

        Args:
            game_id: uuid of the game to continue.
            row: 1-based row of the stone to place.
            col: 1-based column of the stone to place.

        Returns:
            The snapshot produced by the engine after applying the move.

        Raises:
            EngineApiError: If the engine rejects the move (``400``), does not
                know the game (``404``), or the request itself fails.
        """
        request = MakeMoveRequest(row=row, col=col)
        return self._request_snapshot(
            "POST",
            f"{self._game_path(game_id)}/moves",
            payload=request.to_dict(),
        )

    def close(self) -> None:
        """Release the underlying HTTP connection pool.

        Safe to call more than once; the controller calls it during shutdown.
        """
        self._client.close()

    def _game_path(self, game_id: str) -> str:
        """Build the absolute path of one game resource.

        Args:
            game_id: uuid of the game.

        Returns:
            The path below the base URL, e.g. ``/api/v1/games/<uuid>``.
        """
        return f"{self.GAMES_PATH}/{game_id}"

    def _request_snapshot(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> GameSnapshot:
        """Perform one request and decode its snapshot.

        Args:
            method: HTTP method, ``GET`` or ``POST``.
            path: Absolute path appended to :attr:`base_url`.
            payload: JSON body for ``POST`` requests; ``None`` for ``GET``.

        Returns:
            The decoded snapshot carried by the response.

        Raises:
            EngineApiError: If the request fails in any way, including an
                unparseable or shape-invalid response body.
        """
        url: str = f"{self._base_url}{path}"
        try:
            response = self._client.request(method, url, json=payload)
        except httpx.TimeoutException as exc:
            # Covers both connect and read timeouts.
            raise EngineApiError(
                f"The engine at {self._base_url} did not answer in time."
            ) from exc
        except httpx.TransportError as exc:
            # Connection refused, DNS failure, TLS errors, and so on.
            raise EngineApiError(
                f"Could not reach the engine at {self._base_url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise EngineApiError(
                self._describe_error(response),
                status_code=response.status_code,
            )

        try:
            data: Any = response.json()
        except ValueError as exc:
            raise EngineApiError(
                "The engine returned a response that is not valid JSON."
            ) from exc
        if not isinstance(data, Mapping):
            raise EngineApiError("The engine returned an unexpected game payload.")
        try:
            return GameSnapshot.from_dict(data)
        except ValueError as exc:
            raise EngineApiError(
                f"The engine returned an unexpected game payload: {exc}"
            ) from exc

    def _describe_error(self, response: httpx.Response) -> str:
        """Extract the engine's ``detail`` message from an error response.

        Args:
            response: The non-2xx response returned by the engine.

        Returns:
            A user-facing message that names the status and, when available,
            the engine's human-readable reason.
        """
        detail: str | None = None
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        if isinstance(body, Mapping):
            candidate = body.get("detail")
            if isinstance(candidate, str) and candidate:
                detail = candidate
        reason: str = detail or response.reason_phrase or "request failed"
        return f"The engine rejected the request (HTTP {response.status_code}): {reason}"
