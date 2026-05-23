# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed
- `extract_claims_from_passages` MCP tool: `domain: str | None` renamed to
  `domains: list[str] | None`. Pass a single domain as `["metabolomics"]`. Multiple
  domain IDs are resolved and composed into a `CompositeAdapter` so all adapters'
  context, qualifier, enrichment, and SHACL shapes are applied together.
- `generate_report` MCP tool: same `domain` → `domains` rename and composition logic.

### Added
- `domains` multi-adapter support for both claim-extraction MCP tools (see Changed above).
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
