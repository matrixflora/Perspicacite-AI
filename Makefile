# Perspicacite — convenience targets
# Run from the repo root.

.PHONY: ingest-kb test lint format

## ingest-kb: Ingest an ASB-Skill collection into Perspicacité.
##   Usage: make ingest-kb COLLECTION=/path/to/collection KB=asb-metabolomics-v1
##
##   Requires the Perspicacité server to be running:
##     uv run perspicacite -c config.yml serve
##
##   Plugin install hook investigation result (Plan 2c / Wave 2c):
##   Claude Code plugins do NOT support post-install lifecycle hooks.
##   The .claude-plugin/plugin.json manifest format supports name, description,
##   version, and allowed_tools fields — there is no post_install or on_install
##   hook mechanism as of 2026-05.  Plugin install (/plugin install) downloads
##   and registers the manifest but does not execute arbitrary shell commands or
##   trigger MCP tool calls on install.
##
##   Therefore: users must run this target manually after installing an
##   ASB-Skill collection plugin to ingest it into their local Perspicacité KB.
##
##   One-liner via MCP REST (if server exposes a direct call endpoint):
##     curl -s -X POST $(PERSP_URL)/api/mcp/call \
##       -H "Content-Type: application/json" \
##       -d '{"tool":"ingest_skill_bundle","args":{"source":"$(COLLECTION)",
##             "kb_name":"$(KB)","source_format":"asb-skill-collection-v1"}}'
##
##   Or from Claude Code (with MCP configured):
##     /mcp perspicacite ingest_skill_bundle source="$(COLLECTION)" \
##       kb_name="$(KB)" source_format="asb-skill-collection-v1"
COLLECTION ?= $(error Set COLLECTION= to the path of the ASB-Skill collection directory)
KB         ?= $(shell basename $(COLLECTION))
PERSP_URL  ?= http://localhost:8000

ingest-kb:
	@echo "Ingesting $(COLLECTION) into KB '$(KB)' ..."
	@echo "Ensure the Perspicacité server is running at $(PERSP_URL)"
	@echo ""
	@echo "Run this MCP tool call from Claude Code:"
	@echo "  ingest_skill_bundle(source='$(COLLECTION)', kb_name='$(KB)', source_format='asb-skill-collection-v1')"
	@echo ""
	@echo "Or via curl (requires server REST endpoint):"
	@echo "  curl -s -X POST $(PERSP_URL)/api/mcp/call -H 'Content-Type: application/json' \\"
	@echo "    -d '{\"tool\":\"ingest_skill_bundle\",\"args\":{\"source\":\"$(COLLECTION)\",\"kb_name\":\"$(KB)\",\"source_format\":\"asb-skill-collection-v1\"}}'"

test:
	uv run pytest tests/unit/ -v

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/
