# Gomoku UI — Design

This document describes how the desktop client is structured, how it stays
consistent with `gomoku-engine`, and which decisions are deliberately left open.

## 1. Goals and Constraints

Goals:

- Load an existing game by uuid and render its current position.
- Let a human play exactly one side (black or white).
- Show the opponent's moves as soon as the engine publishes them.
- Keep the engine as the single source of truth for rules, legality and outcome.

Constraints:

- The client must not implement Gomoku rules, win detection or move decisions.
- The client must not read or write the engine's `save/` directory; all state
  comes over HTTP and Redis.
- The engine API has no authentication, so the chosen side is enforced locally
  only.
- The GUI toolkit is Tkinter (`tkinter` + `tkinter.ttk`, standard library; see
  README "GUI Toolkit Selection"). Everything outside `gomoku_ui/ui` must stay
  toolkit-agnostic.

## 2. Layering

Dependency direction (no cycles, no lazy imports):

```text
src/scripts/play_game.py
        │
        ▼
gomoku_ui/ui ──implements──▶ GameView (gomoku_ui/game/game_view.py)
        │                                   ▲
        ▼                                   │
GameController / ClientGame ────────────────┘
        │
        ▼
gomoku_ui/services
        │
        ▼
gomoku_ui/models

gomoku_ui/config.py is used by the entry script and by the controller.
```

- `models` are pure dataclasses decoded from the engine's wire formats. They
  depend on nothing.
- `services` own all I/O: HTTP requests and the Redis subscription thread. They
  expose plain Python values and never touch the UI.
- `game` owns the client-side state machine and reconciliation rules. It knows
  nothing about a toolkit.
- `ui` draws widgets and translates clicks into controller calls. It is the only
  toolkit-specific layer.

## 3. Components

| Class | File | Responsibility |
| --- | --- | --- |
| `UiConfig` | `config.py` | Resolve side, engine URL and Redis URL from CLI arguments, environment variables and defaults, in that order |
| `GameSnapshot` | `models/game_snapshot.py` | Immutable decoded `SavedGame`: nested `config` and `state` (board, `current_player`, `winner`) plus the move history |
| `MakeMoveRequest` | `models/make_move_request.py` | Request body of `POST /moves`: 1-based `row` and `col` |
| `MoveRecord` | `models/move_record.py` | One played move: 1-based `row`, `col`, `player` |
| `MoveEvent` | `models/move_event.py` | Immutable decoded pub/sub payload: `move_number`, coordinates, players, `winner`, `game_over` |
| `EngineApiClient` | `services/engine_api_client.py` | `get_game()` and `make_move()` over HTTP with timeouts; converts non-2xx responses into `EngineApiError` |
| `MoveEventSubscriber` | `services/move_event_subscriber.py` | Dedicated thread: subscribe, confirm, decode events, report connection status, reconnect with backoff |
| `SubscriberStatus` | `services/subscriber_status.py` | Connection state reported to the controller: connecting, live, paused |
| `ClientGame` | `game/client_game.py` | Holds the snapshot and the chosen side; applies/deduplicates events; answers `is_my_turn`, `cell_at`, `last_move`, `is_game_over`; UX pre-checks |
| `EventOutcome` | `game/event_outcome.py` | Result of `apply_event`: applied, ignored, or gap (resync needed) |
| `GameView` | `game/game_view.py` | Abstract boundary the controller talks to: render snapshot, status, error, input enablement |
| `GameController` | `game/game_controller.py` | Startup sequence, background HTTP tasks, queue draining, resync, shutdown |
| `MainWindow` / `BoardView` / `DpiScaler` | `ui/` | Tkinter implementation of `GameView`: frame layout, canvas rendering, click mapping, per-monitor DPI awareness |
| `play_game.py` | `scripts/` | Argument parsing and process entry; the only module allowed global `main` code |

One class per module. Exception classes get their own modules as well (for
example `services/engine_api_error.py`).

## 4. Threading Model

