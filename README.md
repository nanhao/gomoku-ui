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
- GUI toolkit — **not selected yet**. A dedicated research step will compare
  candidates (for example Tkinter, PySide6, Pygame). The toolkit is isolated
  behind `gomoku_ui/ui`, so models, services and game state stay
  toolkit-agnostic.
- `pytest` — tests

## Directory Layout

Target layout (this scaffold contains the package directories and documentation;
the modules below are implemented in following steps):

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
│   │   │   ├── game_view.py         # abstract boundary the controller drives
│   │   │   └── game_controller.py   # orchestrates API, subscriber and view
│   │   └── ui/                      # toolkit-specific widgets (TBD)
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

## Usage

The entry point is a single Python script that takes the game id and the side to
play:

```powershell
python src/scripts/play_game.py <game_id> --side black
```

Two humans can play the same game with one client each:

```powershell
# Player 1
python src/scripts/play_game.py 3383d4f3-7d61-4799-88d5-d2592d80d7b6 --side black

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

Snapshot fields (`SavedGame`, board shortened for readability):

```json
{
  "game_id": "3383d4f3-7d61-4799-88d5-d2592d80d7b6",
  "board_size": 15,
  "rule_variant": "standard",
  "winner": 4,
  "current_player": 1,
  "board": [[0, 0, 0]],
  "moves": [{ "row": 8, "col": 8, "player": 1 }]
}
```

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

Scaffold only: directory layout, packaging, README and design document. The next
steps are the wire-format models, the engine API client, the Redis move event
subscriber, the client game state and controller, the GUI toolkit research and
board view, and the tests.
