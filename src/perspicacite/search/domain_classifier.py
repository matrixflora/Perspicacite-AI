"""Lightweight domain classifier for query-to-provider routing."""

from __future__ import annotations

import re

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("biomedical", re.compile(
        r"(\bgene\b|\bprotein\b|\bmrna\b|\brna\b|\bdna\b|\bgenome\b|metabol|microbiom|microbiota|"
        r"pathogen|disease|clinical|patient|\bdrug\b|pharma|enzyme|\bantibody\b|"
        r"\bpcr\b|sequencing|transcriptom|proteom|\bcell\b|\btissue\b|tumor|\bcancer\b|"
        r"bacteria|virus|fungal|\bpmid\b|\bpubmed\b|medline|\bmesh\b|immunotherap|"
        r"crispr|epigenet|biomarker|lipidom|glycom|spectroscopy)",
        re.IGNORECASE,
    )),
    ("chemistry", re.compile(
        r"(\bcompound\b|\bmolecule\b|\bsmiles\b|inchikey|inchi|\bchemical\b|\breaction\b|"
        r"synthesis|polymer|ligand|solvent|catalyst|reagent|metabolite|"
        r"metabolomics|spectroscopy|\bnmr\b|mass[\s-]?spec|chromatograph|pubchem|cas[\s-]?number|"
        r"mol(?:ecular)?[\s-]?weight|formula|pharmacophore|cheminformat|"
        r"stereochemist|conformer|tautomer|discovery)",
        re.IGNORECASE,
    )),
    ("cs", re.compile(
        r"(\balgorithm\b|neural[\s-]?network|deep[\s-]?learn|machine[\s-]?learn|"
        r"\btransformer\b|\bllm\b|language[\s-]?model|graph[\s-]?neural|\bconvolutional\b|"
        r"\bbenchmark\b|\bdataset\b|\bsoftware\b|\bframework\b|\bcompiler\b|\bdistributed\b|"
        r"\bparallel\b|\bblockchain\b|\bdblp\b|reinforcement[\s-]?learn|attention[\s-]?mechanism|"
        r"\brecurrent\b|random[\s-]?forest|gradient[\s-]?boost)",
        re.IGNORECASE,
    )),
    ("physics", re.compile(
        r"(\bquantum\b|particle|quark|lepton|\bboson\b|\bhiggs\b|dark[\s-]?matter|"
        r"dark[\s-]?energy|collider|\blhc\b|\bcern\b|neutrino|hadron|gravitational[\s-]?wave|"
        r"detector|accelerator|\bplasma\b|inspire|\bhep[\s-]?(ph|th|ex|lat)\b|"
        r"supersymmet|string[\s-]?theory|\bqcd\b|\bqed\b|\bfeynman\b)",
        re.IGNORECASE,
    )),
    ("astronomy", re.compile(
        r"(galaxy|galax|\bstar\b|\bplanet\b|exoplanet|telescope|nebula|pulsar|"
        r"black[\s-]?hole|quasar|supernova|cosmolog|redshift|spectral|"
        r"photometric|\bhubble\b|\bjwst\b|chandra|fermi|\bnasa\b|astrophys|ads[\s-]?nasa|"
        r"dark[\s-]?energy[\s-]?survey|milky[\s-]?way|solar[\s-]?system|asteroid)",
        re.IGNORECASE,
    )),
]


class DomainClassifier:
    """Maps a query string to a list of domain tags.

    Returns ['general'] when no specific domain matches — signals to the
    DomainAwareAggregator that general-tagged providers should run.
    Multi-label: a query can match several domains simultaneously.
    """

    def classify(self, query: str) -> list[str]:
        if not query or not query.strip():
            return ["general"]
        domains: list[str] = []
        for domain, pattern in _RULES:
            if pattern.search(query):
                domains.append(domain)
        return domains if domains else ["general"]
