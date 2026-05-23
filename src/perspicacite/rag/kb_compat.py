"""Embedding-model compatibility check for KB creation/reuse.

When an ingest target KB already exists, the caller must use the SAME
embedding model the KB was originally built with. Otherwise the new
chunks are written with embeddings from a different model than the
existing chunks — and similarity scores become meaningless. The
multi-KB retrieval layer already enforces this at query time
(retrieval/multi_kb.py::check_embedding_compat) but ingest sites
didn't enforce it until 2026-05-16.

The three KB-creation sites that adopt this check:

* :func:`perspicacite.pipeline.search_to_kb._create_kb_if_missing`
* :func:`perspicacite.pipeline.asb.run_ingest._make_or_get_kb`
  (transitively via the search_to_kb helper).
* :func:`perspicacite.pipeline.github_kb._add_papers_to_kb`

The check is best-effort: when either the existing KB or the current
embedding service can't report its model name (e.g. test stubs),
the check silently skips rather than producing a spurious error.
"""
from __future__ import annotations

from typing import Any


class EmbeddingModelConflictError(ValueError):
    """Raised when an ingest target KB exists but was built with a
    different embedding model than the one currently configured.

    Inherits from :class:`ValueError` so callers that already
    `except ValueError` keep working; new call-sites can catch the
    narrower type and surface the three structured fields
    (``kb_name``, ``existing_model``, ``attempted_model``) to the
    operator.
    """

    def __init__(
        self,
        *,
        kb_name: str,
        existing_model: str,
        attempted_model: str,
    ) -> None:
        self.kb_name = kb_name
        self.existing_model = existing_model
        self.attempted_model = attempted_model
        super().__init__(
            f"KB {kb_name!r} was built with embedding model "
            f"{existing_model!r}; cannot re-ingest with "
            f"{attempted_model!r}. Either rebuild the KB or switch "
            f"the embedding model back."
        )


def check_embedding_compat_for_ingest(
    *,
    kb_meta: Any,
    embedding_service: Any,
) -> None:
    """Raise :class:`EmbeddingModelConflictError` if ``kb_meta`` exists
    and its ``embedding_model`` differs from the current service's
    ``model_name``.

    Parameters
    ----------
    kb_meta:
        :class:`~perspicacite.models.kb.KnowledgeBase` instance, or
        ``None`` if the KB does not exist yet. ``None`` is a no-op —
        the caller will create the KB next.
    embedding_service:
        The currently configured embedding service. Reads its
        ``model_name`` attribute. Missing attribute → no-op (best
        effort, supports test stubs that don't expose it).

    Notes
    -----
    Designed to be called right after ``session_store.get_kb_metadata``
    and BEFORE any ``vector_store.create_collection`` or
    ``save_kb_metadata`` calls — so a conflict raises cleanly without
    leaving the system in a partial state.
    """
    if kb_meta is None:
        return
    attempted = getattr(embedding_service, "model_name", None)
    existing = getattr(kb_meta, "embedding_model", None)
    if not attempted or not existing:
        # Best-effort: skip the check rather than fail on missing
        # fields (e.g. test stubs without model_name, or KBs from a
        # legacy schema where embedding_model wasn't captured).
        return
    if attempted != existing:
        raise EmbeddingModelConflictError(
            kb_name=kb_meta.name,
            existing_model=existing,
            attempted_model=attempted,
        )
