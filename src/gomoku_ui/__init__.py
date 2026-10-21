"""Gomoku UI: a desktop client for the gomoku-engine REST + Redis API.

The client loads an existing game, lets a human play one side, and follows the
opponent's moves over Redis pub/sub. Board rules, move legality and the outcome
stay owned by gomoku-engine and gomoku-core; this package only presents and
transports state.
"""
