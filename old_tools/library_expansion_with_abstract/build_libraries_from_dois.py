#!/usr/bin/env python3
"""
Script to build PubMed libraries from DOIs found in thaiz.bib using PubmedExplorer.
This script extracts DOIs from the BibTeX file and uses the build_library_from_doi method
to create comprehensive libraries for each DOI.
"""

import os
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
    """Main function to orchestrate the library building process."""
    
    # Configuration
    script_dir = Path(__file__).parent
    bibtex_file = script_dir / "thaiz.bib"
    output_dir = script_dir
    
    # Email for NCBI API - you should replace this with your actual email
    email = "your.email@example.com"
    
    print("PubMed Library Builder from DOIs")
    print("="*60)
    print(f"BibTeX file: {bibtex_file}")
    print(f"Output directory: {output_dir}")
    print(f"Email for NCBI API: {email}")
    
    # Check if BibTeX file exists
    if not bibtex_file.exists():
        print(f"❌ Error: BibTeX file not found: {bibtex_file}")
        return
    
    # Extract DOIs from BibTeX file
    print(f"\nExtracting DOIs from {bibtex_file}...")
    dois = extract_dois_from_bibtex(bibtex_file)
    
    if not dois:
        print("❌ No DOIs found in the BibTeX file")
        return
    
    print(f"✅ Extracted {len(dois)} DOIs:")
    for i, doi in enumerate(dois, 1):
        print(f"  {i}. {doi}")
    
    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Build libraries from DOIs
    print(f"\nStarting library building process...")
    successful_builds, failed_builds = build_libraries_from_dois(dois, str(output_dir), email)
    
    # Create a summary file
    summary_file = output_dir / "library_building_summary.txt"
    with open(summary_file, 'w') as f:
        f.write("PubMed Library Building Summary\n")
        f.write("="*50 + "\n\n")
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

