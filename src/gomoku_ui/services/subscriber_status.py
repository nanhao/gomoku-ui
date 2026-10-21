"""Connection state of the Redis move event subscriber.

The subscriber runs on its own thread and cannot touch the view, so it reports
its state through a queue of :class:`SubscriberStatus` values. The controller
drains that queue on the UI thread and forwards the state to the window, which
is what turns a lost Redis connection into the visible "live updates paused"
message instead of a frozen board.
"""

import enum


class SubscriberStatus(enum.Enum):
    """How the live update channel is doing.

    Attributes:
        CONNECTING: The subscriber is dialling Redis or waiting for the
            server to confirm the subscription.
        LIVE: The subscription is confirmed; events published from now on
            will be delivered.
        PAUSED: The connection failed or dropped; the subscriber retries with
            exponential backoff while the player can still move over REST.
    """

    CONNECTING = "connecting"
    LIVE = "live"
    PAUSED = "paused"
