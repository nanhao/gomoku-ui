"""Toolkit-specific presentation layer.

The toolkit is Tkinter (``tkinter`` + ``tkinter.ttk``). This package implements
the ``gomoku_ui.game.game_view.GameView`` boundary with concrete windows, a
canvas-based board and input handling; all other packages stay toolkit-agnostic,
so no module outside ``gomoku_ui.ui`` may import ``tkinter``.
"""
