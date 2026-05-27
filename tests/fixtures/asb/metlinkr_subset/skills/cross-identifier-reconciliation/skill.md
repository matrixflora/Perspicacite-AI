---
description: >
  Cross-identifier reconciliation of metabolite IDs using MetLinkR.
  Maps between RefMet, HMDB, ChEBI, PubChem, and other metabolomics databases.
edam_operation: http://edamontology.org/operation_0336
edam_topics:
  - http://edamontology.org/topic_3172
---

## Overview

Cross-identifier reconciliation resolves the same metabolite across
heterogeneous databases (RefMet, HMDB, ChEBI, PubChem, KEGG, LipidMaps).
MetLinkR automates this process by maintaining a mapping graph that links
metabolite identifiers from Metabolomics Workbench, MassBank, and other sources.

## When to Use

Use this skill when a downstream analysis requires unified metabolite IDs
or when integrating multi-study metabolomics datasets that use different
identifier systems.

## Procedure

1. Prepare a list of source metabolite IDs (e.g., RefMet names or HMDB IDs).
2. Load the MetLinkR mapping resource for the target database.
3. Call `reconcile_identifiers()` with source ID list and target namespace.
4. Inspect unmapped entries and apply manual curation if needed.
5. Merge reconciled IDs back into the feature table.
