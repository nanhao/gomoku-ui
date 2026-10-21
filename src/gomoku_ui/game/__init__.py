"""Toolkit-agnostic client game state and orchestration.

``ClientGame`` owns the authoritative snapshot, the chosen side and the event
reconciliation rules. ``GameController`` wires services, state and the abstract
``GameView`` boundary. Nothing in this package imports a GUI toolkit.
"""
