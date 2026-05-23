# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- `domain` parameter on the `extract_claims_from_passages` MCP tool. When provided, the
  corresponding `indicium-adapters` domain adapter is resolved and wired through the full
  pipeline: LLM context enrichment, qualifier acceptance, ontology-term annotation, and
  domain-specific SHACL validation.
- `domain` parameter on the `generate_report` MCP tool — the `extract_claims=True` path now
  supports the same adapter-aware extraction and validation as `extract_claims_from_passages`.
- `claims_to_graph()` now serializes the `ontology_terms` dict from enriched claims as
  `asb:{slot}_ontology_term` RDF literals, enabling SHACL property-shape validation on
  ontology identifiers.

### Fixed
- `domain_adapter` is now correctly passed into `extract_claims()` (not applied as a manual
  post-processing loop), enabling LLM context enrichment and domain qualifier acceptance during
  extraction rather than only after.
- `claims_to_graph()` no longer serializes `None` ontology term values as the literal string
  `"None"`; `None`/falsy values are silently skipped.

---

## [2.0.0] - 2026-05-15

Initial public release of the Perspicacite 2.x series with the redesigned MCP server,
multi-repo knowledge-base support, and the `indicium` claim/evidence standard integration.
