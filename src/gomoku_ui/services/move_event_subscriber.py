"""Redis pub/sub subscriber for the live move events of one game.

The subscriber owns exactly one background thread. The thread connects to
Redis, subscribes to ``gomoku:games:{game_id}:events``, waits for the server's
``subscribe`` confirmation (so no event published after that point is missed),
and then polls for messages. Decoded :class:`MoveEvent` values go to a queue;
connection state goes to a second queue. Neither worker ever touches the view;
the controller drains both queues on the UI thread, which keeps this module and
the whole game layer toolkit-agnostic.

Delivery is best-effort on the engine side, so a dropped connection can lose
events. The controller reacts to every fresh ``LIVE`` status by reloading the
snapshot, which is what closes such gaps.
"""

import json
import logging
import threading
import time
from collections.abc import Mapping
from queue import Queue
from typing import Any, Final

import redis

from gomoku_ui.models.move_event import MoveEvent
from gomoku_ui.services.subscriber_status import SubscriberStatus


class MoveEventSubscriber:
    """Subscribes to one game's move events on a dedicated daemon thread.

    Attributes:
        channel: The pub/sub channel this subscriber listens on.
    """

    # Channel naming shared with the engine's publisher; the game id is the
    # only variable part. Keeping the template here documents the contract.
    CHANNEL_TEMPLATE: Final[str] = "gomoku:games:{game_id}:events"

    # How long one ``get_message`` poll waits before the loop re-checks the
    # stop flag. Also bounds how quickly ``stop()`` can join the thread.
    POLL_SECONDS: Final[float] = 0.5

    # How long to wait for Redis to confirm the subscription before treating
    # the connection as failed.
    SUBSCRIBE_CONFIRM_SECONDS: Final[float] = 10.0

    # Reconnect delay grows from the initial value up to the cap.
    INITIAL_BACKOFF_SECONDS: Final[float] = 1.0
    MAX_BACKOFF_SECONDS: Final[float] = 30.0

    # Socket timeouts so a half-dead connection cannot hang the thread.
    SOCKET_TIMEOUT_SECONDS: Final[float] = 5.0

    # How long ``stop()`` waits for the thread to finish.
    JOIN_TIMEOUT_SECONDS: Final[float] = 2.0

    def __init__(self, redis_url: str, game_id: str) -> None:
        """Create a subscriber; no connection is opened until ``start()``.

        Args:
            redis_url: Redis connection URL the engine publishes to.
            game_id: uuid of the game whose channel should be observed.
        """
        self._redis_url: str = redis_url
        self._game_id: str = game_id
        self._channel: str = self.CHANNEL_TEMPLATE.format(game_id=game_id)

        # Cross-thread hand-off to the controller. Only this thread writes.
        self._events: Queue[MoveEvent] = Queue()
        self._statuses: Queue[SubscriberStatus] = Queue()

        self._stop_event: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        # Only touched by the subscriber thread; reset after every successful
        # subscription confirmation so an old outage does not slow the next
        # reconnect down.
        self._backoff: float = self.INITIAL_BACKOFF_SECONDS
        self._logger: logging.Logger = logging.getLogger(__name__)

    @property
    def channel(self) -> str:
        """Return the pub/sub channel this subscriber listens on."""
        return self._channel

    @property
    def events(self) -> Queue[MoveEvent]:
        """Return the queue receiving decoded move events."""
        return self._events

    @property
    def statuses(self) -> Queue[SubscriberStatus]:
        """Return the queue receiving connection state changes."""
        return self._statuses

    def start(self) -> None:
        """Spawn the subscriber thread; returning immediately.

        Calling ``start()`` while the thread is already running is a no-op,
        which keeps the controller's startup path idempotent.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"move-events-{self._game_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Ask the thread to stop and wait briefly for it to finish.

        The thread releases its pub/sub object and connection in its ``finally``
        block, so no Redis handle is leaked when the window closes. Safe to
        call more than once.
        """
        self._stop_event.set()
        thread: threading.Thread | None = self._thread
        if thread is None:
            return
        thread.join(timeout=self.JOIN_TIMEOUT_SECONDS)
        if thread.is_alive():
            # Daemon thread: it will exit on its own once the in-flight Redis
            # call times out, and it can no longer block process shutdown.
            self._logger.warning(
                "Subscriber thread for game %s is still winding down.",
                self._game_id,
            )
        else:
            self._thread = None

    def _run(self) -> None:
        """Connect, listen, and reconnect with capped exponential backoff."""
        while not self._stop_event.is_set():
            try:
                self._run_session()
                # A clean session return only happens after stop().
                return
            except (redis.exceptions.RedisError, OSError, TimeoutError, ValueError) as exc:
                self._logger.warning(
                    "Live updates for game %s paused: %s", self._game_id, exc
                )
                self._statuses.put(SubscriberStatus.PAUSED)
                if self._stop_event.wait(self._backoff):
                    return
                self._backoff = min(self._backoff * 2.0, self.MAX_BACKOFF_SECONDS)

    def _run_session(self) -> None:
        """Open one connection, confirm the subscription, and poll it.

        Returns when the stop flag is set. Raises a Redis/OS error when the
        session fails, so ``_run`` can apply its backoff policy.
        """
        self._statuses.put(SubscriberStatus.CONNECTING)
        client: redis.Redis = redis.Redis.from_url(
            self._redis_url,
            decode_responses=True,
            socket_connect_timeout=self.SOCKET_TIMEOUT_SECONDS,
            socket_timeout=self.SOCKET_TIMEOUT_SECONDS,
        )
        pubsub = client.pubsub()
        try:
            pubsub.subscribe(self._channel)
            if not self._await_confirmation(pubsub):
                # stop() interrupted the confirmation wait.
                return
            self._backoff = self.INITIAL_BACKOFF_SECONDS
            self._statuses.put(SubscriberStatus.LIVE)
            self._listen(pubsub)
        finally:
            # Release Redis resources on every path, including failures.
            try:
                pubsub.unsubscribe(self._channel)
                pubsub.close()
            except redis.exceptions.RedisError:
                # The connection is already gone; closing the client below is
                # enough.
                self._logger.debug("Ignoring error while closing pub/sub object.")
            client.close()

    def _await_confirmation(self, pubsub: redis.client.PubSub) -> bool:
        """Wait for Redis to confirm the subscription.

        Args:
            pubsub: The pub/sub object the subscription was requested on.

        Returns:
            ``True`` once the confirmation arrived, ``False`` if ``stop()``
            was requested while waiting.

        Raises:
            TimeoutError: If Redis does not confirm within
                :attr:`SUBSCRIBE_CONFIRM_SECONDS`.
        """
        deadline: float = time.monotonic() + self.SUBSCRIBE_CONFIRM_SECONDS
        while not self._stop_event.is_set():
            message = pubsub.get_message(timeout=self.POLL_SECONDS)
            if message is not None and message.get("type") == "subscribe":
                return True
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Redis did not confirm subscription to '{self._channel}' "
                    f"within {self.SUBSCRIBE_CONFIRM_SECONDS} seconds."
                )
        return False

    def _listen(self, pubsub: redis.client.PubSub) -> None:
        """Poll the subscription until the stop flag is set or it errors.

        Args:
            pubsub: A confirmed subscription.
        """
        while not self._stop_event.is_set():
            message = pubsub.get_message(timeout=self.POLL_SECONDS)
            if message is None or message.get("type") != "message":
                # Subscription confirmations and keep-alive noise are skipped.
                continue
            self._handle_message(message)

    def _handle_message(self, message: Mapping[str, Any]) -> None:
        """Decode one pub/sub message and queue it when it is a move event.

        Malformed payloads are logged and skipped: one bad message must never
        tear down the subscription.

        Args:
            message: The raw ``redis-py`` message dictionary.
        """
        raw: Any = message.get("data")
        try:
            payload: Any = json.loads(raw)
        except (TypeError, ValueError):
            self._logger.warning(
                "Ignoring a move event for game %s that is not valid JSON.",
                self._game_id,
            )
            return
        if not isinstance(payload, Mapping):
            self._logger.warning(
                "Ignoring a move event for game %s with an unexpected payload.",
                self._game_id,
            )
            return
        try:
            event: MoveEvent | None = MoveEvent.from_dict(payload)
        except ValueError:
            self._logger.warning(
                "Ignoring a malformed move event for game %s.", self._game_id
            )
            return
        if event is not None:
            self._events.put(event)