Three kinds of threads exist:

1. **UI thread** — runs the toolkit main loop. Owns every widget, the controller
   and `ClientGame`.
2. **Subscriber thread** — one thread inside `MoveEventSubscriber`, blocking on
   Redis `get_message(timeout=0.5)`.
3. **Request worker** — a single-worker executor inside `GameController` used for
   `GET` and `POST`, so a slow engine never freezes the window. A single worker
   also prevents two moves from being submitted concurrently.

Cross-thread communication uses `queue.Queue` only:

- `MoveEventSubscriber.events: Queue[MoveEvent]` (owned by the subscriber)
- `MoveEventSubscriber.statuses: Queue[SubscriberStatus]` (owned by the subscriber)
- `GameController.snapshot_queue: Queue[GameSnapshot | EngineApiError]` for GET results
- `GameController.move_result_queue: Queue[GameSnapshot | EngineApiError]` for POST results

POST results are kept apart from GET results so a snapshot that arrives while a
move is still in flight cannot re-enable the board underneath that pending
move. Worker threads never call view methods. The UI thread drains the queues
on a short timer and updates the view. This is what makes the design portable
to any toolkit.

## 5. Startup Sequence

1. Entry script parses arguments, builds `UiConfig`, creates the view and the
   controller.
2. Controller starts the subscriber immediately, then submits the initial `GET`
   without waiting for Redis. Events that arrive before the snapshot are queued,
   not applied.
3. On each UI tick, the controller drains the **snapshot queue first**, then
   the **POST result queue**, the **status queue**, and finally the **event
   queue**.
4. Once the snapshot is installed, queued events are processed by
   `ClientGame.apply_event()`:
   - `move_number <= applied moves` → ignore (already reflected, or own echo),
   - `move_number == applied moves + 1` → apply,
   - `move_number > applied moves + 1` → gap → submit a resync `GET`.
5. The view renders the board, the side, the move count and a status line
   (`your turn`, `waiting for opponent`, `game over`, `live updates paused`).

Events are buffered until the snapshot is installed, and every successful
subscription confirmation triggers a resync `GET`. Together these rules close
the window between starting the subscription and loading the snapshot. In the
happy path the confirmation `GET` is redundant; it is kept because it removes
the startup race at the cost of one cheap request.

## 6. Making a Move

1. A click is converted to a 1-based `(row, col)` by the board view.
2. `ClientGame.pre_check(row, col)` rejects out-of-bounds, occupied points, a
   finished game and out-of-turn clicks purely for UX. The engine remains the
   authority.
3. The controller disables input and submits `POST /games/{id}/moves` on the
   worker.
4. On success the returned snapshot replaces the client state and is rendered
   immediately. The event echo of this move fails the `move_number <= applied`
   check and is ignored.
5. On `400` the detail is shown, the state resyncs via `GET`, and input is
   re-enabled if it is still the player's turn.
6. On a network error the board is left untouched, an error is shown, and a
   retry is possible.

## 7. Snapshot Reconciliation

`ClientGame.replace_snapshot(snapshot)` must handle snapshots that arrive late:

- if `len(snapshot.moves) < applied moves in the current state` the snapshot is
  **stale** and is discarded (an event for a newer move already advanced the
  board);
- otherwise it replaces the board, move count, `current_player` and `winner`.

`ClientGame.apply_event(event)` must handle:

- unknown event name → ignore;
- `game_id` mismatch → ignore (defensive: one channel per game, but cheap);
- duplicate/older `move_number` → ignore;
- next `move_number` → set the cell, append the move record, take
  `current_player = next_player` and `winner` from the event;
- future `move_number` → report a gap without mutating the board.

The client never derives `winner` from the board. A local five-in-a-row is never
computed.

## 8. Subscription Lifecycle

- `MoveEventSubscriber.start()` spawns a daemon thread and returns immediately.
- The thread connects, subscribes to `gomoku:games:{game_id}:events`, waits for
  the server's `subscribe` confirmation, then reports `CONNECTED`.
