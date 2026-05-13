"""In-process job registry (SQLite + in-memory queues) for async ingestion."""
from perspicacite.jobs.registry import JobRegistry

__all__ = ["JobRegistry"]
