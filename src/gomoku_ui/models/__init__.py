"""Wire-format models mirroring the gomoku-engine payloads.

Every model is an immutable dataclass decoded from either a REST response or a
Redis pub/sub message. Models depend on nothing else in the package, so the
transport and presentation layers can share them without cycles.
"""
