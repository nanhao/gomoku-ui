"""Request body for ``POST /api/v1/games/{game_id}/moves``.

The engine accepts ``{"row": r, "col": c}`` with 1-based coordinates. Keeping
the body as a dataclass instead of an ad-hoc dictionary gives the payload a
single definition and one ``to_dict`` conversion point, mirroring the engine's
own ``MakeMoveRequest`` model.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MakeMoveRequest:
    """One move submitted to the engine.

    Attributes:
        row: 1-based row of the stone to place.
        col: 1-based column of the stone to place.
    """

    row: int
    col: int

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable body expected by the engine.

        Returns:
            A dictionary with the two 1-based coordinates.
        """
        return {"row": self.row, "col": self.col}
