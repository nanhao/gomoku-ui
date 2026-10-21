"""Abstract boundary between the controller and any concrete window.

The controller orchestrates services, timers and state, but it never imports a
GUI toolkit. Everything it needs to show is expressed as plain method calls on
this interface, so the Tkinter widgets in ``gomoku_ui.ui`` can be replaced by
another toolkit -- or by a no-op fake in tests -- without touching the game
layer. All methods are called on the UI thread only.
"""

import abc

from gomoku_ui.models.game_snapshot import GameSnapshot
from gomoku_ui.services.subscriber_status import SubscriberStatus


class GameView(abc.ABC):
    """The window contract the controller drives."""

    @abc.abstractmethod
    def render_snapshot(self, snapshot: GameSnapshot, side_label: str) -> None:
        """Draw the authoritative board state and the player's side.

        Args:
            snapshot: The state to display, including move history and
                outcome.
            side_label: Human-readable side this client plays, e.g.
                ``"black"``.
        """

    @abc.abstractmethod
    def set_status(self, text: str) -> None:
        """Replace the turn/result text on the status line.

        Args:
            text: A short, already formatted sentence for the player.
        """

    @abc.abstractmethod
    def set_subscription_status(self, status: SubscriberStatus) -> None:
        """Show whether live updates are connecting, live or paused.

        Args:
            status: The subscriber's latest connection state.
        """

    @abc.abstractmethod
    def set_input_enabled(self, enabled: bool) -> None:
        """Allow or block board clicks.

        Args:
            enabled: ``True`` when it is the player's turn and no request is
                in flight.
        """

    @abc.abstractmethod
    def set_error(self, message: str | None) -> None:
        """Show a non-fatal error, or clear the error area.

        Args:
            message: The message to display, or ``None`` to hide the area.
        """

    @abc.abstractmethod
    def show_fatal_error(self, message: str) -> None:
        """Report an unrecoverable startup error and close the window.

        Args:
            message: Explanation shown in a modal dialog before closing.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Destroy the window and leave the toolkit main loop."""
