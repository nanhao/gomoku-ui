"""A move event decoded from the engine's Redis pub/sub channel.

The engine publishes one JSON document per move on
``gomoku:games:{game_id}:events``; the payload also carries the resulting
``current_player`` (as ``next_player``) and ``winner``, so an observer can
advance its board without recomputing any rule. This model mirrors that wire
format and deliberately computes nothing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Self


@dataclass(frozen=True)
class MoveEvent:
    """One decoded move event, exactly as broadcast by the engine.

    Attributes:
        game_id: uuid of the game the move belongs to.
        move_number: 1-based position of this move in the game's history.
        row: 1-based row of the placed stone.
        col: 1-based column of the placed stone.
        player: Stone colour that was placed: ``1`` black or ``2`` white.
        next_player: Player who must move next; after the last move it keeps
            the player who made it.
        winner: Outcome after this move: ``1`` black, ``2`` white, ``3`` tie,
            or ``4`` while the game is still running.
        game_over: Whether this move decided the game, as reported by the
            engine's ``game_over`` field.
    """

    game_id: str
    move_number: int
    row: int
    col: int
    player: int
    next_player: int
    winner: int
    game_over: bool

    # Wire name of the only event type this client understands. Declared as
    # a class variable so it never becomes a dataclass field.
    EVENT_NAME: ClassVar[str] = "move"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self | None:
        """Decode one pub/sub payload.

        Args:
            data: The JSON body of a Redis pub/sub message.

        Returns:
            The decoded event, or ``None`` when the payload is not a move
            event (for example a future event type this client must ignore).

        Raises:
            ValueError: If the payload claims to be a move event but a
                required field is missing or has the wrong type.
        """
        if data.get("event") != cls.EVENT_NAME:
            return None
        try:
            return cls(
                game_id=str(data["game_id"]),
                move_number=int(data["move_number"]),
                row=int(data["row"]),
                col=int(data["col"]),
                player=int(data["player"]),
                next_player=int(data["next_player"]),
                winner=int(data["winner"]),
                # Be tolerant about the flag's JSON type: the engine sends a
                # real boolean, but any truthy value means "game is over".
                game_over=bool(data.get("game_over", False)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Malformed move event: {data!r}") from exc
