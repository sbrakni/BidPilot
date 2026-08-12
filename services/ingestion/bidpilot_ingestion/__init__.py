"""BidPilot sourcing & aggregation engine (SPEC F1 - the moat).

One canonical, deduplicated, always-fresh stream of every public tender relevant to our
users. Everything downstream - matching, decision, generation - consumes only this stream.
"""

__version__ = "0.1.0"
