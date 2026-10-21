"""Resolution of the client's runtime configuration.

Every setting is resolved with the same precedence: command-line argument
first, environment variable second, built-in default last. ``UiConfig`` is a
frozen dataclass so the controller can read the endpoints and the chosen side
without defensive copies, and the entry script can hand it around freely.

The side is stored as the engine's own value code (``1`` black, ``2`` white);
the CLI accepts the aliases documented in the README.
"""

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Self


@dataclass(frozen=True)
class UiConfig:
    """Fully resolved configuration of one client session.

    Attributes:
        game_id: uuid of the existing game to load.
        side: Side this client plays: ``1`` black or ``2`` white.
        engine_url: Base URL of the engine REST API, without a trailing slash.
        redis_url: Redis connection URL used for the move event subscription.
    """

    # Engine value codes for the two sides.
    BLACK: ClassVar[int] = 1
    WHITE: ClassVar[int] = 2

    # Accepted ``--side`` spellings, all case-insensitive.
    SIDE_ALIASES: ClassVar[Mapping[str, int]] = {
        "black": 1,
        "b": 1,
        "1": 1,
        "white": 2,
        "w": 2,
        "2": 2,
    }

    # Environment variables and defaults documented in the README.
    ENGINE_URL_ENV_VAR: ClassVar[str] = "GOMOKU_ENGINE_URL"
    REDIS_URL_ENV_VAR: ClassVar[str] = "REDIS_URL"
    DEFAULT_ENGINE_URL: ClassVar[str] = "http://127.0.0.1:8000"
    DEFAULT_REDIS_URL: ClassVar[str] = "redis://localhost:6379/0"

    game_id: str
    side: int
    engine_url: str
    redis_url: str

    @classmethod
    def from_argv(cls, argv: Sequence[str] | None = None) -> Self:
        """Parse command-line arguments and resolve the remaining settings.

        Args:
            argv: Argument list without the program name. ``None`` means
                ``sys.argv[1:]``, which is what the entry script wants.

        Returns:
            The resolved configuration.

        Raises:
            SystemExit: If the arguments are invalid or ``--help`` was used;
                this is argparse's normal behaviour and is handled by the
                interpreter.
        """
        parser: argparse.ArgumentParser = cls._build_parser()
        args: argparse.Namespace = parser.parse_args(argv)
        return cls(
            game_id=args.game_id,
            side=args.side,
            engine_url=(
                args.engine_url
                or os.environ.get(cls.ENGINE_URL_ENV_VAR)
                or cls.DEFAULT_ENGINE_URL
            ).rstrip("/"),
            redis_url=(
                args.redis_url
                or os.environ.get(cls.REDIS_URL_ENV_VAR)
                or cls.DEFAULT_REDIS_URL
            ),
        )

    @classmethod
    def _build_parser(cls) -> argparse.ArgumentParser:
        """Build the CLI parser mirroring the README's CLI reference.

        Returns:
            A parser with the game id, side and endpoint overrides.
        """
        parser = argparse.ArgumentParser(
            prog="play_game.py",
            description=(
                "Load an existing gomoku-engine game, play one side, and "
                "follow the opponent's moves over Redis pub/sub."
            ),
        )
        parser.add_argument(
            "game_id",
            help="uuid of the existing game to load",
        )
        parser.add_argument(
            "--side",
            required=True,
            type=cls._parse_side,
            metavar="{black|white}",
            help="side to play: black/b/1 or white/w/2 (case-insensitive)",
        )
        parser.add_argument(
            "--engine-url",
            default=None,
            help=(
                "base URL of the engine REST API; falls back to the "
                f"{cls.ENGINE_URL_ENV_VAR} environment variable, then to "
                f"{cls.DEFAULT_ENGINE_URL}"
            ),
        )
        parser.add_argument(
            "--redis-url",
            default=None,
            help=(
                "Redis connection URL; falls back to the "
                f"{cls.REDIS_URL_ENV_VAR} environment variable, then to "
                f"{cls.DEFAULT_REDIS_URL}"
            ),
        )
        return parser

    @classmethod
    def _parse_side(cls, value: str) -> int:
        """Translate a ``--side`` spelling into the engine's value code.

        Args:
            value: The raw command-line value.

        Returns:
            ``1`` for black or ``2`` for white.

        Raises:
            argparse.ArgumentTypeError: If the alias is not recognised.
        """
        try:
            return cls.SIDE_ALIASES[value.strip().lower()]
        except KeyError as exc:
            raise argparse.ArgumentTypeError(
                f"invalid side {value!r}; use black/b/1 or white/w/2"
            ) from exc
