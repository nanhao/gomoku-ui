"""I/O boundaries: the engine REST client and the Redis move event subscriber.

Services translate network traffic into plain Python values and never touch the
UI. They are the only modules that know about HTTP, Redis channels or threads.
"""
