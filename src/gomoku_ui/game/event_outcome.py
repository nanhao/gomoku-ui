"""Result of feeding one move event to the client-side state machine.

``ClientGame.apply_event`` never guesses: an event can advance the board, be
irrelevant (duplicate, own echo, foreign game), or reveal that the client
missed something. The controller needs that distinction to decide between a
redraw and a resync, so it is modelled explicitly instead of being encoded in
booleans or magic return values.
"""

import enum


class EventOutcome(enum.Enum):
    """What ``ClientGame.apply_event`` did with one event.

    Attributes:
        APPLIED: The event was the next move; board and turn advanced.
        IGNORED: The event was stale, duplicated, foreign, or arrived before
            a snapshot was installed; nothing changed.
        GAP: The event belongs to the future; the board was not mutated and
            the controller must reload the snapshot.
    """

    APPLIED = "applied"
    IGNORED = "ignored"
    GAP = "gap"
