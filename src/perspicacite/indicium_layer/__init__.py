"""Indicium claim-graph storage and build pipeline for Perspicacité.

This package adds the per-KB claim graph layer (oxigraph-backed in
production, rdflib in tests) that powers `RAGMode.REASONING`. The indicium
package itself stays oxigraph-free; this layer is the Perspicacité-side
adapter that wraps it.
"""
