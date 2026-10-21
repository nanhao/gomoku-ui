"""One played move, as carried by the engine's REST snapshot.

``gomoku-core`` records every move with 1-based coordinates and the stone
colour that was placed; the engine serialises those records verbatim inside
``SavedGame.moves``. This model mirrors that structure so the client never
works with raw dictionaries.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class MoveRecord:
    """A single move of a game's history.

    Attributes:
        row: 1-based row of the placed stone, matching ``gomoku-core``.
        col: 1-based column of the placed stone, matching ``gomoku-core``.
        player: Stone colour that was placed: ``1`` black or ``2`` white.
    """

    row: int
    col: int
    player: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Decode one move record from the engine's JSON representation.

        Args:
            data: Mapping with ``row``, ``col`` and ``player`` entries, as
                found inside ``SavedGame.moves``.

        Returns:
            The decoded move record.

        Raises:
            ValueError: If a required field is missing or is not an integer.
        """
        try:
            return cls(
                row=int(data["row"]),
                col=int(data["col"]),
                player=int(data["player"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Normalise every field-level problem into one decoding error so
            # callers (the API client) only have to catch ValueError.
            raise ValueError(f"Malformed move record: {data!r}") from exc
