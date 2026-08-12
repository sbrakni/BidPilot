"""Source adapters. Importing this package registers every built-in adapter family."""

from .base import (
    BaseAdapter,
    Cursor,
    LegalBasis,
    SourceAdapter,
    SourceConfig,
    content_hash,
    get_adapter,
    register,
    registered_adapters,
)
from .boamp import BoampAdapter
from .ted import TedAdapter

__all__ = [
    "BaseAdapter",
    "BoampAdapter",
    "Cursor",
    "LegalBasis",
    "SourceAdapter",
    "SourceConfig",
    "TedAdapter",
    "content_hash",
    "get_adapter",
    "register",
    "registered_adapters",
]
