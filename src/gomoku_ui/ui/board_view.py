"""Canvas rendering of the board, with click and hover handling.

The board is drawn from the latest authoritative snapshot: grid, star points,
stones, a marker on the last move and a ghost stone under the pointer. Click
positions are converted into 1-based ``(row, col)`` intersections entirely
inside this class; nothing outside ``gomoku_ui.ui`` ever sees a pixel.

All geometry is recomputed from the canvas size and the DPI scale on every
``<Configure>`` event, so resizing the window or moving it to a monitor with a
different scale keeps the board square and clickable.
"""

import math
import tkinter as tk
from collections.abc import Callable
from typing import Final

from gomoku_ui.models.game_snapshot import GameSnapshot
from gomoku_ui.ui.dpi_scaler import DpiScaler


class BoardView:
    """A ``tk.Canvas`` that renders a gomoku board and maps clicks to cells."""

    # Palette: warm wood, dark grid, near-black and off-white stones.
    BOARD_COLOR: Final[str] = "#E4BC7F"
    GRID_COLOR: Final[str] = "#4A3520"
    BLACK_STONE_COLOR: Final[str] = "#151515"
    WHITE_STONE_COLOR: Final[str] = "#F7F3E8"
    WHITE_STONE_OUTLINE: Final[str] = "#6B6257"
    LAST_MOVE_COLOR: Final[str] = "#C0392B"
    HOVER_COLOR: Final[str] = "#7A6A55"

    # Geometry expressed in design pixels at 100% scaling.
    BOARD_PADDING: Final[int] = 24
    INITIAL_SIZE: Final[int] = 600
    STONE_RADIUS_RATIO: Final[float] = 0.42
    LAST_MOVE_RADIUS_RATIO: Final[float] = 0.16
    STAR_POINT_RADIUS_RATIO: Final[float] = 0.08
    CLICK_TOLERANCE_RATIO: Final[float] = 0.5

    def __init__(
        self,
        master: tk.Misc,
        scaler: DpiScaler,
        on_click: Callable[[int, int], None],
    ) -> None:
        """Create the canvas widget.

        Args:
            master: Parent widget the canvas is placed in.
            scaler: DPI scale used for every pixel size.
            on_click: Callback invoked with 1-based ``(row, col)`` when an
                enabled board is clicked on an intersection.
        """
        self._scaler: DpiScaler = scaler
        self._on_click: Callable[[int, int], None] = on_click
        self._canvas: tk.Canvas = tk.Canvas(
            master,
            background=self.BOARD_COLOR,
            highlightthickness=0,
            width=scaler.px(self.INITIAL_SIZE),
            height=scaler.px(self.INITIAL_SIZE),
        )
        self._snapshot: GameSnapshot | None = None
        self._enabled: bool = False
        self._hover: tuple[int, int] | None = None

        # Board geometry, recomputed by _update_geometry before every draw.
        self._cell_size: float = 0.0
        self._origin_x: float = 0.0
        self._origin_y: float = 0.0

        self._canvas.bind("<Configure>", self._on_configure)
        self._canvas.bind("<Button-1>", self._on_button_press)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", self._on_leave)
        self._canvas.configure(cursor="arrow")

    @property
    def widget(self) -> tk.Canvas:
        """Return the canvas so the parent can place it in its layout."""
        return self._canvas

    def render(self, snapshot: GameSnapshot) -> None:
        """Replace the displayed board with a new authoritative snapshot.

        Args:
            snapshot: The state to draw.
        """
        self._snapshot = snapshot
        self._hover = None
        self._update_geometry()
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        """Allow or block click and hover handling.

        Args:
            enabled: ``True`` only while it is the player's turn and no move
                request is in flight.
        """
        self._enabled = enabled
        self._canvas.configure(cursor="hand2" if enabled else "arrow")
        if not enabled and self._hover is not None:
            self._hover = None
            self._draw()

    def _on_configure(self, event: tk.Event) -> None:
        """Recompute the geometry after a resize and redraw.

        Args:
            event: The ``<Configure>`` event carrying the new size.
        """
        self._update_geometry()
        self._draw()

    def _on_button_press(self, event: tk.Event) -> None:
        """Translate a click into a controller callback.

        Args:
            event: The mouse event with widget-relative coordinates.
        """
        if not self._enabled:
            return
        point: tuple[int, int] | None = self._pixel_to_cell(event.x, event.y)
        if point is not None:
            self._on_click(point[0], point[1])

    def _on_motion(self, event: tk.Event) -> None:
        """Track the intersection under the pointer for the ghost stone.

        Args:
            event: The motion event with widget-relative coordinates.
        """
        snapshot: GameSnapshot | None = self._snapshot
        if not self._enabled or snapshot is None:
            return
        point: tuple[int, int] | None = self._pixel_to_cell(event.x, event.y)
        if point is not None and snapshot.board[point[0] - 1][point[1] - 1] != 0:
            # Never preview a stone on an occupied intersection.
            point = None
        if point != self._hover:
            self._hover = point
            self._draw()

    def _on_leave(self, event: tk.Event) -> None:
        """Clear the hover preview when the pointer leaves the canvas.

        Args:
            event: The ``<Leave>`` event; unused.
        """
        if self._hover is not None:
            self._hover = None
            self._draw()

    def _update_geometry(self) -> None:
        """Fit the square grid into the canvas, keeping it centred."""
        snapshot: GameSnapshot | None = self._snapshot
        if snapshot is None:
            return
        width: int = max(self._canvas.winfo_width(), 1)
        height: int = max(self._canvas.winfo_height(), 1)
        padding: int = self._scaler.px(self.BOARD_PADDING)
        usable: float = max(float(min(width, height) - 2 * padding), 1.0)
        gaps: int = max(snapshot.board_size - 1, 1)
        self._cell_size = usable / gaps
        self._origin_x = (width - self._cell_size * gaps) / 2.0
        self._origin_y = (height - self._cell_size * gaps) / 2.0

    def _draw(self) -> None:
        """Redraw every canvas item from the current snapshot."""
        self._canvas.delete("all")
        snapshot: GameSnapshot | None = self._snapshot
        if snapshot is None:
            return
        self._draw_grid(snapshot)
        self._draw_star_points(snapshot)
        self._draw_stones(snapshot)
        self._draw_last_move(snapshot)
        self._draw_hover(snapshot)

    def _draw_grid(self, snapshot: GameSnapshot) -> None:
        """Draw the horizontal and vertical grid lines.

        Args:
            snapshot: Board whose size defines the grid.
        """
        span: float = self._cell_size * (snapshot.board_size - 1)
        width: int = self._scaler.px(1)
        for index in range(snapshot.board_size):
            offset: float = index * self._cell_size
            x: float = self._origin_x + offset
            y: float = self._origin_y + offset
            self._canvas.create_line(
                self._origin_x,
                y,
                self._origin_x + span,
                y,
                fill=self.GRID_COLOR,
                width=width,
            )
            self._canvas.create_line(
                x,
                self._origin_y,
                x,
                self._origin_y + span,
                fill=self.GRID_COLOR,
                width=width,
            )

    def _draw_star_points(self, snapshot: GameSnapshot) -> None:
        """Draw the traditional reference dots on larger boards.

        Args:
            snapshot: Board whose size decides whether dots are appropriate.
        """
        radius: float = self._cell_size * self.STAR_POINT_RADIUS_RATIO
        for row, col in self._star_points(snapshot.board_size):
            cx, cy = self._point_center(row, col)
            self._canvas.create_oval(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                fill=self.GRID_COLOR,
                outline=self.GRID_COLOR,
            )

    def _draw_stones(self, snapshot: GameSnapshot) -> None:
        """Draw every stone present on the board.

        Args:
            snapshot: Board with the cell values to render.
        """
        for row_index, row in enumerate(snapshot.board):
            for col_index, value in enumerate(row):
                if value != 0:
                    self._draw_stone(row_index + 1, col_index + 1, value)

    def _draw_stone(self, row: int, col: int, value: int) -> None:
        """Draw one stone at a 1-based intersection.

        Args:
            row: 1-based row of the stone.
            col: 1-based column of the stone.
            value: ``1`` for black or ``2`` for white.
        """
        cx, cy = self._point_center(row, col)
        radius: float = self._cell_size * self.STONE_RADIUS_RATIO
        if value == 1:
            fill: str = self.BLACK_STONE_COLOR
            outline: str = self.BLACK_STONE_COLOR
        else:
            fill = self.WHITE_STONE_COLOR
            outline = self.WHITE_STONE_OUTLINE
        self._canvas.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            fill=fill,
            outline=outline,
            width=self._scaler.px(1),
        )

    def _draw_last_move(self, snapshot: GameSnapshot) -> None:
        """Mark the most recent stone so both players can follow the game.

        Args:
            snapshot: Board whose last move should be marked.
        """
        if not snapshot.moves:
            return
        last = snapshot.moves[-1]
        cx, cy = self._point_center(last.row, last.col)
        radius: float = self._cell_size * self.LAST_MOVE_RADIUS_RATIO
        self._canvas.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            outline=self.LAST_MOVE_COLOR,
            width=self._scaler.px(2),
        )

    def _draw_hover(self, snapshot: GameSnapshot) -> None:
        """Draw a dashed ghost stone under the pointer.

        Args:
            snapshot: Board used to ignore occupied intersections.
        """
        if not self._enabled or self._hover is None:
            return
        row, col = self._hover
        if snapshot.board[row - 1][col - 1] != 0:
            return
        cx, cy = self._point_center(row, col)
        radius: float = self._cell_size * self.STONE_RADIUS_RATIO
        self._canvas.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            outline=self.HOVER_COLOR,
            width=self._scaler.px(1),
            dash=(3, 2),
        )

    def _star_points(self, board_size: int) -> set[tuple[int, int]]:
        """Return the 1-based star point positions for a board size.

        Args:
            board_size: Edge length of the square board.

        Returns:
            The reference intersections; empty for boards smaller than 9.
        """
        if board_size < 9:
            return set()
        edge: int = 4 if board_size >= 13 else 3
        far: int = board_size - (edge - 1)
        points: set[tuple[int, int]] = {
            (edge, edge),
            (edge, far),
            (far, edge),
            (far, far),
        }
        if board_size % 2 == 1:
            centre: int = (board_size + 1) // 2
            points.add((centre, centre))
        return points

    def _point_center(self, row: int, col: int) -> tuple[float, float]:
        """Return the canvas coordinates of a 1-based intersection.

        Args:
            row: 1-based row.
            col: 1-based column.

        Returns:
            The pixel centre of that intersection.
        """
        return (
            self._origin_x + (col - 1) * self._cell_size,
            self._origin_y + (row - 1) * self._cell_size,
        )

    def _pixel_to_cell(self, x: int, y: int) -> tuple[int, int] | None:
        """Convert widget pixels into the nearest 1-based intersection.

        A click counts only when it falls inside the canvas grid and close
        enough to an intersection; clicks on the surrounding padding or far
        from any crossing return ``None``.

        Args:
            x: Horizontal pixel coordinate inside the canvas.
            y: Vertical pixel coordinate inside the canvas.

        Returns:
            The 1-based ``(row, col)`` point, or ``None`` when the click does
            not belong to an intersection.
        """
        snapshot: GameSnapshot | None = self._snapshot
        if snapshot is None or self._cell_size <= 0.0:
            return None
        col: int = round((x - self._origin_x) / self._cell_size) + 1
        row: int = round((y - self._origin_y) / self._cell_size) + 1
        if not (1 <= row <= snapshot.board_size and 1 <= col <= snapshot.board_size):
            return None
        cx, cy = self._point_center(row, col)
        tolerance: float = self._cell_size * self.CLICK_TOLERANCE_RATIO
        if math.hypot(x - cx, y - cy) > tolerance:
            return None
        return row, col
