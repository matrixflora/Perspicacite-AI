"""Sample QC module for the sample bundle fixture.

Exercises the python docstring extractor path of the chunk producer.
"""


def filter_low_count_cells(adata, min_counts: int = 500):
    """Drop cells whose total count falls below ``min_counts``."""
    return adata[adata.obs["total_counts"] >= min_counts]


def compute_qc_summary(adata):
    """Return a dict summarising QC metrics for ``adata``."""
    return {
        "n_obs": int(adata.n_obs),
        "n_vars": int(adata.n_vars),
    }
