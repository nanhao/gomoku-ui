"""Immutable snapshot of one game, decoded from the engine's REST response.

The engine answers ``GET /api/v1/games/{game_id}`` with a ``SavedGame`` JSON
object: its ``config`` (board size and rule variant), its ``state`` (board,
winner and current player) and its full ``moves`` history. :class:`GameSnapshot`
mirrors that payload as frozen tuples, so the UI thread can replace the whole
state in one assignment instead of mutating a shared board.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from gomoku_ui.models.move_record import MoveRecord


@dataclass(frozen=True)
class GameSnapshot:
    """Everything the client needs to render a game.

    Attributes:
        game_id: The uuid the engine assigned to the game.
        board_size: Edge length of the square board, e.g. ``15``.
        rule_variant: Rules configuration name, e.g. ``"standard"``.
        winner: ``1`` black, ``2`` white, ``3`` tie, or ``4`` while the game
            is still running, exactly as reported by the engine.
        current_player: ``1`` black or ``2`` white; who must move next.
        board: Rows of cell values: ``0`` empty, ``1`` black, ``2`` white.
        moves: Played moves in chronological order.
    """

    game_id: str
    board_size: int
    rule_variant: str
    winner: int
    current_player: int
    board: tuple[tuple[int, ...], ...]
    moves: tuple[MoveRecord, ...]

    @property
    def move_count(self) -> int:
        """Return the number of moves played so far."""
        return len(self.moves)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Decode a snapshot from the engine's JSON representation.

        Args:
            data: The decoded JSON body of a ``SavedGame`` response: its
                ``game_id``, nested ``config`` and ``state`` objects, and the
                ``moves`` list.

        Returns:
            The decoded snapshot with tuple-backed board and move history.

        Raises:
            ValueError: If a required field is missing, has the wrong type,
                or the board does not match the declared board size.
        """
        try:
            config: Mapping[str, Any] = data["config"]
            state: Mapping[str, Any] = data["state"]
            board_size = int(config["board_size"])
            rows: Sequence[Sequence[Any]] = state["board"]
            board = tuple(tuple(int(cell) for cell in row) for row in rows)
            moves = tuple(MoveRecord.from_dict(move) for move in data["moves"])
            return cls(
                game_id=str(data["game_id"]),
                board_size=board_size,
                rule_variant=str(config["rule_variant"]),
                winner=int(state["winner"]),
                current_player=int(state["current_player"]),
                board=board,
                moves=moves,
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Every decoding problem surfaces as one ValueError; the API
            # client translates it into a user-facing EngineApiError.
            raise ValueError(f"Malformed game snapshot: {exc}") from exc
        if len(board) != board_size or any(len(row) != board_size for row in board):
            # A square board is a hard invariant of gomoku-core; refusing a
            # ragged board here keeps every later cell lookup in bounds.
            raise ValueError(
                f"Board shape does not match board_size={board_size}."
            )
