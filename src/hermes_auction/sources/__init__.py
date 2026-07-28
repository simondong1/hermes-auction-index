"""Auction house adapters.

Importing this package registers every adapter. Adding a house means adding a module
here and importing it below - the pipeline discovers it through the registry.
"""

from hermes_auction.sources import (  # noqa: F401
    artcurial,
    bonhams,
    christies,
    poly_hk,
    sothebys,
)
from hermes_auction.sources.base import AuctionSource, build, register, registry

__all__ = ["AuctionSource", "build", "register", "registry"]
