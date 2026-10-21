# Gomoku UI

A desktop client for **gomoku-engine** that lets a human load an existing Gomoku
game, take one side, and play it on a graphical board.

`gomoku-ui` is a **client-side presentation layer**. It does not implement Gomoku
rules, does not decide moves, and does not persist games. All board state, move
legality and win detection belong to `gomoku-engine` (which delegates them to
`gomoku-core`). This project only:

- reads a game's full snapshot from the engine's REST API,
- draws the board and accepts the human player's moves,
- submits those moves back to the engine,
- follows every move of the game in real time over Redis pub/sub.

## Project Positioning

```text
+-------------------+      HTTP REST (load game / make move)      +------------------+
|    gomoku-ui      | ------------------------------------------> |  gomoku-engine   |
|    (this project) | <------------------------------------------ |  (REST + Redis)  |
|                   |      Redis pub/sub (move events)            |                  |
|  human plays one  | <------------------------------------------ |                  |
|  side on a board  |                                             +---------+--------+
+-------------------+                                                       |
                                                                    +-------v--------+
                                                                    |  gomoku-core   |
                                                                    | (rules engine) |
                                                                    +----------------+
```

- **gomoku-core** (external dependency of the engine): board representation,
  rule validation, win detection.
- **gomoku-engine** (backend): sessions, persistence, REST API, Redis pub/sub
  move events. It is the single source of truth for a game.
- **gomoku-ui** (this project): the human-facing client. It never re-implements
  rules and never writes save files.

## Features

- Load any existing game by its uuid; the full board and move history come from
  the engine.
- Play as black or white by clicking intersections on the board.
- Real-time opponent moves via Redis pub/sub, with duplicate suppression and gap
  detection.
- Read-only display with the final result once the game is over.
- Clear status reporting: your turn, waiting for the opponent, engine
  unreachable, live updates paused.
- Two clients can share one game (one as black, one as white), locally or across
  machines.

## Non-Goals

