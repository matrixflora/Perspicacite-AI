---
name: Feature Detection LC-MS
description: |
  Detect and align features in LC-MS data using MZmine for metabolomics workflows.
metadata:
  iri: https://w3id.org/holobiomicslab/asb-skill/feature-detection-lcms
  collection: https://w3id.org/holobiomicslab/asb-skill/collection/metabolomics/v1
  edam_operation: http://edamontology.org/operation_3215
  edam_topics:
    - http://edamontology.org/topic_3172
    - http://edamontology.org/topic_0091
  derived_from:
    - doi: 10.1021/acs.jproteome.0c00920
---

## Overview

Feature detection in LC-MS data is the first step in metabolomics data analysis.
MZmine provides robust algorithms for peak picking and feature alignment across
samples. This skill covers standard untargeted metabolomics feature detection
using MZmine.

## Procedure

1. Load raw mzML files into MZmine.
2. Apply mass detection to define raw data points as mass lists.
3. Run ADAP chromatogram builder to extract extracted ion chromatograms (EICs).
4. Smooth chromatograms and perform local minimum feature resolver.
5. Perform isotope grouping and charge state deconvolution.
6. Align features across samples using the join aligner.
7. Fill missing peaks and filter feature list by minimum sample count.
8. Export aligned feature list for downstream statistical analysis.
