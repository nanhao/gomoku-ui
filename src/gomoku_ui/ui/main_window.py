"""The Tkinter window implementing the ``GameView`` boundary.

This is the only module that builds widgets. It owns the frame layout, the
board canvas, the status line, the error banner, the refresh action and the
``root.after`` tick that lets the controller drain its queues on the UI
thread. Everything the controller needs is expressed by the abstract
``GameView`` methods, so no game logic lives here.

DPI awareness is declared before ``tkinter.Tk()`` is created and every pixel
value is derived from :class:`DpiScaler`.
"""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Final

from gomoku_ui.game.game_controller import GameController
from gomoku_ui.game.game_view import GameView
from gomoku_ui.models.game_snapshot import GameSnapshot
from gomoku_ui.services.subscriber_status import SubscriberStatus
from gomoku_ui.ui.board_view import BoardView
from gomoku_ui.ui.dpi_scaler import DpiScaler


class MainWindow(GameView):
    """Tkinter window with a board, a status line and a refresh action."""

    # Window sizing, in design pixels at 100% scaling.
    INITIAL_WIDTH: Final[int] = 760
    INITIAL_HEIGHT: Final[int] = 860
    MIN_WIDTH: Final[int] = 520
    MIN_HEIGHT: Final[int] = 640

    # Fonts and colours; sizes are point sizes, scaled by Tk's DPI setting.
    FONT_FAMILY: Final[str] = "Segoe UI"
    HEADING_FONT_SIZE: Final[int] = 12
    BODY_FONT_SIZE: Final[int] = 10
    ERROR_COLOR: Final[str] = "#B00020"
    LIVE_COLOR: Final[str] = "#1B7F3A"
    WARN_COLOR: Final[str] = "#B26A00"

    def __init__(self, game_id: str) -> None:
        """Declare DPI awareness, create the root window and build the UI.

        Args:
            game_id: uuid of the game, shown in the window title.
        """
        # DPI awareness must be set before the first Tk window exists.
        DpiScaler.declare_awareness()
        self._root: tk.Tk = tk.Tk()
        self._scaler: DpiScaler = DpiScaler(self._root)
        self._controller: GameController | None = None
        self._tick_job: str | None = None
        self._closed: bool = False

        self._side_var: tk.StringVar = tk.StringVar(value="You play -")
        self._moves_var: tk.StringVar = tk.StringVar(value="Moves: 0")
        self._status_var: tk.StringVar = tk.StringVar(value="Starting ...")
        self._live_var: tk.StringVar = tk.StringVar(value="")
        self._error_var: tk.StringVar = tk.StringVar(value="")

        self._root.title(f"Gomoku - {game_id}")
        self._root.minsize(
            self._scaler.px(self.MIN_WIDTH),
            self._scaler.px(self.MIN_HEIGHT),
        )
        self._centre_window()

        self._configure_styles()
        self._board = BoardView(self._root, self._scaler, self._on_board_click)
        self._build_layout()
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._root.bind("<F5>", self._on_refresh_key)

    def bind_controller(self, controller: GameController) -> None:
        """Attach the controller that receives clicks and refresh requests.

        Args:
            controller: The session controller driving this window.
        """
        self._controller = controller

    def run(self) -> None:
        """Start the UI timer and enter the toolkit main loop."""
        self._schedule_tick()
        self._root.mainloop()

    def render_snapshot(self, snapshot: GameSnapshot, side_label: str) -> None:
        """Draw the board and update the side and move counters.

        Args:
            snapshot: Authoritative state to display.
            side_label: Side this client plays, e.g. ``"Black"``.
        """
        self._board.render(snapshot)
        self._side_var.set(f"You play {side_label}")
        self._moves_var.set(f"Moves: {snapshot.move_count}")
        self._root.title(f"Gomoku - {side_label} - {snapshot.game_id}")

    def set_status(self, text: str) -> None:
        """Set the turn or result text on the status line.

        Args:
            text: Short sentence produced by ``ClientGame``.
        """
        self._status_var.set(text)

    def set_subscription_status(self, status: SubscriberStatus) -> None:
        """Show the state of the live update channel.

        Args:
            status: Latest subscriber state.
        """
        if status is SubscriberStatus.LIVE:
            self._live_var.set("Live updates: live")
            self._live_label.configure(style="LiveOk.TLabel")
        elif status is SubscriberStatus.PAUSED:
            self._live_var.set("Live updates paused - retrying")
            self._live_label.configure(style="LivePaused.TLabel")
        else:
            self._live_var.set("Live updates: connecting ...")
            self._live_label.configure(style="LiveConnecting.TLabel")

    def set_input_enabled(self, enabled: bool) -> None:
        """Allow or block board clicks.

        Args:
            enabled: Whether the player may click the board.
        """
        self._board.set_enabled(enabled)

    def set_error(self, message: str | None) -> None:
        """Show or clear the error banner.

        Args:
            message: Error text, or ``None`` to clear the banner.
        """
        self._error_var.set(message or "")

    def show_fatal_error(self, message: str) -> None:
        """Show a modal error dialog and close the window.

        Args:
            message: Explanation of the unrecoverable startup problem.
        """
        messagebox.showerror("Gomoku", message, parent=self._root)
        self.close()

    def close(self) -> None:
        """Cancel the timer and destroy the root window."""
        if self._closed:
            return
        self._closed = True
        self._cancel_tick()
        self._root.destroy()

    def _configure_styles(self) -> None:
        """Create the ttk styles used by the status widgets."""
        style = ttk.Style(self._root)
        style.configure("Error.TLabel", foreground=self.ERROR_COLOR)
        style.configure("LiveOk.TLabel", foreground=self.LIVE_COLOR)
        style.configure("LivePaused.TLabel", foreground=self.ERROR_COLOR)
        style.configure("LiveConnecting.TLabel", foreground=self.WARN_COLOR)

    def _build_layout(self) -> None:
        """Create and place every widget of the window."""
        container = ttk.Frame(self._root, padding=self._scaler.px(12))
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container)
        header.pack(fill=tk.X)
        ttk.Label(
            header,
            textvariable=self._side_var,
            font=(self.FONT_FAMILY, self.HEADING_FONT_SIZE, "bold"),
            anchor="w",
        ).pack(side=tk.LEFT)
        ttk.Label(
            header,
            textvariable=self._moves_var,
            font=(self.FONT_FAMILY, self.BODY_FONT_SIZE),
            anchor="e",
        ).pack(side=tk.RIGHT)

        self._board.widget.pack(
            fill=tk.BOTH,
            expand=True,
            pady=self._scaler.px(10),
        )

        ttk.Label(
            container,
            textvariable=self._error_var,
            style="Error.TLabel",
            anchor="w",
            wraplength=self._scaler.px(self.INITIAL_WIDTH - 48),
        ).pack(fill=tk.X)

        footer = ttk.Frame(container)
        footer.pack(fill=tk.X, pady=(self._scaler.px(8), 0))
        ttk.Label(
            footer,
            textvariable=self._status_var,
            font=(self.FONT_FAMILY, self.BODY_FONT_SIZE),
            anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._refresh_button = ttk.Button(
            footer,
            text="Refresh (F5)",
            command=self._on_refresh,
        )
        self._refresh_button.pack(side=tk.RIGHT)

        self._live_label = ttk.Label(
            container,
            textvariable=self._live_var,
            style="LiveConnecting.TLabel",
            anchor="w",
        )
        self._live_label.pack(fill=tk.X, pady=(self._scaler.px(4), 0))

    def _centre_window(self) -> None:
        """Place the initial window in the middle of the screen."""
        width: int = self._scaler.px(self.INITIAL_WIDTH)
        height: int = self._scaler.px(self.INITIAL_HEIGHT)
        x: int = max((self._root.winfo_screenwidth() - width) // 2, 0)
        y: int = max((self._root.winfo_screenheight() - height) // 2, 0)
        self._root.geometry(f"{width}x{height}+{x}+{y}")

    def _schedule_tick(self) -> None:
        """Schedule the next queue-draining tick on the UI thread."""
        if self._closed:
            return
        self._tick_job = self._root.after(
            GameController.TICK_INTERVAL_MS,
            self._on_tick,
        )

    def _cancel_tick(self) -> None:
        """Cancel a scheduled tick, if any."""
        if self._tick_job is not None:
            self._root.after_cancel(self._tick_job)
            self._tick_job = None

    def _on_tick(self) -> None:
        """Run one controller tick and schedule the next one."""
        self._tick_job = None
        if self._closed:
            return
        if self._controller is not None:
            self._controller.tick()
        self._schedule_tick()

    def _on_board_click(self, row: int, col: int) -> None:
        """Forward a board click to the controller.

        Args:
            row: 1-based row of the clicked intersection.
            col: 1-based column of the clicked intersection.
        """
        if self._controller is not None:
            self._controller.handle_click(row, col)

    def _on_refresh(self) -> None:
        """Reload the snapshot on explicit request."""
        if self._controller is not None:
            self._controller.request_refresh()

    def _on_refresh_key(self, event: tk.Event) -> None:
        """Handle the F5 shortcut.

        Args:
            event: The keyboard event; unused.
        """
        self._on_refresh()

    def _on_close(self) -> None:
        """Stop the session, then destroy the window."""
        if self._controller is not None:
            self._controller.shutdown()
        self.close()
