#!/usr/bin/env python3
"""
BM25-based Paper Relevance Screening Tool

This script screens papers for relevance using BM25 similarity scoring on abstracts.

Workflow:
1. Input .bib → Extract DOIs
2. Run build_libraries_from_dois.py → Generate intermediate .bib
3. Fetch abstracts for both input and intermediate .bib entries
4. Compute BM25 similarity scores between abstracts
5. Filter and rank papers → Output final .bib with relevant papers
"""

import os
import sys
import re
import argparse
import logging
import time
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import bibtexparser
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bibdatabase import BibDatabase
from Bio import Entrez
from rank_bm25 import BM25Okapi
import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
import pandas as pd
import numpy as np
from tqdm import tqdm

# Add bibtex2kb to path for PubmedExplorer import
sys.path.append(str(Path(__file__).parent.parent.parent / "bibtex2kb"))
from pubmed_explorer import PubmedExplorer

# Download NLTK data if not already present
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt', quiet=True)

try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords', quiet=True)


class PaperScreener:
    """Main class for BM25-based paper screening"""

    def __init__(self, input_bib: str, output_bib: str, threshold: float = 0.3,
                 email: Optional[str] = None, csv_report: Optional[str] = None,
                 intermediate_dir: Optional[str] = None, cache_abstracts: bool = True,
                 auto_threshold: bool = False, threshold_strategy: Optional[str] = None):
        """
        Initialize the Paper Screener.

        Args:
            input_bib: Path to input .bib file (reference papers)
            output_bib: Path to output .bib file (screened papers)
            threshold: BM25 score threshold for filtering
            email: Email for PubMed API (optional but recommended)
            csv_report: Path to CSV report file (optional)
            intermediate_dir: Directory for intermediate files (optional)
            cache_abstracts: Whether to cache fetched abstracts
            auto_threshold: Whether to suggest optimal thresholds based on distribution
            threshold_strategy: Specific threshold strategy to use (non-interactive mode)
        """
        self.input_bib = Path(input_bib)
        self.output_bib = Path(output_bib)
        self.threshold = threshold
        self.email = email or "user@example.com"
        self.csv_report = Path(csv_report) if csv_report else None
        self.cache_abstracts = cache_abstracts
        self.auto_threshold = auto_threshold
        self.threshold_strategy = threshold_strategy

        # Set intermediate directory
        if intermediate_dir:
            self.intermediate_dir = Path(intermediate_dir)
        else:
            self.intermediate_dir = self.input_bib.parent / "intermediate"
        self.intermediate_dir.mkdir(parents=True, exist_ok=True)

        # Cache file for abstracts
        self.abstract_cache_file = self.intermediate_dir / "abstract_cache.json"
        self.abstract_cache = {}

        # Cache file for BM25 scores
        self.scores_cache_file = self.intermediate_dir / "bm25_scores.csv"

        # Set up logging
        self.setup_logging()

        # Set up Entrez
        Entrez.email = self.email
        Entrez.tool = "PaperScreener"

        # Load abstract cache if exists
        if self.cache_abstracts and self.abstract_cache_file.exists():
            import json
            try:
                with open(self.abstract_cache_file, 'r') as f:
                    self.abstract_cache = json.load(f)
                self.logger.info(f"Loaded {len(self.abstract_cache)} cached abstracts")
            except Exception as e:
                self.logger.warning(f"Failed to load abstract cache: {e}")

    def setup_logging(self):
        """Set up logging configuration"""
        log_file = self.intermediate_dir / "screening.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def save_abstract_cache(self):
        """Save abstract cache to file"""
        if self.cache_abstracts:
            import json
            try:
                with open(self.abstract_cache_file, 'w') as f:
                    json.dump(self.abstract_cache, f, indent=2)
                self.logger.info(f"Saved {len(self.abstract_cache)} abstracts to cache")
            except Exception as e:
                self.logger.warning(f"Failed to save abstract cache: {e}")

    def parse_bibtex(self, bib_file: Path) -> BibDatabase:
        """
        Parse a BibTeX file and return the database.

        Args:
            bib_file: Path to .bib file

        Returns:
            BibDatabase object
        """
        self.logger.info(f"Parsing BibTeX file: {bib_file}")

        if not bib_file.exists():
            raise FileNotFoundError(f"BibTeX file not found: {bib_file}")

        with open(bib_file, 'r', encoding='utf-8') as f:
            bib_database = bibtexparser.load(f)

        self.logger.info(f"Found {len(bib_database.entries)} entries in {bib_file}")
        return bib_database

    def extract_dois(self, bib_database: BibDatabase) -> List[str]:
        """
        Extract DOIs from BibTeX entries.

        Args:
            bib_database: BibDatabase object

        Returns:
            List of DOIs
        """
        self.logger.info("Extracting DOIs from entries...")
        dois = []

        for entry in bib_database.entries:
            doi = entry.get('doi', '').strip()
            if doi:
                dois.append(doi)

        self.logger.info(f"Extracted {len(dois)} DOIs from {len(bib_database.entries)} entries")
        return dois

    def build_library_from_dois(self, dois: List[str]) -> Optional[Path]:
        """
        Build PubMed library from DOIs using PubmedExplorer.

        Args:
            dois: List of DOIs to process

        Returns:
            Path to the merged intermediate .bib file, or None if failed
        """
        self.logger.info(f"Building library from {len(dois)} DOIs...")

        if not dois:
            self.logger.warning("No DOIs to process")
            return None

        successful_builds = []
        failed_builds = []

        for i, doi in enumerate(dois, 1):
            self.logger.info(f"[{i}/{len(dois)}] Processing DOI: {doi}")

            try:
                # Create PubmedExplorer instance for this DOI
                with PubmedExplorer(
                    email=self.email,
                    doi=doi,
                    get_citations=True,
                    get_references=True,
                    also_viewed=True
                ) as explorer:

                    # Generate a filename based on the DOI
                    safe_doi = doi.replace("/", "_").replace(".", "_")
                    filename = f"library_{safe_doi}.bib"
                    filepath = self.intermediate_dir / filename

                    self.logger.info(f"Building library for DOI: {doi}")

                    # Build the library
                    result_file = explorer.build_library(filename=str(filepath))

                    if result_file:
                        successful_builds.append((doi, result_file))
                        self.logger.info(f"✅ Successfully created library: {result_file}")
                    else:
                        failed_builds.append((doi, "Library building returned None"))
                        self.logger.warning(f"❌ Failed to create library for DOI: {doi}")

            except Exception as e:
                failed_builds.append((doi, str(e)))
                self.logger.error(f"❌ Error processing DOI {doi}: {e}")

        # Summary
        self.logger.info("="*60)
        self.logger.info(f"Library Building Summary:")
        self.logger.info(f"  Total DOIs processed: {len(dois)}")
        self.logger.info(f"  Successful builds: {len(successful_builds)}")
        self.logger.info(f"  Failed builds: {len(failed_builds)}")
        self.logger.info("="*60)

        if not successful_builds:
            self.logger.error("No libraries were successfully built")
            return None

        # Find all generated .bib files
        generated_bibs = list(self.intermediate_dir.glob("library_*.bib"))

        if generated_bibs:
            self.logger.info(f"Found {len(generated_bibs)} generated library files")
            # Merge all generated .bib files into one
            merged_bib = self._merge_bib_files(generated_bibs)
            return merged_bib
        else:
            self.logger.warning("No library files were generated")
            return None

    def _merge_bib_files(self, bib_files: List[Path]) -> Path:
        """Merge multiple .bib files into one"""
        self.logger.info(f"Merging {len(bib_files)} .bib files...")

        merged_db = BibDatabase()
        all_entries = []
        seen_ids = set()

        for bib_file in tqdm(bib_files, desc="Merging .bib files"):
            try:
                with open(bib_file, 'r', encoding='utf-8') as f:
                    db = bibtexparser.load(f)
                    for entry in db.entries:
                        # Deduplicate by ID
                        entry_id = entry.get('ID', '')
                        if entry_id and entry_id not in seen_ids:
                            all_entries.append(entry)
                            seen_ids.add(entry_id)
            except Exception as e:
                self.logger.warning(f"Failed to parse {bib_file}: {e}")

        merged_db.entries = all_entries

        # Save merged file
        merged_file = self.intermediate_dir / "merged_library.bib"
        writer = BibTexWriter()
        with open(merged_file, 'w', encoding='utf-8') as f:
            f.write(writer.write(merged_db))

        self.logger.info(f"Merged {len(all_entries)} unique entries to {merged_file}")
        return merged_file

    def fetch_abstract_from_pubmed(self, doi: Optional[str] = None,
                                   pmid: Optional[str] = None,
                                   title: Optional[str] = None) -> Optional[str]:
        """
        Fetch abstract from PubMed using DOI, PMID, or title.

        Args:
            doi: DOI of the paper
            pmid: PubMed ID
            title: Paper title (fallback)

        Returns:
            Abstract text or None
        """
        # Check cache first
        cache_key = doi or pmid or title
        if cache_key and cache_key in self.abstract_cache:
            return self.abstract_cache[cache_key]

        # Rate limiting (3 requests per second without API key, 10 with key)
        time.sleep(0.34)  # ~3 requests/second

        try:
            # Try to get PMID from DOI if needed
            if doi and not pmid:
                pmid = self._convert_doi_to_pmid(doi)

            # Try to search by title if no PMID
            if not pmid and title:
                pmid = self._search_pubmed_by_title(title)

            if not pmid:
                return None

            # Fetch abstract using PMID
            handle = Entrez.efetch(
                db="pubmed",
                id=pmid,
                retmode="xml"
            )

            from xml.etree import ElementTree as ET
            xml_data = handle.read()
            handle.close()

            root = ET.fromstring(xml_data)
            abstract_elem = root.find(".//AbstractText")

            if abstract_elem is not None and abstract_elem.text:
                abstract = abstract_elem.text
                # Cache the result
                if cache_key:
                    self.abstract_cache[cache_key] = abstract
                return abstract
            else:
                return None

        except Exception as e:
            self.logger.debug(f"Failed to fetch abstract: {e}")
            return None

    def _convert_doi_to_pmid(self, doi: str) -> Optional[str]:
        """Convert DOI to PMID using PubMed search"""
        try:
            handle = Entrez.esearch(
                db="pubmed",
                term=doi,
                retmode="json"
            )

            import json
            result = json.loads(handle.read())
            handle.close()

            id_list = result.get('esearchresult', {}).get('idlist', [])
            if id_list:
                return id_list[0]
            return None

        except Exception as e:
            self.logger.debug(f"Failed to convert DOI to PMID: {e}")
            return None

    def _search_pubmed_by_title(self, title: str) -> Optional[str]:
        """Search PubMed by title to get PMID"""
        try:
            # Clean title for search
            clean_title = title.strip().replace('\n', ' ')

            handle = Entrez.esearch(
                db="pubmed",
                term=f'"{clean_title}"[Title]',
                retmode="json",
                retmax=1
            )

            import json
            result = json.loads(handle.read())
            handle.close()

            id_list = result.get('esearchresult', {}).get('idlist', [])
            if id_list:
                return id_list[0]
            return None

        except Exception as e:
            self.logger.debug(f"Failed to search by title: {e}")
            return None

    def fetch_abstracts_for_entries(self, bib_database: BibDatabase) -> Dict[str, str]:
        """
        Fetch abstracts for all entries in a BibTeX database.

        Args:
            bib_database: BibDatabase object

        Returns:
            Dictionary mapping entry IDs to abstracts
        """
        self.logger.info(f"Fetching abstracts for {len(bib_database.entries)} entries...")

        abstracts = {}
        missing = 0

        for entry in tqdm(bib_database.entries, desc="Fetching abstracts"):
            entry_id = entry.get('ID', '')

            # Check if abstract already in entry
            if 'abstract' in entry and entry['abstract'].strip():
                abstracts[entry_id] = entry['abstract'].strip()
                continue

            # Try to fetch from PubMed
            doi = entry.get('doi', '').strip()
            pmid = entry.get('pmid', '').strip() or entry.get('ID', '').strip()
            title = entry.get('title', '').strip()

            abstract = self.fetch_abstract_from_pubmed(doi=doi, pmid=pmid, title=title)

            if abstract:
                abstracts[entry_id] = abstract
            else:
                missing += 1
                # Use title as fallback
                if title:
                    self.logger.debug(f"Using title as fallback for {entry_id}")
                    abstracts[entry_id] = title

        self.logger.info(f"Retrieved {len(abstracts)} abstracts ({missing} missing)")

        if missing > len(bib_database.entries) * 0.5:
            self.logger.warning(f"Warning: More than 50% of abstracts are missing!")

        # Save cache
        self.save_abstract_cache()

        return abstracts

    def preprocess_text(self, text: str) -> List[str]:
        """
        Preprocess text for BM25: tokenize, lowercase, remove stopwords.

        Args:
            text: Input text

        Returns:
            List of tokens
        """
        # Tokenize and lowercase
        tokens = word_tokenize(text.lower())

        # Remove stopwords and non-alphabetic tokens
        stop_words = set(stopwords.words('english'))
        tokens = [t for t in tokens if t.isalpha() and t not in stop_words]

        return tokens

    def suggest_optimal_threshold(self, scores_df: pd.DataFrame) -> Dict[str, float]:
        """
        Suggest optimal thresholds based on BM25 score distribution analysis.
        
        Args:
            scores_df: DataFrame with BM25 scores
            
        Returns:
            Dictionary with threshold suggestions and their characteristics
        """
        if scores_df.empty:
            return {}
        
        max_scores = scores_df['bm25_max_score'].values
        
        # Calculate distribution statistics
        mean_score = max_scores.mean()
        median_score = np.median(max_scores)
        std_score = max_scores.std()
        q1 = np.percentile(max_scores, 25)
        q3 = np.percentile(max_scores, 75)
        iqr = q3 - q1
        
        # Calculate percentiles
        p90 = np.percentile(max_scores, 90)
        p95 = np.percentile(max_scores, 95)
        p99 = np.percentile(max_scores, 99)
        
        # Score range analysis
        very_high = len(max_scores[max_scores >= 50])
        high = len(max_scores[(max_scores >= 20) & (max_scores < 50)])
        medium = len(max_scores[(max_scores >= 10) & (max_scores < 20)])
        low = len(max_scores[max_scores < 10])
        total = len(max_scores)
        
        # Suggest thresholds based on different strategies
        suggestions = {}
        
        # 1. Conservative (High Precision) - Top 5%
        conservative_threshold = p95
        conservative_count = len(max_scores[max_scores >= conservative_threshold])
        suggestions['conservative'] = {
            'threshold': conservative_threshold,
            'papers': conservative_count,
            'percentage': (conservative_count / total) * 100,
            'description': 'High precision - captures only the most relevant papers',
            'use_case': 'When you want very high confidence in relevance'
        }
        
        # 2. Balanced (Good Precision/Recall) - Top 20%
        balanced_threshold = p90
        balanced_count = len(max_scores[max_scores >= balanced_threshold])
        suggestions['balanced'] = {
            'threshold': balanced_threshold,
            'papers': balanced_count,
            'percentage': (balanced_count / total) * 100,
            'description': 'Balanced precision and recall',
            'use_case': 'General purpose screening with good balance'
        }
        
        # 3. Inclusive (High Recall) - Top 50%
        inclusive_threshold = median_score
        inclusive_count = len(max_scores[max_scores >= inclusive_threshold])
        suggestions['inclusive'] = {
            'threshold': inclusive_threshold,
            'papers': inclusive_count,
            'percentage': (inclusive_count / total) * 100,
            'description': 'High recall - captures more potentially relevant papers',
            'use_case': 'When you want to minimize missing relevant papers'
        }
        
        # 4. Statistical outlier threshold (Mean + 1.5*IQR)
        outlier_threshold = mean_score + 1.5 * iqr
        outlier_count = len(max_scores[max_scores >= outlier_threshold])
        suggestions['statistical'] = {
            'threshold': outlier_threshold,
            'papers': outlier_count,
            'percentage': (outlier_count / total) * 100,
            'description': 'Statistical outlier detection',
            'use_case': 'Data-driven threshold based on distribution'
        }
        
        # 5. Fixed semantic thresholds based on BM25 interpretation
        semantic_thresholds = {
            'very_high': 50.0,
            'high': 20.0,
            'medium': 10.0,
            'low': 5.0
        }
        
        for level, threshold in semantic_thresholds.items():
            count = len(max_scores[max_scores >= threshold])
            suggestions[f'semantic_{level}'] = {
                'threshold': threshold,
                'papers': count,
                'percentage': (count / total) * 100,
                'description': f'Semantic {level} relevance threshold',
                'use_case': f'Based on BM25 score interpretation for {level} relevance'
            }
        
        return suggestions
    
    def print_threshold_suggestions(self, scores_df: pd.DataFrame):
        """
        Print threshold suggestions with detailed analysis.
        Supports both interactive and non-interactive modes.
        
        Args:
            scores_df: DataFrame with BM25 scores
        """
        if scores_df.empty:
            self.logger.warning("No scores available for threshold analysis")
            return
        
        suggestions = self.suggest_optimal_threshold(scores_df)
        max_scores = scores_df['bm25_max_score'].values
        
        self.logger.info("="*80)
        self.logger.info("AUTOMATIC THRESHOLD SUGGESTIONS")
        self.logger.info("="*80)
        
        # Distribution summary
        self.logger.info(f"\nDistribution Summary:")
        self.logger.info(f"  Total papers: {len(max_scores)}")
        self.logger.info(f"  Score range: {max_scores.min():.2f} - {max_scores.max():.2f}")
        self.logger.info(f"  Mean: {max_scores.mean():.2f}")
        self.logger.info(f"  Median: {np.median(max_scores):.2f}")
        self.logger.info(f"  Std Dev: {max_scores.std():.2f}")
        
        # Score range breakdown
        very_high = len(max_scores[max_scores >= 50])
        high = len(max_scores[(max_scores >= 20) & (max_scores < 50)])
        medium = len(max_scores[(max_scores >= 10) & (max_scores < 20)])
        low = len(max_scores[max_scores < 10])
        total = len(max_scores)
        
        self.logger.info(f"\nScore Range Distribution:")
        self.logger.info(f"  Very High (≥50):   {very_high:3d} ({very_high/total*100:5.1f}%)")
        self.logger.info(f"  High (20-49):      {high:3d} ({high/total*100:5.1f}%)")
        self.logger.info(f"  Medium (10-19):    {medium:3d} ({medium/total*100:5.1f}%)")
        self.logger.info(f"  Low (<10):         {low:3d} ({low/total*100:5.1f}%)")
        
        # Threshold suggestions
        self.logger.info(f"\nRecommended Thresholds:")
        self.logger.info("-" * 80)
        
        # Order suggestions by threshold value (descending)
        sorted_suggestions = sorted(suggestions.items(), key=lambda x: x[1]['threshold'], reverse=True)
        
        for name, suggestion in sorted_suggestions:
            self.logger.info(f"\n{name.upper().replace('_', ' ')}:")
            self.logger.info(f"  Threshold: {suggestion['threshold']:.2f}")
            self.logger.info(f"  Papers: {suggestion['papers']:3d} ({suggestion['percentage']:5.1f}%)")
            self.logger.info(f"  Description: {suggestion['description']}")
            self.logger.info(f"  Use case: {suggestion['use_case']}")
        
        # Non-interactive mode: use specified strategy
        if self.threshold_strategy:
            if self.threshold_strategy in suggestions:
                self.threshold = suggestions[self.threshold_strategy]['threshold']
                self.logger.info(f"\n✅ NON-INTERACTIVE MODE: Using {self.threshold_strategy} strategy")
                self.logger.info(f"   Threshold set to: {self.threshold:.2f}")
                self.logger.info(f"   Papers above threshold: {suggestions[self.threshold_strategy]['papers']} ({suggestions[self.threshold_strategy]['percentage']:.1f}%)")
                self.logger.info(f"   Description: {suggestions[self.threshold_strategy]['description']}")
            else:
                self.logger.warning(f"Unknown threshold strategy: {self.threshold_strategy}")
                self.logger.info("Available strategies: " + ", ".join(suggestions.keys()))
            self.logger.info("="*80)
            return
        
        # Interactive threshold selection
        self.logger.info(f"\n" + "="*80)
        self.logger.info("INTERACTIVE THRESHOLD SELECTION")
        self.logger.info("="*80)
        
        while True:
            self.logger.info(f"\nCurrent threshold: {self.threshold}")
            self.logger.info(f"Papers above current threshold: {len(max_scores[max_scores >= self.threshold])}")
            
            self.logger.info(f"\nOptions:")
            self.logger.info(f"  1. Use conservative threshold ({suggestions['conservative']['threshold']:.2f})")
            self.logger.info(f"  2. Use balanced threshold ({suggestions['balanced']['threshold']:.2f})")
            self.logger.info(f"  3. Use inclusive threshold ({suggestions['inclusive']['threshold']:.2f})")
            self.logger.info(f"  4. Use statistical threshold ({suggestions['statistical']['threshold']:.2f})")
            self.logger.info(f"  5. Enter custom threshold")
            self.logger.info(f"  6. Keep current threshold ({self.threshold})")
            self.logger.info(f"  7. Skip threshold selection")
            
            try:
                choice = input("\nEnter your choice (1-7): ").strip()
                
                if choice == '1':
                    self.threshold = suggestions['conservative']['threshold']
                    self.logger.info(f"✅ Set threshold to {self.threshold:.2f} (conservative)")
                    break
                elif choice == '2':
                    self.threshold = suggestions['balanced']['threshold']
                    self.logger.info(f"✅ Set threshold to {self.threshold:.2f} (balanced)")
                    break
                elif choice == '3':
                    self.threshold = suggestions['inclusive']['threshold']
                    self.logger.info(f"✅ Set threshold to {self.threshold:.2f} (inclusive)")
                    break
                elif choice == '4':
                    self.threshold = suggestions['statistical']['threshold']
                    self.logger.info(f"✅ Set threshold to {self.threshold:.2f} (statistical)")
                    break
                elif choice == '5':
                    custom_threshold = float(input("Enter custom threshold: "))
                    if 0 <= custom_threshold <= max_scores.max():
                        self.threshold = custom_threshold
                        self.logger.info(f"✅ Set threshold to {self.threshold:.2f} (custom)")
                        break
                    else:
                        self.logger.warning(f"Threshold must be between 0 and {max_scores.max():.2f}")
                elif choice == '6':
                    self.logger.info(f"✅ Keeping current threshold: {self.threshold:.2f}")
                    break
                elif choice == '7':
                    self.logger.info("Skipping threshold selection")
                    break
                else:
                    self.logger.warning("Invalid choice. Please enter 1-7.")
                    
            except (ValueError, KeyboardInterrupt):
                self.logger.info("Skipping threshold selection")
                break
        
        self.logger.info("="*80)

    def compute_bm25_scores(self, reference_abstracts: Dict[str, str],
                           candidate_abstracts: Dict[str, str]) -> pd.DataFrame:
        """
        Compute BM25 similarity scores between reference and candidate abstracts.

        Args:
            reference_abstracts: Dict of reference paper abstracts {id: abstract}
            candidate_abstracts: Dict of candidate paper abstracts {id: abstract}

        Returns:
            DataFrame with scores for each candidate paper
        """
        self.logger.info(f"Computing BM25 scores for {len(candidate_abstracts)} candidates "
                        f"against {len(reference_abstracts)} references...")

        # Preprocess reference abstracts
        reference_ids = list(reference_abstracts.keys())
        reference_texts = [reference_abstracts[id] for id in reference_ids]

        self.logger.info("Preprocessing reference abstracts...")
        tokenized_corpus = [self.preprocess_text(text) for text in tqdm(reference_texts, desc="Tokenizing references")]

        # Build BM25 index
        self.logger.info("Building BM25 index...")
        bm25 = BM25Okapi(tokenized_corpus)

        # Compute scores for each candidate
        self.logger.info("Computing scores for candidates...")
        results = []

        for candidate_id, candidate_text in tqdm(candidate_abstracts.items(), desc="Scoring candidates"):
            tokenized_query = self.preprocess_text(candidate_text)

            # Get scores against all reference papers
            scores = bm25.get_scores(tokenized_query)

            # Compute statistics
            max_score = float(max(scores))
            mean_score = float(sum(scores) / len(scores))
            max_idx = int(scores.argmax())
            most_similar_paper = reference_ids[max_idx]

            results.append({
                'entry_id': candidate_id,
                'bm25_max_score': max_score,
                'bm25_mean_score': mean_score,
                'most_similar_paper': most_similar_paper,
                'most_similar_score': max_score
            })

        df = pd.DataFrame(results)
        df = df.sort_values('bm25_max_score', ascending=False)

        self.logger.info(f"Computed scores. Max score: {df['bm25_max_score'].max():.4f}, "
                        f"Mean score: {df['bm25_max_score'].mean():.4f}")

        # Save scores to cache
        try:
            df.to_csv(self.scores_cache_file, index=False)
            self.logger.info(f"Saved BM25 scores to cache: {self.scores_cache_file}")
        except Exception as e:
            self.logger.warning(f"Failed to save scores cache: {e}")

        return df

    def filter_and_output(self, candidate_db: BibDatabase, scores_df: pd.DataFrame):
        """
        Filter candidates by threshold and generate output files.

        Args:
            candidate_db: BibDatabase of candidate papers
            scores_df: DataFrame with BM25 scores
        """
        self.logger.info(f"Filtering papers with threshold {self.threshold}...")

        # Filter by threshold
        filtered_df = scores_df[scores_df['bm25_max_score'] >= self.threshold]
        self.logger.info(f"Filtered to {len(filtered_df)} papers above threshold "
                        f"(from {len(scores_df)} candidates)")

        if len(filtered_df) == 0:
            self.logger.warning("No papers passed the threshold!")
            return

        # Load input papers to exclude duplicates
        input_db = self.parse_bibtex(self.input_bib)
        input_dois = {e.get('doi', '').strip().lower() for e in input_db.entries if e.get('doi', '').strip()}
        input_titles = {e.get('title', '').strip().lower() for e in input_db.entries if e.get('title', '').strip()}
        self.logger.info(f"Excluding {len(input_dois)} input papers from output (by DOI/title)")

        # Create output database
        output_db = BibDatabase()
        output_entries = []
        duplicates_removed = 0

        # Create a lookup for scores (ensure entry_id is string for matching)
        scores_df_copy = scores_df.copy()
        scores_df_copy['entry_id'] = scores_df_copy['entry_id'].astype(str)
        score_lookup = scores_df_copy.set_index('entry_id').to_dict('index')

        for entry in candidate_db.entries:
            entry_id = entry.get('ID', '')

            if entry_id in score_lookup:
                scores = score_lookup[entry_id]

                if scores['bm25_max_score'] >= self.threshold:
                    # Check if this paper is a duplicate of input papers
                    doi = entry.get('doi', '').strip().lower()
                    title = entry.get('title', '').strip().lower()

                    is_duplicate = False
                    if doi and doi in input_dois:
                        is_duplicate = True
                    elif title and title in input_titles:
                        is_duplicate = True

                    if is_duplicate:
                        duplicates_removed += 1
                        self.logger.debug(f"Skipping duplicate: {entry_id}")
                        continue

                    # Ensure all entry fields are strings (clean up any non-string fields)
                    clean_entry = {}
                    for key, value in entry.items():
                        if isinstance(value, (int, float)):
                            clean_entry[key] = str(value)
                        else:
                            clean_entry[key] = value

                    # Add score fields to entry (ensure all are strings for BibTeX)
                    clean_entry['bm25_max_score'] = f"{scores['bm25_max_score']:.4f}"
                    clean_entry['bm25_mean_score'] = f"{scores['bm25_mean_score']:.4f}"
                    clean_entry['most_similar_paper'] = str(scores['most_similar_paper'])

                    output_entries.append(clean_entry)

        output_db.entries = output_entries

        if duplicates_removed > 0:
            self.logger.info(f"Removed {duplicates_removed} duplicate papers (already in input)")

        # Write output .bib file
        self.logger.info(f"Writing output to {self.output_bib}...")
        self.logger.info(f"Final output: {len(output_entries)} unique papers")
        writer = BibTexWriter()
        with open(self.output_bib, 'w', encoding='utf-8') as f:
            f.write(writer.write(output_db))

        self.logger.info(f"✅ Output written to {self.output_bib}")

        # Write CSV report if requested
        if self.csv_report:
            self.logger.info(f"Writing CSV report to {self.csv_report}...")

            # Create detailed report
            report_data = []

            for entry in output_entries:
                entry_id = entry.get('ID', '')
                scores = score_lookup.get(entry_id, {})

                report_data.append({
                    'Entry Key': entry_id,
                    'Title': entry.get('title', ''),
                    'Authors': entry.get('author', ''),
                    'Year': entry.get('year', ''),
                    'DOI': entry.get('doi', ''),
                    'BM25 Max': scores.get('bm25_max_score', 0),
                    'BM25 Mean': scores.get('bm25_mean_score', 0),
                    'Most Similar': scores.get('most_similar_paper', ''),
                    'Journal': entry.get('journal', ''),
                    'URL': entry.get('url', '')
                })

            report_df = pd.DataFrame(report_data)
            report_df = report_df.sort_values('BM25 Max', ascending=False)
            report_df.to_csv(self.csv_report, index=False)

            self.logger.info(f"✅ CSV report written to {self.csv_report}")

    def refilter(self) -> bool:
        """
        Re-filter using cached BM25 scores with a new threshold.

        Returns:
            True if successful, False otherwise
        """
        self.logger.info("="*60)
        self.logger.info("Re-filtering with New Threshold")
        self.logger.info("="*60)

        # Check if scores cache exists
        if not self.scores_cache_file.exists():
            self.logger.warning(f"Scores cache not found: {self.scores_cache_file}")
            self.logger.info("Attempting to reconstruct from previous output...")

            # Check if a previous output exists that we can extract scores from
            if self.output_bib.exists():
                try:
                    prev_db = self.parse_bibtex(self.output_bib)
                    scores_data = []

                    for entry in prev_db.entries:
                        if 'bm25_max_score' in entry:
                            scores_data.append({
                                'entry_id': entry.get('ID', ''),
                                'bm25_max_score': float(entry.get('bm25_max_score', 0)),
                                'bm25_mean_score': float(entry.get('bm25_mean_score', 0)),
                                'most_similar_paper': entry.get('most_similar_paper', ''),
                                'most_similar_score': float(entry.get('bm25_max_score', 0))
                            })

                    if scores_data:
                        scores_df = pd.DataFrame(scores_data)
                        self.logger.info(f"Reconstructed {len(scores_df)} scores from previous output")

                        # We need to get ALL candidates, not just filtered ones
                        # So we need to recompute - inform user
                        self.logger.warning("Note: Previous output only contains filtered papers.")
                        self.logger.warning("To use --refilter with the full dataset, please:")
                        self.logger.warning("  1. Run the full workflow once with the updated script")
                        self.logger.warning("  2. Or continue with limited data (only previously filtered papers)")

                        response = input("\nContinue with limited data? (y/n): ").strip().lower()
                        if response != 'y':
                            return False
                    else:
                        self.logger.error("No BM25 scores found in previous output")
                        self.logger.error("Please run the full workflow first")
                        return False

                except Exception as e:
                    self.logger.error(f"Failed to reconstruct scores: {e}")
                    self.logger.error("Please run the full workflow first")
                    return False
            else:
                self.logger.error("No previous output found")
                self.logger.error("Please run the full workflow first before using --refilter")
                return False
        else:
            # Load cached scores
            self.logger.info(f"Loading cached BM25 scores from {self.scores_cache_file}...")
            try:
                scores_df = pd.read_csv(self.scores_cache_file)
                self.logger.info(f"Loaded {len(scores_df)} cached scores")
            except Exception as e:
                self.logger.error(f"Failed to load scores cache: {e}")
                return False

        # Check if merged library exists
        merged_bib = self.intermediate_dir / "merged_library.bib"
        if not merged_bib.exists():
            self.logger.error(f"Merged library not found: {merged_bib}")
            self.logger.error("Please run the full workflow first before using --refilter")
            return False

        # Parse candidate database
        self.logger.info(f"Parsing candidate library from {merged_bib}...")
        try:
            candidate_db = self.parse_bibtex(merged_bib)
        except Exception as e:
            self.logger.error(f"Failed to parse merged library: {e}")
            return False

        # Display score statistics
        self.logger.info(f"\nScore Statistics:")
        self.logger.info(f"  Total papers: {len(scores_df)}")
        self.logger.info(f"  Max score: {scores_df['bm25_max_score'].max():.4f}")
        self.logger.info(f"  Mean score: {scores_df['bm25_max_score'].mean():.4f}")
        self.logger.info(f"  Median score: {scores_df['bm25_max_score'].median():.4f}")
        self.logger.info(f"  Min score: {scores_df['bm25_max_score'].min():.4f}")
        self.logger.info(f"\nNew threshold: {self.threshold}")

        papers_above_threshold = len(scores_df[scores_df['bm25_max_score'] >= self.threshold])
        self.logger.info(f"Papers above threshold: {papers_above_threshold}/{len(scores_df)}")

        # Suggest optimal thresholds if enabled
        if self.auto_threshold or self.threshold_strategy:
            self.logger.info(f"\nAnalyzing score distribution and suggesting thresholds...")
            self.print_threshold_suggestions(scores_df)

        # Filter and output
        self.logger.info(f"\nFiltering and generating output...")
        self.filter_and_output(candidate_db, scores_df)

        self.logger.info("\n" + "="*60)
        self.logger.info("Re-filtering completed!")
        self.logger.info("="*60)

        return True

    def run(self, dry_run: bool = False, refilter: bool = False):
        """
        Execute the complete screening workflow.

        Args:
            dry_run: If True, skip library building and abstract fetching
            refilter: If True, skip all steps and just re-filter with new threshold
        """
        # If refilter mode, skip everything and just re-filter
        if refilter:
            return self.refilter()

        self.logger.info("="*60)
        self.logger.info("Starting Paper Screening Workflow")
        self.logger.info("="*60)

        # Step 1: Parse input .bib and extract DOIs
        self.logger.info("\n[Step 1/5] Parsing input .bib file and extracting DOIs...")
        input_db = self.parse_bibtex(self.input_bib)
        dois = self.extract_dois(input_db)

        if not dois and not dry_run:
            self.logger.error("No DOIs found in input file. Cannot proceed.")
            return

        # Step 2: Build library from DOIs
        if not dry_run:
            self.logger.info("\n[Step 2/5] Building library from DOIs...")
            intermediate_bib = self.build_library_from_dois(dois)

            if not intermediate_bib:
                self.logger.error("Failed to build library. Cannot proceed.")
                return
        else:
            self.logger.info("\n[Step 2/5] Skipping library building (dry run)")
            intermediate_bib = None

        # Step 3: Fetch abstracts
        if not dry_run:
            self.logger.info("\n[Step 3/5] Fetching abstracts...")

            # Fetch abstracts for reference papers
            self.logger.info("Fetching abstracts for reference papers...")
            reference_abstracts = self.fetch_abstracts_for_entries(input_db)

            # Parse and fetch abstracts for candidate papers
            self.logger.info("Fetching abstracts for candidate papers...")
            candidate_db = self.parse_bibtex(intermediate_bib)
            candidate_abstracts = self.fetch_abstracts_for_entries(candidate_db)
        else:
            self.logger.info("\n[Step 3/5] Skipping abstract fetching (dry run)")
            reference_abstracts = {}
            candidate_abstracts = {}
            candidate_db = BibDatabase()

        # Step 4: Compute BM25 scores
        if not dry_run and reference_abstracts and candidate_abstracts:
            self.logger.info("\n[Step 4/5] Computing BM25 similarity scores...")
            scores_df = self.compute_bm25_scores(reference_abstracts, candidate_abstracts)
            
            # Suggest optimal thresholds if enabled
            if self.auto_threshold or self.threshold_strategy:
                self.logger.info("\n[Step 4.5/5] Analyzing score distribution and suggesting thresholds...")
                self.print_threshold_suggestions(scores_df)
        else:
            self.logger.info("\n[Step 4/5] Skipping BM25 computation (dry run or no abstracts)")
            scores_df = pd.DataFrame()

        # Step 5: Filter and output
        if not dry_run and not scores_df.empty:
            self.logger.info("\n[Step 5/5] Filtering and generating output...")
            self.filter_and_output(candidate_db, scores_df)
        else:
            self.logger.info("\n[Step 5/5] Skipping output generation (dry run or no scores)")

        self.logger.info("\n" + "="*60)
        self.logger.info("Screening workflow completed!")
        self.logger.info("="*60)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="BM25-based Paper Relevance Screening Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python screen_papers.py --input references.bib --output screened.bib

  # With automatic threshold suggestion
  python screen_papers.py --input refs.bib --output screened.bib --auto-threshold

  # With custom threshold and email
  python screen_papers.py --input refs.bib --output screened.bib --threshold 0.5 --email user@domain.com

  # With CSV report
  python screen_papers.py --input refs.bib --output screened.bib --csv-report report.csv

  # Dry run (test without fetching data)
  python screen_papers.py --input refs.bib --output screened.bib --dry-run
        """
    )

    parser.add_argument(
        '--input',
        required=True,
        help='Path to input .bib file containing reference papers'
    )

    parser.add_argument(
        '--output',
        required=True,
        help='Path to output .bib file for screened papers'
    )

    parser.add_argument(
        '--threshold',
        type=float,
        default=0.3,
        help='BM25 score threshold for filtering (default: 0.3)'
    )

    parser.add_argument(
        '--email',
        help='Email address for PubMed API (recommended for better rate limits)'
    )

    parser.add_argument(
        '--csv-report',
        help='Path to CSV report file (optional)'
    )

    parser.add_argument(
        '--intermediate-dir',
        help='Directory for intermediate files (default: input_dir/intermediate)'
    )

    parser.add_argument(
        '--no-cache',
        action='store_true',
        help='Disable abstract caching'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Test the workflow without actually fetching data'
    )

    parser.add_argument(
        '--refilter',
        action='store_true',
        help='Re-filter using cached scores with new threshold (skips library building and abstract fetching)'
    )

    parser.add_argument(
        '--auto-threshold',
        action='store_true',
        help='Enable automatic threshold suggestion based on score distribution'
    )

    parser.add_argument(
        '--threshold-strategy',
        choices=['conservative', 'balanced', 'inclusive', 'statistical', 'semantic_very_high', 'semantic_high', 'semantic_medium', 'semantic_low'],
        help='Automatically select threshold using specified strategy (non-interactive mode)'
    )

    args = parser.parse_args()

    # Create screener instance
    screener = PaperScreener(
        input_bib=args.input,
        output_bib=args.output,
        threshold=args.threshold,
        email=args.email,
        csv_report=args.csv_report,
        intermediate_dir=args.intermediate_dir,
        cache_abstracts=not args.no_cache,
        auto_threshold=args.auto_threshold,
        threshold_strategy=args.threshold_strategy
    )

    # Run the screening workflow
    screener.run(dry_run=args.dry_run, refilter=args.refilter)


if __name__ == "__main__":
    main()