- Each Redis error is reported as `DISCONNECTED`, followed by exponential backoff
  (capped), then a fresh connection and a fresh confirmation.
- Every successful re-confirmation triggers a controller `GET`, because events
  published during the outage may be lost. The engine publishes best-effort and
  logs a warning when Redis is down; this resync is what hides that from the
  player.
- `stop()` sets a stop event, unsubscribes, closes the pub/sub object and joins
  the thread with a short timeout. It is called when the window closes.

No fallback polling is planned initially. If Redis never connects, the status
line says live updates are paused while the player's own moves still work over
REST. Polling can be added later behind the same controller boundary.

## 9. Error Policy

| Class | Raised for | Controller reaction |
| --- | --- | --- |
| `EngineApiError` | Non-2xx response, timeout, connection failure, malformed JSON | Show message; `404` at startup is fatal; `400` on move triggers resync |
| Redis connection errors | Redis unreachable or connection dropped | Status update only; automatic reconnect with backoff |

Terminal conditions:

- Unknown game id at startup → error dialog, then exit.
- Engine unreachable at startup → error with the attempted URL; retry is
  possible once the engine is up (a later implementation detail).
- Game over → input disabled, result shown, subscription kept until close.

## 10. Testing Strategy

- `MoveEvent`: decoding valid payloads, ignoring non-move events, invalid field
  handling, `game_over` passthrough.
- `UiConfig`: CLI > environment > default precedence, side aliases and invalid
  side rejection.
- `ClientGame`: duplicate events, out-of-order/gap detection, stale snapshot
  rejection, own-event echo, turn transitions, winner from engine data only,
  `pre_check` cases.
- `GameController`: startup ordering (snapshot before buffered events), POST
  success/failure, resync on gap and on Redis reconnection, shutdown idempotency.
  The engine client, subscriber and view are replaced by fakes; no live Redis or
  engine is needed.
- UI widget tests are not part of the strategy. `GameView` is a no-op fake in
  controller tests, and board geometry (click → cell) is written as a pure
  function in the view so it can be tested without opening a window.
- No test writes to the engine's `save/` directory.

## 11. Decisions and Open Questions

- **GUI toolkit — resolved: Tkinter** (`tkinter` + `tkinter.ttk`). Chosen for the
  zero install footprint, a canvas that covers the board, and a contained
  high-DPI workaround; PySide6 was rejected as a several-hundred-megabyte
  dependency with LGPLv3 obligations, pygame-ce because it ships no widgets.
  See README "GUI Toolkit Selection" for the comparison and the trigger to
  revisit.
- **Manual refresh affordance — resolved.** A `Refresh (F5)` `ttk` button and
  the F5 shortcut call `GameController.request_refresh()`, which resubmits the
  GET. It is the last resort when Redis is unavailable.
- **Visual polish.** The last-move marker and the hover preview are
  implemented as `Canvas` items. Numbered moves and animations remain optional
  and would need no controller change.
- **Spectator mode.** Not required; the same client with no side could observe
  read-only later.

## 12. Risks

- **No authentication.** Any client may submit a move for the player whose turn
  it is. Side enforcement is cooperative only.
- **Lost events.** Publishing is best-effort in the engine; a Redis outage can
  drop events. Gap detection plus resync on reconnect and after errors limits
  the damage, but a manual refresh remains the last resort.
- **Two channels.** HTTP responses and pub/sub events can arrive in any order;
  the `move_number` rules and stale-snapshot rejection are mandatory, not
  defensive extras.
- **Tk 8.6 high-DPI.** Tk does not declare DPI awareness by itself, so the board
  view has to opt in before the root window exists. Moving that call later
  silently brings back bitmap-stretched text and a mis-scaled board.
- **Engine compatibility.** The client mirrors the engine's wire formats. If the
  engine changes `SavedGame` or `MoveEvent`, `models` must be updated together
  with the README contract tables.
