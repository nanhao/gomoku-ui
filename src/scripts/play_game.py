"""Entry point of the Gomoku desktop client.

Usage::

    python src/scripts/play_game.py <game_id> --side black

The script is the only module allowed to hold global main code. It parses the
arguments into a :class:`UiConfig`, builds the Tkinter window, wires the
services and the controller, and runs the toolkit main loop until the window
closes. A failed startup (for example an unknown game id) is reported by the
controller, which closes the window, so the process simply exits afterwards.
"""

import sys

from gomoku_ui.config import UiConfig
from gomoku_ui.game.game_controller import GameController
from gomoku_ui.services.engine_api_client import EngineApiClient
from gomoku_ui.services.move_event_subscriber import MoveEventSubscriber
from gomoku_ui.ui.main_window import MainWindow


def main() -> int:
    """Run one client session.

    Returns:
        The process exit code: ``0`` after a normal window close.
    """
    config: UiConfig = UiConfig.from_argv()

    window = MainWindow(config.game_id)
    engine_client = EngineApiClient(config.engine_url)
    subscriber = MoveEventSubscriber(config.redis_url, config.game_id)
    controller = GameController(config, window, engine_client, subscriber)

    window.bind_controller(controller)
    try:
        controller.start()
        window.run()
    finally:
        # Idempotent: the window's close handler normally stopped everything
        # already, but a fatal startup error or an exception can get here
        # first.
        controller.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
