#!/usr/bin/env python3
"""
Script to build PubMed libraries from DOIs found in thaiz.bib using PubmedExplorer.
This script extracts DOIs from the BibTeX file and uses the build_library_from_doi method
to create comprehensive libraries for each DOI.
"""

import os
import argparse
import re
from pathlib import Path

from pubmed_explorer import PubmedExplorer

def extract_dois_from_bibtex(bibtex_file):
    """
    Extract DOIs from a BibTeX file.
    
    Args:
        bibtex_file (str): Path to the BibTeX file
        
    Returns:
        list: List of DOIs found in the file
    """
    dois = []
    
    with open(bibtex_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Pattern to match DOI entries in BibTeX format
    doi_pattern = r'doi\s*=\s*\{([^}]+)\}'
    matches = re.findall(doi_pattern, content, re.IGNORECASE)
    
    for match in matches:
        # Clean up the DOI (remove any extra whitespace)
        doi = match.strip()
        if doi:
            dois.append(doi)
    
    return dois

def build_libraries_from_dois(dois, output_dir, email="your.email@example.com"):
    """
    Build PubMed libraries from a list of DOIs using PubmedExplorer.
    
    Args:
        dois (list): List of DOIs to process
        output_dir (str): Directory to save the output files
        email (str): Email address for NCBI API requests
    """
    print(f"Found {len(dois)} DOIs to process")
    print(f"Output directory: {output_dir}")
    print("="*60)
    
    successful_builds = []
    failed_builds = []
    
    for i, doi in enumerate(dois, 1):
        print(f"\n[{i}/{len(dois)}] Processing DOI: {doi}")
        print("-" * 50)
        
        try:
            # Create PubmedExplorer instance for this DOI
            with PubmedExplorer(
                email=email,
                doi=doi,
                get_citations=True,
                get_references=True,
                also_viewed=True
            ) as explorer:
                
                # Generate a filename based on the DOI
                safe_doi = doi.replace("/", "_").replace(".", "_")
                filename = f"library_{safe_doi}.bib"
                filepath = os.path.join(output_dir, filename)
                
                print(f"Building library for DOI: {doi}")
                print(f"Output file: {filename}")
                
                # Build the library
                result_file = explorer.build_library(filename=filepath)
                
                if result_file:
                    successful_builds.append((doi, result_file))
                    print(f"✅ Successfully created library: {result_file}")
                else:
                    failed_builds.append((doi, "Library building returned None"))
                    print(f"❌ Failed to create library for DOI: {doi}")
                    
        except Exception as e:
            failed_builds.append((doi, str(e)))
            print(f"❌ Error processing DOI {doi}: {e}")
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total DOIs processed: {len(dois)}")
    print(f"Successful builds: {len(successful_builds)}")
    print(f"Failed builds: {len(failed_builds)}")
    
    if successful_builds:
        print(f"\n✅ Successfully created libraries:")
        for doi, filename in successful_builds:
            print(f"  - {doi} → {filename}")
    
    if failed_builds:
        print(f"\n❌ Failed to create libraries:")
        for doi, error in failed_builds:
            print(f"  - {doi}: {error}")
    
    return successful_builds, failed_builds

def main():
    """Parse CLI args and orchestrate library building."""
    parser = argparse.ArgumentParser(
        description="Build PubMed libraries from DOIs (citations + references + related papers).",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--bibtex",
        type=Path,
        help="Path to a .bib file; all DOIs inside are processed.",
    )
    src.add_argument(
        "--doi",
        nargs="+",
        help="One or more DOIs to process directly.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="Directory for library_*.bib outputs (default: current working directory).",
    )
    parser.add_argument(
        "--email",
        help="Email for NCBI E-utilities. Falls back to PERSPICACITE_NCBI_EMAIL env var if unset.",
    )
    args = parser.parse_args()

    email = args.email or os.environ.get("PERSPICACITE_NCBI_EMAIL")
    if not email:
        parser.error("NCBI email required. Set --email or export PERSPICACITE_NCBI_EMAIL.")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print("PubMed Library Builder from DOIs")
    print("=" * 60)
    print(f"Output directory: {output_dir}")
    print(f"Email for NCBI API: {email}")

    if args.bibtex:
        if not args.bibtex.exists():
            print(f"❌ Error: BibTeX file not found: {args.bibtex}")
            return
        print(f"\nExtracting DOIs from {args.bibtex}...")
        dois = extract_dois_from_bibtex(str(args.bibtex))
        if not dois:
            print("❌ No DOIs found in the BibTeX file")
            return
        print(f"✅ Extracted {len(dois)} DOIs:")
    else:
        dois = args.doi
        print(f"\n✅ Got {len(dois)} DOI(s) from --doi:")

    for i, doi in enumerate(dois, 1):
        print(f"  {i}. {doi}")

    print("\nStarting library building process...")
    successful_builds, failed_builds = build_libraries_from_dois(dois, str(output_dir), email)

    summary_file = output_dir / "library_building_summary.txt"
    with open(summary_file, "w") as f:
        f.write("PubMed Library Building Summary\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Total DOIs processed: {len(dois)}\n")
        f.write(f"Successful builds: {len(successful_builds)}\n")
        f.write(f"Failed builds: {len(failed_builds)}\n\n")
        if successful_builds:
            f.write("Successful builds:\n")
            for doi, filename in successful_builds:
                f.write(f"  - {doi} → {filename}\n")
            f.write("\n")
        if failed_builds:
            f.write("Failed builds:\n")
            for doi, error in failed_builds:
                f.write(f"  - {doi}: {error}\n")

    print(f"\n📄 Summary saved to: {summary_file}")
    print("\n🎉 Library building process completed!")

if __name__ == "__main__":
    main()