- No rules engine, no win detection, no local board validation beyond basic
  user-experience checks (see [Side and Turn Ownership](#side-and-turn-ownership)).
- No move decision making or AI.
- No game creation, save files or persistence in this project.
- No player accounts. The engine API is unauthenticated, so the chosen side is a
  client-side convention.
- No spectator mode yet (watching a game without taking a side).

## Tech Stack

- Python 3.11+
- `httpx` — REST calls to gomoku-engine
- `redis-py` 5.x — pub/sub subscription, pinned below 6 for the same reason as
  the engine (redis-py 6+ requires Redis ≥ 6.0, while Redis-on-Windows ships
  3.0.x)
- GUI toolkit — **Tkinter** (`tkinter` + `tkinter.ttk`, part of the standard
  library), selected after the comparison in
  [GUI Toolkit Selection](#gui-toolkit-selection). It adds no install step, its
  `Canvas` covers the board, and its one weakness (high-DPI) is handled inside
  `gomoku_ui/ui`. The toolkit stays isolated there, so models, services and game
  state remain toolkit-agnostic.
- `pytest` — tests

## GUI Toolkit Selection

The toolkit was the last open decision of the scaffold. Candidates were judged
against the constraints in [architecture/design.md](architecture/design.md):
a Windows desktop window, one board canvas with click hit-testing, readable at
125–200% display scaling, polling driven from the UI thread, no new build
toolchain, and everything outside `gomoku_ui/ui` staying toolkit-agnostic.

Versions evaluated in October 2026:

| Criterion | `tkinter` + `ttk` | PySide6 6.12 | pygame-ce 2.5.8 |
| --- | --- | --- | --- |
| Install footprint | none — bundled with CPython | several hundred MB of wheels | ~30 MB wheel |
| Board drawing | `Canvas` lines and ovals, no anti-aliasing | `QPainter` / `QGraphicsScene`, anti-aliased | SDL surfaces, AA only via `gfxdraw` |
| Click → `(row, col)` | own arithmetic on canvas pixels | built in (`itemAt`) | own arithmetic |
| High DPI | Tk 8.6 is DPI-blind until the process opts in | native per-monitor handling | handled by SDL2 |
| Widgets | `ttk` status line, buttons, message boxes | full Qt widget set | none — draw everything |
| UI-thread polling | `root.after()` | `QTimer` | an explicit game-loop tick |
| UI tests | awkward, but board logic stays outside the widget | `pytest-qt` + `QTest` | weakest of the three |
| Packaging | small; Tcl/Tk licence is BSD-style | large; LGPLv3 obligations | medium; LGPL 2.1 |
| Main risk | dated look, aliased strokes | heavy dependency for one window | rebuilding a widget toolkit |

**Decision: `tkinter` with `tkinter.ttk`.**

- This client needs a canvas, a status line and two dialogs. Tk provides all
  three without a dependency, so `pip install -e .` stays at two libraries
  (`httpx`, `redis`) and a frozen build stays small.
- The hard part of this project is not widget chrome: it is the two-channel
  reconciliation described below, and that code is toolkit-agnostic by design.
  A heavier toolkit would not remove any of it.
- Canvas item coordinates are window pixels relative to the widget, so
  "click → 1-based `(row, col)`" is arithmetic over a cell size and an origin.
  It stays a pure function that is tested without opening a window.
- Tk's single real weakness on Windows is display scaling, and it has a
  contained fix (next subsection).

### High-DPI handling

Tk 8.6 — the Tk bundled with Python 3.11 to 3.13, and confirmed locally on
Python 3.13.3 — does not declare DPI awareness, so Windows bitmap-stretches the
whole window at 125–200% scaling. The board view therefore:

- requests per-monitor DPI awareness before `tkinter.Tk()` exists (after that
  point the process setting is locked), falling back to the older system-wide
  call when the newer API is unavailable;
- reads the resulting scale once and derives everything from it: cell size,
  stone radius, line width, fonts and the initial window geometry — no widget
  hard-codes a pixel size;
- redraws on `<Configure>`, so a resize or a move to another monitor keeps the
  geometry consistent.

Python 3.14 moved the Windows builds to Tcl/Tk 9.0, which improves DPI support,
but tkinter regressions are still being reported against it (for example a
missing `TkDefaultFont` on 3.14.7). The client uses no Tk 9 feature, so the
workaround above is applied on every version.

Grid lines are horizontal and vertical, so the lack of anti-aliasing does not
show on them; stones are the only place where it does. If that matters, stones
can be drawn from pre-rendered anti-aliased PNGs through `PhotoImage` instead of
`create_oval`, with no change outside the view.

### Rejected alternatives

- **PySide6 6.12** — the best rendering and DPI story of the three, but several
  hundred megabytes and an LGPLv3 relink obligation for a window that contains
  one grid. Revisit if the client grows a move list, animations or theming;
  `GameView` and the controller's queue polling are all that would change.
- **pygame-ce 2.5.8** — the right tool for a sprite-and-animation game, wrong
  for a form-shaped client: status line, buttons, dialogs and text layout would
  all be hand-rolled, and UI testing gets harder. Revisit if the board becomes
  animated or sound-driven.
- **PyQt6** — the same Qt 6 API under GPL/commercial licensing, so it adds
  nothing over PySide6 here.
- **ttkbootstrap / customtkinter** — restyle `ttk` but fix neither canvas
  anti-aliasing nor DPI, and turn a standard-library toolkit into a dependency.

### Where the toolkit appears

Only `gomoku_ui/ui` imports `tkinter`:

- `DpiScaler` (`ui/dpi_scaler.py`) — declares DPI awareness before the root
  window exists and exposes the scale factor the other widgets multiply by.
- `BoardView` (`ui/board_view.py`) — the canvas: grid, stones, last-move marker,
  hover preview and click mapping.
- `MainWindow` (`ui/main_window.py`) — `ttk` frame layout, status line, side and
  move count, error banner, refresh action.

The controller keeps draining its queues on a `root.after(...)` tick, so the
threading model in the design document is unchanged. `pyproject.toml` needs no
new dependency: `tkinter` ships with CPython.

## Directory Layout

Target layout (all modules below are implemented):

```text
gomoku-ui/
├── README.md
├── pyproject.toml
├── .gitignore
├── architecture/
│   └── design.md                    # architecture, threading, reconciliation rules
├── src/
│   ├── gomoku_ui/
│   │   ├── __init__.py
│   │   ├── config.py                # UiConfig: resolved endpoints and side
│   │   ├── models/                  # DTOs mirroring the engine wire formats
│   │   │   ├── game_snapshot.py     # GameSnapshot: decoded GET response
│   │   │   ├── make_move_request.py # MakeMoveRequest: POST /moves body
│   │   │   ├── move_record.py       # MoveRecord: one played move
│   │   │   └── move_event.py        # MoveEvent: decoded pub/sub payload
│   │   ├── services/                # I/O boundaries
│   │   │   ├── engine_api_client.py # REST: load a game, make a move
│   │   │   ├── engine_api_error.py  # non-2xx / network failures
│   │   │   ├── move_event_subscriber.py
│   │   │   │                        # Redis pub/sub on a background thread
│   │   │   └── subscriber_status.py # connecting / live / paused
│   │   ├── game/                    # toolkit-agnostic client state
│   │   │   ├── client_game.py       # board + side + event reconciliation
│   │   │   ├── event_outcome.py     # applied / ignored / gap result
│   │   │   ├── game_view.py         # abstract boundary the controller drives
│   │   │   └── game_controller.py   # orchestrates API, subscriber and view
│   │   └── ui/                      # tkinter widgets (the only toolkit-aware layer)
│   │       ├── dpi_scaler.py        # per-monitor DPI awareness + scale factor
│   │       ├── board_view.py        # canvas: grid, stones, click mapping
│   │       └── main_window.py       # frame layout, status line, dialogs
│   └── scripts/
│       └── play_game.py             # entry point
└── tests/
```

## Prerequisites

1. **gomoku-engine is running** with its REST API reachable
   (by default `http://127.0.0.1:8000`). From the engine project:

   ```powershell
   cd d:\projects\gomoku-engine
   .\start.ps1
   ```

   `start.ps1` starts Redis (if needed) and the API server together.

2. **Redis is reachable** at the URL the engine publishes to
   (`redis://localhost:6379/0` by default). The client must subscribe to the
   same Redis instance the engine publishes on, otherwise no move events arrive.

3. **An existing game id** (uuid). Create one with the engine, for example:

   ```powershell
   cd d:\projects\gomoku-engine
   .\.venv\Scripts\python.exe src/scripts/start_game.py --board-size 15
   ```

   The output JSON contains the new `game_id`.

## Installation

```powershell
cd d:\projects\gomoku-ui
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The client package must be installed (editable is enough) because the entry
script imports `gomoku_ui`. Activating the venv first ensures `python` resolves
to the project interpreter.

Use the python.org installer for the interpreter: it bundles Tkinter and
Tcl/Tk. The Microsoft Store build and some minimal distributions ship without
`_tkinter`, and the window cannot open on those.

## Usage

The entry point is a single Python script that takes the game id and the side to
play:

```powershell
python src/scripts/play_game.py <game_id> --side black
```

Two humans can play the same game with one client each:

```powershell
# Player 1
python src/scripts/play_game.py 7438fb33-d39c-4d07-924f-f19bb97baed2 --side black

# Player 2 (same machine or another machine pointed at the engine host)
python src/scripts/play_game.py 3383d4f3-7d61-4799-88d5-d2592d80d7b6 --side white
```

For a remote opponent, point `--engine-url` and `--redis-url` at hosts that are
reachable from their machine (the engine binds `0.0.0.0` by default; Redis
usually needs an explicit bind configuration).

Both clients subscribe to the game's Redis channel, so each one sees every move.
Each client only accepts clicks for its own side; the other side is played by
the other client (or by the engine's CLI/API).

### CLI reference

| Argument | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `game_id` | `str` | Yes | — | uuid of the existing game to load |
| `--side` | `str` | Yes | — | Side to play: `black`/`b`/`1` or `white`/`w`/`2` (case-insensitive) |
| `--engine-url` | `str` | No | `$GOMOKU_ENGINE_URL`, else `http://127.0.0.1:8000` | Base URL of the engine REST API |
| `--redis-url` | `str` | No | `$REDIS_URL`, else `redis://localhost:6379/0` | Redis connection URL used for pub/sub |
| `--help` | — | No | — | Argument details |

Precedence: command-line argument > environment variable > built-in default.

### Configuration

| Setting | CLI flag | Environment variable | Default |
| --- | --- | --- | --- |
| Engine API base URL | `--engine-url` | `GOMOKU_ENGINE_URL` | `http://127.0.0.1:8000` |
| Redis URL | `--redis-url` | `REDIS_URL` | `redis://localhost:6379/0` |

`REDIS_URL` is the same variable the engine uses, so pointing both processes at
one Redis only requires setting it once in the environment.

## How a Turn Flows

```text
human clicks an intersection
   └─> local UX pre-check (my turn? empty point? game running?)
        └─> POST /api/v1/games/{game_id}/moves  { "row": r, "col": c }
             ├─ engine validates with gomoku-core and saves the move
             ├─ engine publishes a move event on Redis
             └─ engine returns the updated game snapshot
                  └─> client renders that snapshot immediately

opponent makes a move
   └─> move event published on gomoku:games:{game_id}:events
        └─> subscriber thread decodes it and hands it to the UI thread
             └─> applied when move_number == applied moves + 1
                  └─> otherwise a gap is detected and the snapshot is reloaded
```

## State Reconciliation

The client consumes two independent channels (HTTP responses and pub/sub
events), so their order is not guaranteed. These rules keep the displayed board
consistent with the engine:

1. **The GET snapshot is authoritative.** It carries the board, the full move
   history, `current_player` and `winner`.
2. **Events are applied only when they are the next move**
   (`move_number == applied moves + 1`).
3. **Earlier or duplicate events are ignored** — in particular the echo of your
   own move, which is already reflected in the POST response.
4. **A gap never guesses.** If `move_number` jumps ahead, the client reloads the
   snapshot with GET and continues from there.
5. **Failed requests resync.** After a rejected move (`400`) the client shows the
   engine's error and reloads the snapshot; a missing game (`404`) names the game
   id and, at startup, ends the session. On a network/timeout error the board
   stays untouched until a retry succeeds.
6. **Every confirmed subscription resyncs.** Moves published before the
   subscription was confirmed may be missing, so each successful (re)subscribe
   is followed by a GET — including the first one at startup.
7. **The winner always comes from the engine.** The client never computes a
   five-in-a-row locally; it reads `winner` from the snapshot or event.

On startup the client subscribes first, then loads the snapshot. Events arriving
before the snapshot are buffered; once the snapshot is in place they are
deduplicated or applied by the rules above.

## Engine Contract

### REST API

| Method | Path | Purpose | Responses |
| --- | --- | --- | --- |
| `GET` | `/api/v1/games/{game_id}` | Load all information about the game | `200` snapshot; `404` unknown game |
| `POST` | `/api/v1/games/{game_id}/moves` | Make a move, body `{"row": 1, "col": 1}` | `200` updated snapshot; `400` illegal move; `404` unknown game |
| `GET` | `/api/v1/health` | Liveness probe exposed by the engine | `200` |

Snapshot fields (`SavedGame` as serialised by the engine, board shortened for
readability):

```json
{
  "game_id": "3383d4f3-7d61-4799-88d5-d2592d80d7b6",
  "config": { "board_size": 15, "rule_variant": "standard" },
  "state": {
    "board": [[0, 0, 0]],
    "winner": 4,
    "current_player": 1
  },
  "moves": [{ "row": 8, "col": 8, "player": 1 }]
}
```

The response wraps the board and the turn/outcome in `config` and `state`
objects, matching the engine's `SavedGame` dataclass; `GameSnapshot.from_dict`
decodes exactly this shape.

### Redis pub/sub

| Redis key | Kind | Purpose |
| --- | --- | --- |
| `gomoku:games:{game_id}:events` | pub/sub channel | Live broadcast of every move of one game |
| `gomoku:games:{game_id}:history` | list | Temporary copy of the same events; expires one minute after the game is over |

Event payload:

```json
{
  "event": "move",
  "game_id": "3383d4f3-7d61-4799-88d5-d2592d80d7b6",
  "move_number": 1,
  "row": 8,
  "col": 8,
  "player": 1,
  "next_player": 2,
  "winner": 4,
  "game_over": false
}
```

The client subscribes to the channel only. The history list is not needed for
recovery because the GET snapshot already contains the full board and move
history.

### Value codes

| Value | Meaning |
| --- | --- |
| board cell `0` | Empty intersection |
| board cell `1` / `2` | Black / white stone |
| `current_player`, `player`, `next_player` `1` / `2` | Black / white |
| `winner` `1` / `2` / `3` / `4` | Black wins / white wins / tie / still running |

All coordinates are **1-based** `(row, col)`, matching `gomoku-core` and the
engine. The engine returns `400` with a human-readable `detail` for illegal
moves (out of bounds, occupied point, or a finished game).

## Side and Turn Ownership

The engine has no player identity: any caller may submit a move for whoever's
turn it is. The client therefore enforces the chosen side locally:

- clicks are accepted only while `current_player` equals your `--side` and
  `winner` is `4`;
- after your move is submitted, input stays disabled until the opponent's move
  arrives (or a resync shows it is your turn again);
- if you join mid-game and it is not your turn, the board opens in read-only
  "waiting for opponent" mode;
- if the game is already over, the board opens read-only with the final result.

This is a cooperative convention, not an access-control mechanism. Two clients
on the same machine both receive every event; each one only *plays* its own
side.

## Failure Handling

| Situation | Client behaviour |
| --- | --- |
| Engine unreachable at startup | Clear error with the attempted URL; no board is shown |
| Game id unknown (`404`) | Error naming the game id; client exits |
| Illegal move (`400`) | Shows the engine's message, reloads the snapshot, keeps the window open |
| Redis unreachable | Board still loads and your moves still work over REST; status shows "live updates paused" and the subscriber retries with backoff |
| Redis reconnects | Reloads the snapshot to close any gap, then resumes live updates |
| Missed or out-of-order event | Detected by `move_number`; snapshot reload instead of guessing |
| Game over | Input disabled; final result displayed; subscription kept until the window closes |
| Window closed | Subscriber stopped and unsubscribed before exit |

## Development

- **Rules ownership.** The UI must not implement Gomoku rules. Local checks are
  limited to user-experience concerns (bounds, occupied point, your turn, game
  over); the engine remains the authority.
- **Conventions** (shared with `gomoku-core` and `gomoku-engine`): one class per
  module, file named after the snake_case class name, entry scripts exempt; no
  global functions or variables; dataclasses for structured data; full type
  annotations; rich docstrings; no file above 500 lines; no duplicated logic.
- **UI code.** Only `gomoku_ui/ui` may import `tkinter`. Board geometry (cell
  size, origin, pixel-to-cell conversion) is computed in the view from the DPI
  scale and handed to the controller as a 1-based `(row, col)`; nothing outside
  `ui` knows about pixels.
- **Tests.** Run `python -m pytest`. Tests use fake engine/subscriber boundaries
  (no live Redis or engine needed) and must not touch real game data.
- **Design.** See [architecture/design.md](architecture/design.md) for the
  layering, threading model, reconciliation algorithm and open decisions.
- **Dependencies.** The client does not depend on `gomoku-core`; it only consumes
  the engine's wire formats.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `404` when the window opens | Wrong game id, or the engine has no save for it | Check the uuid and the engine's `save/` directory |
| Connection refused / timeout | Engine not running or wrong `--engine-url` | Start the engine (`.\start.ps1`) and verify `GET /api/v1/health` |
| Your moves work, the opponent's never appear | Client and engine use different Redis instances | Align `REDIS_URL` / `--redis-url` with the engine's |
| Move rejected by the engine | Not your turn, occupied point, or game over | The status bar explains; the board resyncs |
| Clicking does nothing | Not your side's turn, or the game is over | Check the status bar |
| Board shows an outdated position | Missed event while Redis was down | Reconnect happens automatically; a manual refresh will be available |

## Status

Implemented: wire-format models, the engine REST client, the Redis move event
subscriber with reconnect backoff, the client game state and controller, the
Tkinter board view and window, and the entry script. Automated tests are not
included yet; the testing strategy in
[architecture/design.md](architecture/design.md) still describes the suite the
project intends to have.
