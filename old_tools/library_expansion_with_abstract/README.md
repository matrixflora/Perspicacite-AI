# Library Expansion Tools (vendored from ScienceGuide)

These scripts were ported from the v1 ScienceGuide project on 2026-05-08. They are kept under `old_tools/` because they are not yet integrated with perspicacite's KB infrastructure — they are standalone CLI tools that produce `.bib` files you can then drag into the perspicacite web UI's "Add from BibTeX" flow.

## Invocation in perspicacite v2

All scripts are run via `uv run`:

```bash
# Set your NCBI email once (mandatory; obvious placeholders are rejected)
export PERSPICACITE_NCBI_EMAIL="you@your-domain.org"

# Expand a single DOI into a library of citations + references + related
uv run python old_tools/library_expansion_with_abstract/build_libraries_from_dois.py \
    --doi 10.1038/s41467-022-33890-w \
    --output-dir ./expanded_library/

# Or expand every DOI inside an existing .bib file
uv run python old_tools/library_expansion_with_abstract/build_libraries_from_dois.py \
    --bibtex seed.bib \
    --output-dir ./expanded_library/

# Screen one expanded library against a reference set via BM25
# (--input takes a single .bib path; if you expanded multiple seeds, merge or screen them one at a time)
uv run python old_tools/library_expansion_with_abstract/screen_papers.py \
    --input expanded_library/library_10_1038_s41467-022-33890-w.bib \
    --output screened.bib \
    --threshold 0.3
```

If you don't set `PERSPICACITE_NCBI_EMAIL` and don't pass `--email`, both scripts fail fast with a clear error.

NLTK corpora (`punkt`, `stopwords`) auto-download on first run of `screen_papers.py`.

The rest of this file is the original ScienceGuide README, kept for reference.

---

# BM25-based Paper Relevance Screening Tool

A Python script that screens papers for relevance using BM25 similarity scoring on abstracts. This tool helps researchers quickly identify relevant papers from a large collection by comparing them against a set of reference papers.

## Overview

The tool implements a comprehensive workflow:

1. **Input Processing** → Extract DOIs from reference papers (.bib file)
2. **Library Building** → Automatically fetch related papers using `build_libraries_from_dois.py`
3. **Abstract Retrieval** → Fetch abstracts from PubMed for all papers
4. **BM25 Scoring** → Compute similarity scores between candidate and reference abstracts
5. **Filtering & Output** → Generate filtered .bib file and optional CSV report

## Features

- **Automated workflow** from reference papers to filtered results
- **BM25 algorithm** for robust text similarity matching
- **PubMed integration** with automatic abstract retrieval
- **Abstract caching** to avoid redundant API calls
- **Progress bars** for long-running operations
- **Comprehensive logging** for debugging and monitoring
- **CSV reports** for detailed analysis
- **Dry-run mode** for testing without fetching data
- **Conda environment** integration for reproducibility

## Installation

### Prerequisites

- Python 3.8+
- Conda (for running `build_libraries_from_dois.py`)
- Conda environment named `scienceguid` (required by the library building script)

### Install Dependencies

```bash
cd abstract_compare
pip install -r requirements.txt
```

### Required Packages

- `bibtexparser>=1.4.0` - BibTeX file parsing
- `biopython>=1.80` - PubMed API access
- `rank-bm25>=0.2.2` - BM25 implementation
- `nltk>=3.8` - Text preprocessing
- `pandas>=1.5.0` - Data manipulation
- `tqdm>=4.65.0` - Progress bars

## Usage

### Basic Usage

```bash
python screen_papers.py \
  --input references.bib \
  --output screened_papers.bib
```

### Full Options

```bash
python screen_papers.py \
  --input references.bib \
  --output screened_papers.bib \
  --threshold 0.5 \
  --email your.email@domain.com \
  --csv-report report.csv \
  --intermediate-dir ./temp
```

### Command-Line Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--input` | Yes | - | Path to input .bib file (reference papers) |
| `--output` | Yes | - | Path to output .bib file (screened papers) |
| `--threshold` | No | 0.3 | BM25 score threshold for filtering (0.0-1.0+) |
| `--email` | No | user@example.com | Email for PubMed API (recommended) |
| `--csv-report` | No | None | Path to CSV report file |
| `--intermediate-dir` | No | input_dir/intermediate | Directory for intermediate files |
| `--no-cache` | No | False | Disable abstract caching |
| `--dry-run` | No | False | Test workflow without fetching data |

## Workflow Details

### Step 1: Input Processing

The script parses your input .bib file and extracts all DOIs:

```python
# Input: references.bib
@article{paper1,
  title = {Example Paper},
  doi = {10.1234/example},
  ...
}
```

### Step 2: Library Building

Automatically executes `../build_libraries_from_dois.py` in the `scienceguid` conda environment:

- Fetches citations, references, and related papers from PubMed
- Generates intermediate .bib files for each DOI
- Merges all results into a single intermediate library

