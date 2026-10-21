"""Client-side state of one game and its reconciliation rules.

The engine is the single source of truth. ``ClientGame`` only stores the last
authoritative snapshot, the side the human plays, and the rules that decide
whether an incoming move event may advance the board. It never validates
Gomoku rules, never detects a five-in-a-row and never decides moves; the local
checks in :meth:`pre_check` exist purely to keep obviously invalid clicks from
reaching the engine.

Two channels feed the state: HTTP snapshots (authoritative, can arrive late)
and pub/sub events (one move each, can be duplicated, reordered or missing).
The rules below are therefore mandatory, not defensive extras.
"""

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import ClassVar

from gomoku_ui.game.event_outcome import EventOutcome
from gomoku_ui.models.game_snapshot import GameSnapshot
from gomoku_ui.models.move_event import MoveEvent
from gomoku_ui.models.move_record import MoveRecord


class ClientGame:
    """Board, chosen side and event reconciliation for one game.

    Attributes:
        game_id: uuid of the game this client is attached to.
        side: Side the human plays: ``1`` black or ``2`` white.
    """

    # Value codes mirrored from gomoku-core / gomoku-engine.
    BLACK: ClassVar[int] = 1
    WHITE: ClassVar[int] = 2
    TIE: ClassVar[int] = 3
    RUNNING: ClassVar[int] = 4

    # Display names for the two stone colours.
    PLAYER_NAMES: ClassVar[Mapping[int, str]] = MappingProxyType(
        {1: "black", 2: "white"}
    )

    def __init__(self, game_id: str, side: int) -> None:
        """Create an empty state waiting for the first snapshot.

        Args:
            game_id: uuid of the game to follow.
            side: Side the human plays: ``1`` black or ``2`` white.
        """
        self._game_id: str = game_id
        self._side: int = side
        self._snapshot: GameSnapshot | None = None

    @property
    def game_id(self) -> str:
        """Return the uuid of the game this client is attached to."""
        return self._game_id

    @property
    def side(self) -> int:
        """Return the side this client plays."""
        return self._side

    @property
    def side_label(self) -> str:
        """Return a capitalised side name for display, e.g. ``"Black"``."""
        return self.player_name(self._side).capitalize()

    @property
    def snapshot(self) -> GameSnapshot | None:
        """Return the current snapshot, or ``None`` before the first load."""
        return self._snapshot

    @property
    def has_snapshot(self) -> bool:
        """Whether an authoritative snapshot has been installed."""
        return self._snapshot is not None

    @property
    def move_count(self) -> int:
        """Return the number of moves currently reflected on the board."""
        return self._snapshot.move_count if self._snapshot is not None else 0

    @classmethod
    def player_name(cls, player: int) -> str:
        """Translate a stone value code into a display name.

        Args:
            player: ``1`` black or ``2`` white.

        Returns:
            ``"black"``, ``"white"``, or ``"unknown"`` for unexpected codes.
        """
        return cls.PLAYER_NAMES.get(player, "unknown")

    def replace_snapshot(self, snapshot: GameSnapshot) -> bool:
        """Install a snapshot unless it is stale or belongs to another game.

        A snapshot can arrive after a newer pub/sub event already advanced the
        board; such a stale snapshot is discarded. Equal move counts are
        accepted because the engine's snapshot is authoritative on the final
        state (for example ``winner``).

        Args:
            snapshot: The snapshot returned by the engine.

        Returns:
            ``True`` when the snapshot became the current state; ``False``
            when it was rejected.
        """
        if snapshot.game_id != self._game_id:
            # One client is bound to one game; foreign snapshots are a bug.
            return False
        if self._snapshot is not None and snapshot.move_count < self._snapshot.move_count:
            # An event for a newer move already advanced the board; this
            # response was overtaken in flight.
            return False
        self._snapshot = snapshot
        return True

    def apply_event(self, event: MoveEvent) -> EventOutcome:
        """Apply one pub/sub event when it is exactly the next move.

        Args:
            event: The decoded move event.

        Returns:
            :attr:`EventOutcome.APPLIED` when the board advanced,
            :attr:`EventOutcome.IGNORED` for stale/duplicate/foreign events,
            or :attr:`EventOutcome.GAP` when the event is ahead of the board
            and a snapshot reload is required.
        """
        snapshot: GameSnapshot | None = self._snapshot
        if snapshot is None or event.game_id != self._game_id:
            return EventOutcome.IGNORED
        expected: int = snapshot.move_count + 1
        if event.move_number <= snapshot.move_count:
            # Duplicate, replay, or the echo of our own move, which the POST
            # response already reflected.
            return EventOutcome.IGNORED
        if event.move_number > expected:
            # At least one move was missed; never guess the missing cells.
            return EventOutcome.GAP
        if not self._is_on_board(snapshot, event.row, event.col):
            # A structurally invalid event cannot be placed; resync instead
            # of raising inside the UI thread.
            return EventOutcome.GAP

        # Build a new immutable snapshot exactly the way the engine reports
        # it: the stone from the event, and next_player/winner as broadcast.
        board = [list(row) for row in snapshot.board]
        board[event.row - 1][event.col - 1] = event.player
        moves = snapshot.moves + (
            MoveRecord(row=event.row, col=event.col, player=event.player),
        )
        self._snapshot = replace(
            snapshot,
            board=tuple(tuple(row) for row in board),
            moves=moves,
            current_player=event.next_player,
            winner=event.winner,
        )
        return EventOutcome.APPLIED

    def cell_at(self, row: int, col: int) -> int:
        """Return the cell value at a 1-based point.

        Args:
            row: 1-based row.
            col: 1-based column.

        Returns:
            ``0`` empty, ``1`` black, ``2`` white, or ``0`` when no snapshot
            is installed or the point is off the board.
        """
        snapshot: GameSnapshot | None = self._snapshot
        if snapshot is None or not self._is_on_board(snapshot, row, col):
            return 0
        return snapshot.board[row - 1][col - 1]

    def last_move(self) -> MoveRecord | None:
        """Return the most recent move, or ``None`` before the first move."""
        snapshot: GameSnapshot | None = self._snapshot
        if snapshot is None or not snapshot.moves:
            return None
        return snapshot.moves[-1]

    def is_game_over(self) -> bool:
        """Whether the engine has reported an outcome other than running."""
        snapshot: GameSnapshot | None = self._snapshot
        return snapshot is not None and snapshot.winner != self.RUNNING

    def is_my_turn(self) -> bool:
        """Whether the engine says it is this player's side to move."""
        snapshot: GameSnapshot | None = self._snapshot
        return (
            snapshot is not None
            and not self.is_game_over()
            and snapshot.current_player == self._side
        )

    def pre_check(self, row: int, col: int) -> str | None:
        """Reject obviously invalid clicks for a better user experience.

        This is deliberately a UX filter, not a rules engine: the engine still
        validates every submitted move and stays the authority.

        Args:
            row: 1-based row of the clicked intersection.
            col: 1-based column of the clicked intersection.

        Returns:
            ``None`` when the move may be submitted, otherwise a short message
            explaining why it cannot.
        """
        snapshot: GameSnapshot | None = self._snapshot
        if snapshot is None:
            return "The game is still loading."
        if not self._is_on_board(snapshot, row, col):
            return "That point is outside the board."
        if snapshot.board[row - 1][col - 1] != 0:
            return "That intersection is already occupied."
        if self.is_game_over():
            return "The game is over."
        if not self.is_my_turn():
            current: str = self.player_name(snapshot.current_player)
            return f"It is {current}'s turn, not yours."
        return None

    def status_text(self) -> str:
        """Describe the current turn or result for the status line.

        Returns:
            A short sentence suitable for the window's status label.
        """
        snapshot: GameSnapshot | None = self._snapshot
        if snapshot is None:
            return f"Loading game {self._game_id} ..."
        result: str | None = self.result_text()
        if result is not None:
            return result
        if self.is_my_turn():
            return f"Your turn. Place a {self.player_name(self._side)} stone."
        current: str = self.player_name(snapshot.current_player)
        return f"Waiting for opponent ({current} to move)."

    def result_text(self) -> str | None:
        """Return the final result, or ``None`` while the game is running."""
        snapshot: GameSnapshot | None = self._snapshot
        if snapshot is None or snapshot.winner == self.RUNNING:
            return None
        if snapshot.winner == self.TIE:
            return "Game over: the game is a tie."
        return f"Game over: {self.player_name(snapshot.winner)} wins."

    @classmethod
    def _is_on_board(cls, snapshot: GameSnapshot, row: int, col: int) -> bool:
        """Whether a 1-based point lies on the snapshot's board.

        Args:
            snapshot: The board whose size defines the bounds.
            row: 1-based row.
            col: 1-based column.

        Returns:
            ``True`` when both coordinates are within ``1..board_size``.
        """
        size: int = snapshot.board_size
        return 1 <= row <= size and 1 <= col <= size
