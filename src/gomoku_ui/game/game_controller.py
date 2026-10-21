"""Orchestration of engine, subscriber, client state and the abstract view.

``GameController`` is the only place where the two data channels (HTTP
responses and Redis events) meet. It owns a single-worker executor so a slow
or unreachable engine never freezes the window and two moves can never be
submitted concurrently, and it moves every result across the thread boundary
through a ``queue.Queue``. All controller methods run on the UI thread; worker
threads only put values into the queues.

Startup order matters: the subscriber is started first, then the initial
snapshot is requested. Events that arrive before the snapshot are left in
their queue, and every confirmed (re)subscription triggers a fresh snapshot
request, which together close the race between subscribing and loading.
"""

from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
from typing import Final

from gomoku_ui.config import UiConfig
from gomoku_ui.game.client_game import ClientGame
from gomoku_ui.game.event_outcome import EventOutcome
from gomoku_ui.game.game_view import GameView
from gomoku_ui.models.game_snapshot import GameSnapshot
from gomoku_ui.models.move_event import MoveEvent
from gomoku_ui.services.engine_api_client import EngineApiClient
from gomoku_ui.services.engine_api_error import EngineApiError
from gomoku_ui.services.move_event_subscriber import MoveEventSubscriber
from gomoku_ui.services.subscriber_status import SubscriberStatus