**Important**: This step requires:
- The `scienceguid` conda environment to be configured
- The `build_libraries_from_dois.py` script in the parent directory
- Network access to PubMed

### Step 3: Abstract Retrieval

Fetches abstracts for both reference and candidate papers:

- Uses Entrez API from Biopython
- Searches by DOI, PMID, or title
- Implements rate limiting (3 requests/second without API key)
- Caches results to avoid redundant API calls
- Falls back to title if abstract is unavailable

**API Rate Limits**:
- Without API key: 3 requests/second
- With API key (via `--email`): 10 requests/second

### Step 4: BM25 Scoring

Computes BM25 similarity scores:

1. **Preprocessing**:
   - Tokenization (lowercase)
   - Remove English stopwords
   - Keep only alphabetic tokens

2. **BM25 Index**:
   - Build index from reference paper abstracts
   - Use BM25Okapi algorithm (k1=1.5, b=0.75)

3. **Scoring**:
   - For each candidate paper:
     - Compute scores against all reference papers
     - Record: max score, mean score, most similar paper

### Step 5: Output Generation

Creates filtered outputs:

**Output .bib file**:
```bibtex
@article{filtered_paper,
  title = {Relevant Paper Title},
  author = {Smith, John},
  doi = {10.5678/relevant},
  abstract = {Full abstract text...},
  bm25_max_score = {0.87},
  bm25_mean_score = {0.62},
  most_similar_paper = {reference_paper_2023},
  ...
}
```

**CSV Report** (optional):
| Entry Key | Title | Authors | Year | DOI | BM25 Max | BM25 Mean | Most Similar |
|-----------|-------|---------|------|-----|----------|-----------|--------------|
| paper1 | Example | Smith | 2024 | 10... | 0.87 | 0.62 | ref_paper |

## Interpreting BM25 Scores

BM25 scores measure the relevance of candidate papers to your reference set:

- **0.0-0.2**: Low relevance (probably not related)
- **0.2-0.5**: Moderate relevance (may be tangentially related)
- **0.5-1.0**: High relevance (likely very related)
- **>1.0**: Very high relevance (extremely similar)

**Recommended thresholds**:
- Conservative: 0.5+ (high precision, lower recall)
- Balanced: 0.3+ (good balance)
- Inclusive: 0.2+ (high recall, lower precision)

Adjust based on your needs and the distribution of scores in your dataset.

## Output Files

### Directory Structure

```
abstract_compare/
├── screen_papers.py          # Main script
├── requirements.txt          # Dependencies
├── README.md                 # This file
└── intermediate/             # Generated during execution
    ├── screening.log         # Detailed log file
    ├── abstract_cache.json   # Cached abstracts
    ├── temp_dois.bib         # Temporary DOI file
    ├── library_*.bib         # Individual library files
    └── merged_library.bib    # Merged intermediate library
```

### Log File

The script generates a detailed log file at `intermediate/screening.log`:

```
2025-10-13 10:15:23 - INFO - Starting Paper Screening Workflow
2025-10-13 10:15:23 - INFO - Parsing BibTeX file: references.bib
2025-10-13 10:15:23 - INFO - Found 10 entries in references.bib
2025-10-13 10:15:23 - INFO - Extracted 10 DOIs from 10 entries
...
```

## Examples

### Example 1: Basic Screening

Screen papers with default settings:

```bash
python screen_papers.py \
  --input my_references.bib \
  --output relevant_papers.bib
```

### Example 2: High-Precision Screening

Use a higher threshold for more selective results:

```bash
python screen_papers.py \
  --input my_references.bib \
  --output highly_relevant.bib \
  --threshold 0.6 \
  --email researcher@university.edu
```

### Example 3: Generate Detailed Report

Create both .bib and CSV outputs:

```bash
python screen_papers.py \
  --input my_references.bib \
  --output relevant_papers.bib \
  --csv-report detailed_report.csv \
  --threshold 0.4
```

### Example 4: Dry Run

Test the workflow without fetching data:

```bash
python screen_papers.py \
  --input my_references.bib \
  --output test_output.bib \
  --dry-run
```

This validates file paths and configuration without making API calls.

## Error Handling

The script includes comprehensive error handling:

### Common Issues

**1. No DOIs found in input**
```
ERROR - No DOIs found in input file. Cannot proceed.
```
**Solution**: Ensure your .bib entries include `doi = {10.xxxx/xxxxx}` fields.

**2. Library building failed**
```
ERROR - Failed to build library. Cannot proceed.
```
**Solution**:
- Verify the `scienceguid` conda environment exists
- Check that `../build_libraries_from_dois.py` is accessible
- Ensure you have network access to PubMed

**3. Too many missing abstracts**
```
WARNING - Warning: More than 50% of abstracts are missing!
```
**Solution**:
- Provide your email via `--email` for better API access
- Check your network connection
- Verify that papers are indexed in PubMed

