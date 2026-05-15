# Second-round audit — 2026-05-15T15:36:40.302696Z

Git SHA: `9ad0baa`

## Articles

- **crispr_cas9** (biomedical / chemistry): `10.1126/science.1225829`
- **ligo_gw150914** (physics): `10.1103/PhysRevLett.116.061102`
- **gpt3** (ML / NLP (arXiv-only)): `10.48550/arXiv.2005.14165`

## doi_resolution

```json
{
  "crispr_cas9": {
    "openalex_id": "W2045435533",
    "seconds": 0.61,
    "resolved_title": "A Programmable Dual-RNA\u2013Guided DNA Endonuclease in Adaptive Bacterial Immunity",
    "cited_by_count": 17089,
    "publication_year": 2012,
    "status": "ok"
  },
  "ligo_gw150914": {
    "openalex_id": "W2252795400",
    "seconds": 2.04,
    "resolved_title": "Observation of Gravitational Waves from a Binary Black Hole Merger",
    "cited_by_count": 14109,
    "publication_year": 2016,
    "status": "ok"
  },
  "gpt3": {
    "openalex_id": "W3030163527",
    "seconds": 0.49,
    "resolved_title": "Language Models are Few-Shot Learners",
    "cited_by_count": 3029,
    "publication_year": 2020,
    "status": "ok"
  }
}
```

## bug_fixes

```json
{
  "fix_1_provenance_init_db": {
    "status": "PASS",
    "round_trip_ok": true
  },
  "fix_2_source_reference_authors_list": {
    "status": "PASS",
    "list_input": [
      "Jumper",
      "Evans",
      "Pritzel"
    ],
    "str_input": [
      "Alice",
      "Bob"
    ],
    "none_input": []
  },
  "fix_4_paper_source_enum": {
    "status": "PASS",
    "new_values": [
      "openalex",
      "pubmed",
      "arxiv",
      "crossref"
    ]
  },
  "fix_5_budget_tracker_kwargs": {
    "status": "PASS",
    "tokens_total": 1100,
    "raises_at_cap": true
  },
  "fix_6_kb_route_hit_iter": {
    "status": "PASS",
    "ranked": [
      [
        "crispr_kb",
        1.0
      ],
      [
        "physics_kb",
        0.0
      ],
      [
        "ml_kb",
        0.0
      ]
    ]
  }
}
```

## ingest_cycle

```json
{
  "capsule_root": "/var/folders/7t/n4yt9y_n1pb9dcrdxrltwqfw0000gn/T/audit2_capsule_vad6p996",
  "crispr_cas9": {
    "abstract_len": 997,
    "cited_by_count": 17089,
    "paper_source": "crossref",
    "chunk_count": 1,
    "blocks_written": 1,
    "resources_written": 0,
    "status": "ok"
  },
  "ligo_gw150914": {
    "abstract_len": 1207,
    "cited_by_count": 14109,
    "paper_source": "openalex",
    "chunk_count": 1,
    "blocks_written": 1,
    "resources_written": 0,
    "status": "ok"
  },
  "gpt3": {
    "abstract_len": 1789,
    "cited_by_count": 3029,
    "paper_source": "arxiv",
    "chunk_count": 1,
    "blocks_written": 1,
    "resources_written": 0,
    "status": "ok"
  }
}
```

## cite_graph

```json
{
  "crispr_cas9": {
    "status": "ok",
    "seconds": 1.44,
    "hit_count": 10,
    "sample_titles": [
      "Multiplex Genome Engineering Using CRISPR/Cas Systems",
      "Genome engineering using the CRISPR-Cas9 system",
      "RNA-Guided Human Genome Engineering via Cas9"
    ]
  },
  "ligo_gw150914": {
    "status": "ok",
    "seconds": 3.14,
    "hit_count": 10,
    "sample_titles": [
      "SciPy 1.0: fundamental algorithms for scientific computing in Python",
      "Array programming with NumPy",
      "Array programming with NumPy"
    ]
  },
  "gpt3": {
    "status": "ok",
    "seconds": 1.15,
    "hit_count": 10,
    "sample_titles": [
      "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale",
      "Learning Transferable Visual Models From Natural Language Supervision",
      "Evolutionary-scale prediction of atomic-level protein structure with a language "
    ]
  }
}
```

## response_assembly

```json
{
  "sources_len": 2,
  "sources_with_list_authors": 1,
  "citations": [
    "[Jinek et al., 2012]",
    "[Abbott et al., 2016]"
  ],
  "kb_names_passthrough": [
    "crispr_kb",
    "biomed_kb"
  ],
  "events_built": [
    "status",
    "source",
    "figure_ref",
    "done"
  ]
}
```