"""Search provider protocol definitions."""

from typing import Any, Protocol

from perspicacite.models.papers import Paper


class SearchProvider(Protocol):
    """Protocol for literature search providers."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def domains(self) -> list[str]:
        """Domain tags for routing. Use ['general'] to match all queries."""
        ...

    @property
    def tier(self) -> str:
        """Reliability tier: 'reliable' | 'external' | 'flaky'."""
        ...

    @property
    def retry(self) -> int:
        """Number of retry attempts after first failure (0 = fail fast)."""
        ...

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **kwargs: Any,
    ) -> list[Paper]: ...