**4. No papers passed threshold**
```
WARNING - No papers passed the threshold!
```
**Solution**:
- Lower the threshold (try 0.2 or 0.15)
- Verify that your reference papers are relevant to the domain
- Check that abstracts were successfully retrieved

## Performance Considerations

### Speed

Typical runtime for different dataset sizes:

| Reference Papers | Candidate Papers | Estimated Time |
|-----------------|------------------|----------------|
| 5 | 50 | ~5 minutes |
| 10 | 100 | ~10 minutes |
| 20 | 500 | ~30 minutes |
| 50 | 1000 | ~60 minutes |

**Bottlenecks**:
1. Library building (depends on PubMed response time)
2. Abstract retrieval (rate-limited to 3-10 requests/second)
3. BM25 computation (fast, even for large datasets)

### Optimization Tips

1. **Use email for PubMed**: Provides 10 req/sec instead of 3
   ```bash
   --email your.email@domain.com
   ```

2. **Enable abstract caching** (enabled by default):
   - Reuses abstracts from previous runs
   - Significantly speeds up subsequent runs

3. **Adjust intermediate directory** for faster I/O:
   ```bash
   --intermediate-dir /tmp/screening
   ```

4. **Run in batches** for very large datasets:
   - Split input .bib into smaller files
   - Process separately and merge results

## Troubleshooting

### Script doesn't start

**Check Python version**:
```bash
python --version  # Should be 3.8+
```

**Verify dependencies**:
```bash
pip list | grep -E "bibtexparser|biopython|rank-bm25|nltk|pandas|tqdm"
```

### Conda environment issues

**Check if environment exists**:
```bash
conda env list | grep scienceguid
```

**Create environment if missing**:
```bash
conda create -n scienceguid python=3.10
conda activate scienceguid
pip install -r ../requirements.txt  # Install parent project dependencies
```

### PubMed API errors

**Rate limiting**:
- Wait 5 minutes and retry
- Use `--email` to get higher rate limits

**Connection errors**:
- Check network connectivity
- Verify firewall settings
- Try using a VPN if institutional access is required

### Memory issues

For very large datasets (>10,000 papers):

1. **Increase Python memory limit**:
   ```bash
   ulimit -v unlimited
   ```

2. **Process in smaller batches**

3. **Disable caching** if cache file is too large:
   ```bash
   --no-cache
   ```

## Advanced Usage

### Custom Text Preprocessing

Modify the `preprocess_text()` method in `screen_papers.py`:

```python
def preprocess_text(self, text: str) -> List[str]:
    # Add stemming
    from nltk.stem import PorterStemmer
    stemmer = PorterStemmer()

    tokens = word_tokenize(text.lower())
    stop_words = set(stopwords.words('english'))
    tokens = [stemmer.stem(t) for t in tokens
              if t.isalpha() and t not in stop_words]
    return tokens
```

### Custom BM25 Parameters

Modify the BM25 initialization (default: k1=1.5, b=0.75):

```python
# In compute_bm25_scores() method
bm25 = BM25Okapi(tokenized_corpus, k1=2.0, b=0.5)
```

**Parameters**:
- `k1`: Term frequency saturation (higher = more weight to TF)
- `b`: Length normalization (0=no norm, 1=full norm)

### Parallel Processing

For very large datasets, consider processing in parallel:

```bash
# Split input into multiple files
split -l 10 input.bib input_part_

# Process in parallel
for file in input_part_*; do
  python screen_papers.py --input $file --output ${file}_out.bib &
done
wait

# Merge results
cat input_part_*_out.bib > final_output.bib
```

## Citation

If you use this tool in your research, please cite:

```bibtex
@software{paper_screener_2025,
  title = {BM25-based Paper Relevance Screening Tool},
  author = {Your Name},
  year = {2025},
  url = {https://github.com/yourrepo/ScienceGuide}
}
```

## License

This tool is part of the ScienceGuide project. See the main repository for license information.

## Contributing

Contributions are welcome! Areas for improvement:

- [ ] Support for additional databases (Scopus, Web of Science)
- [ ] Alternative similarity algorithms (TF-IDF, Doc2Vec)
- [ ] Web interface for easier usage
- [ ] Batch processing optimization
- [ ] Integration with reference managers (Zotero, Mendeley)

## Support

For issues and questions:

1. Check the [Troubleshooting](#troubleshooting) section
2. Review the log file at `intermediate/screening.log`
3. Open an issue on GitHub with:
   - Error messages
   - Log file contents
   - Your command-line arguments
   - Python and package versions

## Acknowledgments

This tool uses:
- [BM25 algorithm](https://en.wikipedia.org/wiki/Okapi_BM25) for text similarity
- [PubMed E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/) for abstract retrieval
- [rank-bm25](https://github.com/dorianbrown/rank_bm25) Python implementation
