"""MongoDB lifecycle and access boundary."""

from emercard.db.indexes import initialize_indexes
from emercard.db.lifecycle import Database

__all__ = ["Database", "initialize_indexes"]
