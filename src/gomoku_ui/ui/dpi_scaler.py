"""Windows high-DPI support for the Tkinter window.

Tk 8.6 does not declare DPI awareness by itself, so at 125--200% display
scaling Windows bitmap-stretches the whole window and everything looks blurry.
A process can only opt into DPI awareness *before* it creates its first Tk
window, which is why :meth:`DpiScaler.declare_awareness` must be called before
``tkinter.Tk()`` exists.

After the root window is up, the scaler reads the resulting DPI once and
exposes a factor. Every pixel size in the UI (board geometry, padding, line
widths, initial window size) is derived from that factor, so no widget
hard-codes a pixel value.
"""

import ctypes
import sys
import tkinter as tk
from typing import ClassVar


class DpiScaler:
    """Per-monitor DPI awareness and one scale factor for the whole window.

    Attributes:
        factor: Display scale, ``1.0`` at 96 DPI (100%), ``1.5`` at 150%, and
            so on, clamped to a sane range.
    """

    # DPI Tk assumes when it reports 100% scaling on Windows.
    BASE_DPI: ClassVar[float] = 96.0

    # Guard rails against nonsensical DPI values reported by some virtual
    # displays, so geometry never collapses or explodes.
    MIN_FACTOR: ClassVar[float] = 0.75
    MAX_FACTOR: ClassVar[float] = 4.0

    @classmethod
    def declare_awareness(cls) -> None:
        """Enable per-monitor DPI awareness for this process.

        Must run before the first ``tkinter.Tk()`` is created, otherwise the
        process setting is locked and the window stays bitmap-stretched.
        Silently degrades on non-Windows platforms or when the newer API is
        unavailable, falling back to the older system-wide call.
        """
        if sys.platform != "win32":
            return
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE (2): the window follows the DPI of
            # the monitor it is currently on.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except (AttributeError, OSError):
            # shcore.dll is missing before Windows 8.1; try the system-wide
            # API introduced with Vista instead.
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            # Awareness is best-effort: at worst the window is stretched.
            pass

    def __init__(self, root: tk.Tk) -> None:
        """Read the display DPI once and configure Tk's point conversion.

        Args:
            root: The root window, created after
                :meth:`declare_awareness` was called.
        """
        dpi: float = float(root.winfo_fpixels("1i"))
        self._factor: float = min(
            max(dpi / self.BASE_DPI, self.MIN_FACTOR),
            self.MAX_FACTOR,
        )
        # Make fonts specified in points follow the physical DPI as well;
        # otherwise ttk labels stay tiny on high-DPI screens.
        root.tk.call("tk", "scaling", dpi / 72.0)

    @property
    def factor(self) -> float:
        """Return the display scale factor."""
        return self._factor

    def px(self, value: float) -> int:
        """Scale a design pixel value to the current display.

        Args:
            value: Size in pixels at 100% scaling.

        Returns:
            The size in physical pixels, at least one pixel.
        """
        return max(1, int(round(value * self._factor)))