class GameController:
    """Wires services, client state and the view together for one session.

    Attributes:
        config: Resolved session configuration (game id, side, endpoints).
    """

    # How often the UI thread drains the worker queues, in milliseconds.
    # Fast enough to feel instant, slow enough to cost nothing.
    TICK_INTERVAL_MS: Final[int] = 100

    def __init__(
        self,
        config: UiConfig,
        view: GameView,
        engine_client: EngineApiClient,
        subscriber: MoveEventSubscriber,
    ) -> None:
        """Create a controller; nothing starts until :meth:`start`.

        Args:
            config: Resolved session configuration.
            view: The window implementation (or a fake) to drive.
            engine_client: REST boundary for snapshots and moves.
            subscriber: Redis move event subscriber, already built for the
                same game. Its queues are drained by this controller.
        """
        self._config: UiConfig = config
        self._view: GameView = view
        self._engine: EngineApiClient = engine_client
        self._subscriber: MoveEventSubscriber = subscriber
        self._game: ClientGame = ClientGame(config.game_id, config.side)

        # Results of GET requests and of POST requests are kept apart: a GET
        # result may arrive while a move is still in flight, and must not
        # re-enable the board underneath that pending move.
        self._snapshot_queue: Queue[GameSnapshot | EngineApiError] = Queue()
        self._move_result_queue: Queue[GameSnapshot | EngineApiError] = Queue()

        # One worker serialises every HTTP request. The thread is not a daemon,
        # but shutdown() cancels queued work and requests are time-bounded.
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="engine-request",
        )

        self._subscription_status: SubscriberStatus = SubscriberStatus.CONNECTING
        self._move_in_flight: bool = False
        # Move number of the earliest unresolved gap. While it is set, another
        # resync is never requested: a queued burst of events, or a snapshot
        # that still leaves the board behind, must not trigger a GET on every
        # tick. Cleared once a snapshot makes the gap applicable or stale.
        self._resync_target: int | None = None
        self._started: bool = False
        self._stopped: bool = False

    @property
    def config(self) -> UiConfig:
        """Return the resolved session configuration."""
        return self._config

    @property
    def client_game(self) -> ClientGame:
        """Return the client state (exposed for diagnostics and tests)."""
        return self._game

    def start(self) -> None:
        """Start the subscriber and load the initial snapshot.

        Matches the design's startup sequence: Redis first, HTTP second. The
        GET does not wait for Redis, and buffered events stay queued until a
        snapshot is installed. Calling twice is a no-op.
        """
        if self._started or self._stopped:
            return
        self._started = True
        self._view.set_status(self._game.status_text())
        self._view.set_subscription_status(self._subscription_status)
        self._subscriber.start()
        self._request_snapshot()

    def tick(self) -> None:
        """Drain the worker queues and update the view.

        Called by the window on every UI timer tick. Snapshots are processed
        first, then move results, subscription status and finally events;
        events are only touched once a snapshot is installed, which is how
        events that arrive during startup stay buffered.
        """
        if self._stopped:
            return
        self._drain_snapshot_results()
        self._drain_move_results()
        self._drain_status_updates()
        if self._game.has_snapshot:
            self._drain_events()

    def handle_click(self, row: int, col: int) -> None:
        """React to a board click on the UI thread.

        Args:
            row: 1-based row of the clicked intersection.
            col: 1-based column of the clicked intersection.
        """
        if self._stopped or self._move_in_flight:
            return
        reason: str | None = self._game.pre_check(row, col)
        if reason is not None:
            self._view.set_error(reason)
            return
        self._view.set_error(None)
        self._move_in_flight = True
        self._view.set_input_enabled(False)
        self._executor.submit(self._run_move_request, row, col)

    def request_refresh(self) -> None:
        """Reload the authoritative snapshot (manual refresh action)."""
        self._request_snapshot()

    def shutdown(self) -> None:
        """Stop the subscriber, cancel pending work and release the client.

        Idempotent: the window's close handler and the fatal-startup path may
        both reach it.
        """
        if self._stopped:
            return
        self._stopped = True
        self._subscriber.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._engine.close()

    def _request_snapshot(self) -> None:
        """Queue a GET on the worker without blocking the UI thread."""
        if self._stopped:
            return
        self._executor.submit(self._run_snapshot_request)

    def _request_resync(self, event: MoveEvent) -> None:
        """Reload the snapshot once for the earliest detected gap.

        The gap event stays in the subscriber queue until a snapshot resolves
        it, so without this guard every UI tick would submit another GET.

        Args:
            event: The first event that was ahead of the board.
        """
        if self._resync_target is not None:
            return
        self._resync_target = event.move_number
        self._request_snapshot()

    def _run_snapshot_request(self) -> None:
        """Worker task: fetch one snapshot and post it to the UI thread."""
        if self._stopped:
            return
        try:
            snapshot: GameSnapshot = self._engine.get_game(self._config.game_id)
        except EngineApiError as error:
            if not self._stopped:
                self._snapshot_queue.put(error)
            return
        except RuntimeError:
            # The HTTP client was closed by shutdown while this request was
            # starting; the result is no longer needed.
            return
        if not self._stopped:
            self._snapshot_queue.put(snapshot)

    def _run_move_request(self, row: int, col: int) -> None:
        """Worker task: submit one move and post the result.

        Args:
            row: 1-based row of the stone.
            col: 1-based column of the stone.
        """
        if self._stopped:
            return
        try:
            snapshot: GameSnapshot = self._engine.make_move(
                self._config.game_id, row, col
            )
        except EngineApiError as error:
            if not self._stopped:
                self._move_result_queue.put(error)
            return
        except RuntimeError:
            # Client closed during shutdown; discard the result.
            return
        if not self._stopped:
            self._move_result_queue.put(snapshot)

    def _drain_snapshot_results(self) -> None:
        """Process every completed GET result, oldest first."""
        while True:
            try:
                result = self._snapshot_queue.get_nowait()
            except Empty:
                return
            if isinstance(result, EngineApiError):
                self._handle_snapshot_error(result)
            else:
                self._handle_snapshot(result)
            if self._stopped:
                return

    def _drain_move_results(self) -> None:
        """Process every completed POST result, oldest first."""
        while True:
            try:
                result = self._move_result_queue.get_nowait()
            except Empty:
                return
            self._move_in_flight = False
            if isinstance(result, EngineApiError):
                self._handle_move_error(result)
            else:
                self._handle_move_success(result)
            if self._stopped:
                return

    def _drain_status_updates(self) -> None:
        """Forward every subscriber state change to the view."""
        while True:
            try:
                status = self._subscriber.statuses.get_nowait()
            except Empty:
                return
            self._handle_subscription_status(status)

    def _drain_events(self) -> None:
        """Apply queued move events in arrival order until a gap is seen."""
        while True:
            try:
                event = self._subscriber.events.get_nowait()
            except Empty:
                return
            if not self._handle_event(event):
                # Leave the remaining events queued: the snapshot that is on
                # its way will make them stale or applicable in order.
                return

    def _handle_snapshot(self, snapshot: GameSnapshot) -> None:
        """Install a GET snapshot unless it is stale.

        Args:
            snapshot: The snapshot returned by the engine.
        """
        if self._game.replace_snapshot(snapshot):
            self._clear_resync_if_resolved(snapshot)
            self._view.set_error(None)
        self._refresh_view()

    def _handle_snapshot_error(self, error: EngineApiError) -> None:
        """React to a failed GET: fatal for an unknown game, else retryable.

        Args:
            error: The failure raised by the engine client.
        """
        if not self._game.has_snapshot and error.is_not_found:
            self._view.show_fatal_error(
                f"Game {self._config.game_id} was not found on the engine at "
                f"{self._config.engine_url}. Check the uuid and that the "
                "engine is running."
            )
            self.shutdown()
            return
        self._view.set_error(str(error))
        if not self._game.has_snapshot:
            self._view.set_status("Could not load the game yet. Use Refresh to retry.")

    def _handle_move_success(self, snapshot: GameSnapshot) -> None:
        """Render the snapshot returned by a successful POST.

        Args:
            snapshot: The updated state returned by the engine.
        """
        if self._game.replace_snapshot(snapshot):
            # The engine accepted the move; no error remains relevant.
            self._clear_resync_if_resolved(snapshot)
            self._view.set_error(None)
        self._refresh_view()

    def _handle_move_error(self, error: EngineApiError) -> None:
        """React to a rejected or failed POST.

        An illegal move (``400``) or vanished game (``404``) is resolved by
        reloading the snapshot; a transport failure leaves the board untouched
        so the player can simply try again.

        Args:
            error: The failure raised by the engine client.
        """
        self._view.set_error(str(error))
        if error.is_bad_request or error.is_not_found:
            self._request_snapshot()
        else:
            self._refresh_view()

    def _handle_subscription_status(self, status: SubscriberStatus) -> None:
        """Show the new live-update state and resync when it becomes live.

        Args:
            status: The subscriber's latest state.
        """
        self._subscription_status = status
        self._view.set_subscription_status(status)
        if status is SubscriberStatus.LIVE:
            # Moves published while we were disconnected may be lost, so a
            # confirmed subscription always triggers a fresh snapshot.
            self._request_snapshot()

    def _handle_event(self, event: MoveEvent) -> bool:
        """Apply one move event or request a resync on a gap.

        Args:
            event: The decoded event drained from the subscriber queue.

        Returns:
            ``True`` when draining may continue, ``False`` when the event
            revealed a gap and a snapshot reload is required first.
        """
        outcome: EventOutcome = self._game.apply_event(event)
        if outcome is EventOutcome.GAP:
            self._request_resync(event)
            return False
        if outcome is EventOutcome.APPLIED:
            self._refresh_view()
        return True

    def _clear_resync_if_resolved(self, snapshot: GameSnapshot) -> None:
        """Drop a pending gap once a snapshot makes it applicable or stale.

        A snapshot with ``move_count == target - 1`` makes the queued event
        the next move; a snapshot at or beyond ``target`` makes it stale.
        Either way the next event drain resolves it, so the guard can go.

        Args:
            snapshot: A snapshot that was accepted as the current state.
        """
        if (
            self._resync_target is not None
            and snapshot.move_count >= self._resync_target - 1
        ):
            self._resync_target = None

    def _refresh_view(self) -> None:
        """Re-render the current state and derive the input enablement."""
        snapshot: GameSnapshot | None = self._game.snapshot
        if snapshot is None:
            return
        self._view.render_snapshot(snapshot, self._game.side_label)
        self._view.set_status(self._game.status_text())
        self._view.set_input_enabled(
            self._game.is_my_turn() and not self._move_in_flight
        )
