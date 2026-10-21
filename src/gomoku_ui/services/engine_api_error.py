"""Single exception type for every engine REST failure.

``EngineApiClient`` translates timeouts, transport errors, HTTP error
responses and malformed payloads into :class:`EngineApiError`, so the
controller has one failure type to handle and one place to read the HTTP
status from. Keeping this class in its own module follows the project's
one-class-per-module convention.
"""


class EngineApiError(Exception):
    """A REST call to gomoku-engine failed.

    Attributes:
        status_code: The HTTP status returned by the engine, or ``None`` when
            the request never produced a response (timeout, connection
            refused, malformed body).
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Create an error with a user-facing message.

        Args:
            message: Human-readable description shown in the window.
            status_code: HTTP status code when the engine answered.
        """
        super().__init__(message)
        self._status_code: int | None = status_code

    @property
    def status_code(self) -> int | None:
        """Return the HTTP status code, if the engine produced one."""
        return self._status_code

    @property
    def is_not_found(self) -> bool:
        """Whether the engine reported an unknown game id (HTTP 404)."""
        return self._status_code == 404

    @property
    def is_bad_request(self) -> bool:
        """Whether the engine rejected the move as illegal (HTTP 400)."""
        return self._status_code == 400
