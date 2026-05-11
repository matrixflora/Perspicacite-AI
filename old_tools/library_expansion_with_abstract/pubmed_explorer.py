import requests
import xml.etree.ElementTree as ET

class PubmedExplorer:
    def __init__(self, email, doi=None, keywords=None, hybrid_mode=False, get_citations=True, get_references=True, also_viewed=True):
        """
        Initialize PubmedExplorer for DOI-based, keyword-based, or hybrid search.
        
        Args:
            email (str): Your email address (required by NCBI)
            doi (str, optional): DOI to explore (for DOI-based mode)
            keywords (str or list, optional): Keywords to search (for keyword/hybrid mode)
            hybrid_mode (bool): If True with keywords, enables hybrid mode (keyword search + PMID exploration)
            get_citations (bool): Whether to fetch citing articles
            get_references (bool): Whether to fetch referenced articles
            also_viewed (bool): Whether to fetch related articles
        """
        if not doi and not keywords:
            raise ValueError("Either 'doi' or 'keywords' must be provided")
        if doi and keywords:
            raise ValueError("Provide either 'doi' or 'keywords', not both")
        if hybrid_mode and not keywords:
            raise ValueError("Hybrid mode requires keywords to be provided")
            
        self.email = email
        self.tool_name = 'PubmedExplorer'
        self.http = self._configure_http_session()
        self.get_citations = get_citations
        self.get_references = get_references
        self.also_viewed = also_viewed
        self.doi = doi
        self.keywords = self._process_keywords(keywords) if keywords else None
        self.hybrid_mode = hybrid_mode
        
        # Determine mode
        if doi:
            self.mode = 'doi'
        elif hybrid_mode:
            self.mode = 'hybrid'
        else:
            self.mode = 'keywords'

    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.http.close()

    def _configure_http_session(self):
        print("Configuring HTTP session...")
        headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
        retry_strategy = requests.adapters.Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
        session = requests.Session()
        session.headers.update(headers)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        print("HTTP session configured.")
        return session

    def _process_keywords(self, keywords):
        """
        Process and normalize keywords for PubMed search.
        
        Args:
            keywords: str, list of strings, or list of lists
            
        Returns:
            list: Processed keywords ready for search
        """
        if isinstance(keywords, str):
            return [keywords]
        elif isinstance(keywords, list):
            processed = []
            for kw in keywords:
                if isinstance(kw, str):
                    processed.append(kw)
                elif isinstance(kw, list):
                    # Handle nested lists - join with AND
                    processed.append(" AND ".join(kw))
                else:
                    processed.append(str(kw))
            return processed
        else:
            return [str(keywords)]

    def _convert_doi_to_pmid(self):
        """
        Convert a DOI to a PMID using the NCBI E-utilities (esearch.fcgi).
        """
        print(f"Converting DOI {self.doi} to PMID...")
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {
            "db": "pubmed",
            "term": self.doi,
            "retmode": "json",
            "email": self.email,
            "tool": self.tool_name
        }
        try:
            response = self.http.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            # Parse the response to find the PMID
            #print(data["esearchresult"].get("errorlist"))
            if data["esearchresult"].get("errorlist") is not None:
                print(f"Failed to convert DOI {self.doi} to PMID")
                return None
            if 'idlist' in data['esearchresult'] and len(data['esearchresult']['idlist']) > 0:
                pmid = data['esearchresult']['idlist'][0]
                print(f"PMID found for DOI {self.doi}: {pmid}")
                return pmid
            else:
                print(f"No PMID found for DOI {self.doi}.")
                return None
        except requests.exceptions.RequestException as e:
            print(f"Failed to convert DOI {self.doi} to PMID: {e}")
            return None
        except Exception as e:
            print(f"Failed to convert DOI {self.doi} to PMID: {e}")
            return None

    def _construct_date_filter(self, year_from=None, year_to=None, date_range=None):
        """
        Construct PubMed date filter query string.
        
        Args:
            year_from (int, optional): Start year
            year_to (int, optional): End year  
            date_range (str, optional): Predefined range
            
        Returns:
            str: Date filter string for PubMed query
        """
        from datetime import datetime
        
        if date_range:
            current_year = datetime.now().year
            if date_range == "last_year":
                year_from = current_year - 1
                year_to = current_year
            elif date_range == "last_5_years":
                year_from = current_year - 5
                year_to = current_year
            elif date_range == "last_10_years":
                year_from = current_year - 10
                year_to = current_year
            else:
                print(f"Unknown date_range: {date_range}")
                return None
        
        if year_from is not None and year_to is not None:
            return f'("{year_from}"[Date - Publication] : "{year_to}"[Date - Publication])'
        elif year_from is not None:
            return f'"{year_from}"[Date - Publication] : "3000"[Date - Publication]'
        elif year_to is not None:
            return f'"1800"[Date - Publication] : "{year_to}"[Date - Publication]'
        
        return None

    def _search_pubmed_by_keywords(self, max_results=20, sort_order="relevance", 
                                 year_from=None, year_to=None, date_range=None):
        """
        Search PubMed using keywords and return a list of PMIDs.
        
        Args:
            max_results (int): Maximum number of results to return
            sort_order (str): Sort order - 'relevance', 'pub_date', 'Author', 'JournalName'
            year_from (int, optional): Start year for date filtering (e.g., 2020)
            year_to (int, optional): End year for date filtering (e.g., 2024)
            date_range (str, optional): Predefined date range - 'last_year', 'last_5_years', 'last_10_years'
        
        Returns:
            list: List of PMIDs matching the search criteria
        """
        if not self.keywords:
            print("No keywords provided for search")
            return []
            
        # Construct search query - handle different keyword formats
        if len(self.keywords) == 1:
            search_term = self.keywords[0]
        else:
            # Join multiple keywords with AND, preserving quoted phrases
            search_term = " AND ".join(self.keywords)
        
        # Add date constraints to search term
        date_filter = self._construct_date_filter(year_from, year_to, date_range)
        if date_filter:
            search_term = f"({search_term}) AND {date_filter}"
            print(f"Searching PubMed for keywords: {' AND '.join(self.keywords)} with date filter: {date_filter}")
        else:
            print(f"Searching PubMed for keywords: {search_term}")
        
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {
            "db": "pubmed",
            "term": search_term,
            "retmax": max_results,
            "retmode": "json",
            "sort": sort_order,
            "email": self.email,
            "tool": self.tool_name
        }
        
        try:
            response = self.http.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data["esearchresult"].get("errorlist") is not None:
                print(f"Error in keyword search: {data['esearchresult']['errorlist']}")
                return []
                
            pmids = data['esearchresult'].get('idlist', [])
            print(f"Found {len(pmids)} articles for keywords: {search_term}")
            
            return pmids
            
        except requests.exceptions.RequestException as e:
            print(f"Failed to search PubMed with keywords '{search_term}': {e}")
            return []
        except Exception as e:
            print(f"Failed to search PubMed with keywords '{search_term}': {e}")
            return []

    def _explore_from_pmid(self, pmid, get_citations=False, also_viewed=False, get_references=False):
        """
        Fetch all articles cited in a paper with given Pubmed ID using NCBI E-utilities.

        Parameters:
        - pmcid: PMC ID of the article whose citations are to be fetched.

        Returns:
        - List of PubMed IDs (PMIDs) of the articles cited by the given PMC article.
        """
        if get_citations:
            print(f"Fetching PubMed IDs cited PMC ID {pmid}...")
            linkname = "pubmed_pubmed_citedin"
        elif also_viewed:
            print(f"Fetching PubMed IDs frequently viewed together with PMC ID {pmid}...")
            linkname = "pubmed_pubmed_alsoviewed"
        elif get_references:
            print(f"Fetching PubMed IDs cited in PMC ID {pmid}...")
            linkname = "pubmed_pubmed_refs"
        else:
            raise ValueError("Invalid exploration type")

        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
        params = {
            "dbfrom": "pubmed",
            "linkname": linkname,  # to get PubMed IDs of articles this PMC ID cites
            "id": pmid,
            "email": self.email,  # Change to your actual email
            "tool": self.tool_name,
            "retmode": "json"
        }
        try:
            response = self.http.get(url, params=params)
            response.raise_for_status()  # This will raise an HTTPError for bad HTTP responses.
            data = response.json()

            linksets = data.get('linksets', [])
            citations = []
            for linkset in linksets:
                links = linkset.get('linksetdbs', [])
                for link in links:
                    if link['linkname'] == linkname:
                        citations.extend(link['links'])
            if get_citations:
                print(f"{len(citations)} PubMed IDs for citations of PubMed ID {pmid}: {sorted(citations)}")
            elif also_viewed:
                print(f"{len(citations)} PubMed IDs for frequently viewed together with PubMed ID {pmid}: {sorted(citations)}")
            elif get_references:
                print(f"{len(citations)} PubMed IDs for references of PubMed ID {pmid}: {sorted(citations)}")
            return citations
        except requests.exceptions.RequestException as e:
            if get_citations:
                print(f"Failed to fetch PubMed IDs for citations of PubMed ID {pmid}: {e}")
            elif also_viewed:
                print(f"Failed to fetch PubMed IDs frequently viewed together with PubMed ID {pmid}: {e}")
            elif get_references:
                print(f"Failed to fetch PubMed IDs for references of PubMed ID {pmid}: {e}")
            else:
                print(f"Failed to fetch PubMed IDs for exploration of PubMed ID {pmid}: {e}")
            return []

    def _fetch_metadata_from_pubmed(self, pmids):
        print(f"Fetching metadata for {len(pmids)} PubMed IDs...")
        print(f"PMIDs: {pmids}")
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json"
        }
        try:
            response = self.http.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            result = data['result']
            print(f"Found metadata for {len(result)} PubMed IDs.")
            # Add abstracts to the metadata
            print(f"Fetching abstracts for {len(pmids)} articles...")
            for i, pmid in enumerate(pmids, 1):
                if str(pmid) in result:
                    print(f"  Getting abstract {i}/{len(pmids)} for PMID {pmid}")
                    abstract = self.get_abstract(pubmed_id=pmid)
                    if abstract:
                        result[str(pmid)]['abstract'] = abstract
                        print(f"    ✓ Abstract retrieved ({len(abstract)} chars)")
                    else:
                        print(f"    ✗ No abstract available")

            #print(f"Metadata: {result}")
            return [result[str(pmid)] for pmid in pmids if str(pmid) in result]
        except requests.exceptions.RequestException as e:
            print(f"Failed to fetch metadata for PubMed IDs: {e}")
        return []

    def _fetch_metadata_with_citations(self, pmids):
        """
        Optimized method to fetch both metadata and citation counts in a single batch.
        Reduces API calls by combining operations.
        """
        print(f"Fetching metadata and citations for {len(pmids)} PubMed IDs...")
        print(f"PMIDs: {pmids}")
        
        # Step 1: Fetch metadata (same as before)
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json"
        }
        
        try:
            response = self.http.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            result = data['result']
            
            print(f"Found metadata for {len(result)} PubMed IDs.")
            
            # Step 2: Batch fetch citation counts for all PMIDs
            print(f"Fetching citation counts for {len(pmids)} articles...")
            citation_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
            citation_params = {
                "dbfrom": "pubmed",
                "linkname": "pubmed_pubmed_citedin",
                "id": ",".join(pmids),
                "email": self.email,
                "tool": self.tool_name,
                "retmode": "json"
            }
            
            try:
                citation_response = self.http.get(citation_url, params=citation_params, timeout=15)
                citation_response.raise_for_status()
                
                # Handle potential JSON parsing issues in batch mode
                try:
                    citation_data = citation_response.json()
                except ValueError as json_error:
                    print(f"Warning: JSON parsing error in batch citation fetch: {json_error}")
                    raise Exception("JSON parsing failed")
                
                # Process citation data for each PMID
                linksets = citation_data.get('linksets', [])
                citation_success = 0
                
                for linkset in linksets:
                    source_pmid = linkset.get('ids', [None])[0]
                    if source_pmid and str(source_pmid) in result:
                        citation_count = 0
                        links = linkset.get('linksetdbs', [])
                        for link in links:
                            if link['linkname'] == 'pubmed_pubmed_citedin':
                                citation_count = len(link.get('links', []))
                                break
                        result[str(source_pmid)]['citation_count'] = citation_count
                        citation_success += 1
                
                print(f"✅ Citation counts retrieved for {citation_success}/{len(pmids)} papers in batch")
                
                # For papers not in linksets, set citation count to 0
                for pmid in pmids:
                    if str(pmid) in result and 'citation_count' not in result[str(pmid)]:
                        result[str(pmid)]['citation_count'] = 0
                
            except Exception as e:
                print(f"Warning: Failed to fetch citation counts in batch: {e}")
                print(f"Falling back to individual citation fetching...")
                
                # Fall back to individual citation fetching with rate limiting
                import time
                for i, pmid in enumerate(pmids, 1):
                    if str(pmid) in result:
                        print(f"  Getting citations {i}/{len(pmids)} for PMID {pmid}")
                        try:
                            citation_count = self._get_citation_count(pmid)
                            result[str(pmid)]['citation_count'] = citation_count
                        except:
                            result[str(pmid)]['citation_count'] = 0
                        
                        # Rate limiting: small delay between individual requests
                        if i < len(pmids):
                            time.sleep(0.5)
            
            # Step 3: Fetch abstracts
            print(f"Fetching abstracts for {len(pmids)} articles...")
            for i, pmid in enumerate(pmids, 1):
                if str(pmid) in result:
                    print(f"  Getting abstract {i}/{len(pmids)} for PMID {pmid}")
                    abstract = self.get_abstract(pubmed_id=pmid)
                    if abstract:
                        result[str(pmid)]['abstract'] = abstract
                        print(f"    ✓ Abstract retrieved ({len(abstract)} chars)")
                    else:
                        print(f"    ✗ No abstract available")
            
            return [result[str(pmid)] for pmid in pmids if str(pmid) in result]
            
        except requests.exceptions.RequestException as e:
            print(f"Failed to fetch metadata and citations for PubMed IDs: {e}")
        return []

    def _convert_to_bibtex(self, metadata):
        print(f"Converting metadata to BibTeX: {metadata.get('uid')}")
        template = """@article{{{id},
        author = {{{author}}},
        title = {{{title}}},
        journal = {{{journal}}},
        year = {year},
        volume = {{{volume}}},
        number = {{{number}}},
        pages = {{{pages}}},
        issn = {{{issn}}},
        doi = {{{doi}}},
        url = {{{url}}},
        abstract = {{{abstract}}},
        citations = {{{citations}}},
    }}"""
        doi = ''
        for articleid in metadata.get('articleids', []):
            if articleid.get('idtype') == 'doi':
                doi = articleid.get('value')
                break
        if not doi:
            doi = metadata.get('elocationid', '').replace('doi: ', '')
        authors = ' and '.join([author['name'] for author in metadata.get('authors', [])])
        issn_list = [id['value'] for id in metadata.get('articleids', []) if id['idtype'] == 'issn']
        issn = ', '.join(issn_list)

        # Clean abstract for BibTeX (escape special characters and handle line breaks)
        abstract = metadata.get('abstract', '')
        if abstract:
            # Escape curly braces and handle special characters for BibTeX
            abstract = abstract.replace('{', '\\{').replace('}', '\\}')
            abstract = abstract.replace('%', '\\%')
            # Replace line breaks with spaces
            abstract = ' '.join(abstract.split())
        
        return template.format(
            id=metadata.get('uid'),
            author=authors,
            title=metadata.get('title', ''),
            journal=metadata.get('fulljournalname', ''),
            year=metadata.get('pubdate', '').split()[0],
            volume=metadata.get('volume', ''),
            number=metadata.get('issue', ''),
            pages=metadata.get('pages', ''),
            issn=issn,
            doi=doi,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{metadata['uid']}/",
            abstract=abstract,
            citations=metadata.get('citation_count', 'N/A'),
        )
    
    def get_abstract(self, doi=None, pubmed_id=None):

        """
        Fetch the abstract for a given DOI or Pubmed id.
        """

        if pubmed_id == None:
            print(f"Fetching abstract for DOI: {doi}")
            pubmed_id = self._convert_doi_to_pmid()

        if pubmed_id:
            url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            params = {
                "db": "pubmed",
                "id": pubmed_id,
                "retmode": "xml"
            }
            response = self.http.get(url, params=params)
            if response.status_code == 200:
                abstract =  self._parse_abstract_from_xml(response.content)
                if abstract:
                    return abstract
                else:
                    return None
            else:
                print(f"Failed to fetch abstract for PubMed ID: {pubmed_id}")
                return None
        else:
            print(f"No PubMed ID found for DOI: {doi}")
            return None
    
    def _parse_abstract_from_xml(self, xml_data):
        """
        Parse the abstract from the XML data.
        """
        root = ET.fromstring(xml_data)
        abstract = root.find(".//AbstractText")
        if abstract is not None:
            return abstract.text
        else:
            print("Metadata available on PubMed does not contain the abstract")
            return None

    def _filter_by_citations(self, metadata_list, max_papers=20, min_citations=5):
        """
        Filter papers by citation count to keep only high-impact ones.
        
        Args:
            metadata_list (list): List of metadata dictionaries
            max_papers (int): Maximum number of papers to keep
            min_citations (int): Minimum citation threshold
            
        Returns:
            list: Filtered metadata list sorted by citation count
        """
        print(f"Filtering {len(metadata_list)} papers by citation count...")
        
        # Filter by citation counts (already retrieved in optimized metadata fetch)
        papers_with_citations = []
        for meta in metadata_list:
            citations = meta.get('citation_count', 0)
            if citations >= min_citations:
                papers_with_citations.append(meta)
            elif min_citations == 0:  # If no minimum threshold, keep it
                papers_with_citations.append(meta)
        
        # Sort by citation count (descending)
        papers_with_citations.sort(key=lambda x: x.get('citation_count', 0), reverse=True)
        
        # Take top papers
        filtered_papers = papers_with_citations[:max_papers]
        
        print(f"  Kept {len(filtered_papers)} high-impact papers (≥{min_citations} citations)")
        if filtered_papers:
            top_citations = [p.get('citation_count', 0) for p in filtered_papers[:5]]
            print(f"  Top citation counts: {top_citations}")
        
        return filtered_papers

    def _get_citation_count(self, pmid):
        """
        Get citation count for a PMID using PubMed's citation data with robust error handling.
        
        Args:
            pmid (str): PubMed ID
            
        Returns:
            int: Number of citations
        """
        import time
        max_retries = 3
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                # Use elink to get citing articles
                url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
                params = {
                    "dbfrom": "pubmed",
                    "linkname": "pubmed_pubmed_citedin",
                    "id": pmid,
                    "email": self.email,
                    "tool": self.tool_name,
                    "retmode": "json"
                }
                
                response = self.http.get(url, params=params, timeout=10)
                response.raise_for_status()
                
                # Handle potential JSON parsing issues
                try:
                    data = response.json()
                except ValueError as json_error:
                    print(f"    JSON parsing error for PMID {pmid}: {json_error}")
                    if attempt < max_retries - 1:
                        time.sleep(base_delay * (2 ** attempt))
                        continue
                    return 0
                
                # Count citing articles
                citation_count = 0
                linksets = data.get('linksets', [])
                for linkset in linksets:
                    links = linkset.get('linksetdbs', [])
                    for link in links:
                        if link['linkname'] == 'pubmed_pubmed_citedin':
                            citation_count = len(link.get('links', []))
                            break
                
                return citation_count
                
            except requests.exceptions.RequestException as e:
                print(f"    Network error for PMID {pmid} (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"    Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    print(f"    Failed to get citation count for PMID {pmid} after {max_retries} attempts")
                    return 0
            except Exception as e:
                print(f"    Unexpected error for PMID {pmid}: {e}")
                return 0
        
        return 0

    def build_library(self, filename=None, **kwargs):
        """
        Build a BibTeX library. Routes to appropriate method based on mode.
        
        Args:
            filename (str, optional): Output filename
            **kwargs: Additional arguments passed to specific build methods
        """
        if self.mode == 'keywords':
            return self.build_library_from_keywords(filename=filename, **kwargs)
        elif self.mode == 'hybrid':
            return self.build_library_hybrid(filename=filename, **kwargs)
        else:  # doi mode
            return self._build_library_from_doi(filename=filename)
    
    def _build_library_from_doi(self, filename=None):
        """Original DOI-based library building functionality."""
        print(f"Processing PubMed entry for DOI: {self.doi}")

        pmid = self._convert_doi_to_pmid()  # Convert DOI to PMID first
        if not pmid:
            print(f"Unable to retrieve PMID for DOI: {self.doi}")
            return

        # Fetch metadata using PMID
        metadata_list = self._fetch_metadata_from_pubmed([pmid])
        if not metadata_list:
            print("No metadata could be fetched.")
            return

        # If filename is not provided, create one based on the article title
        if filename is None:
            article_title = metadata_list[0].get('title', 'NoTitleFound')
            print(f"Article title: {article_title}")
            sanitized_title = ''.join([c for c in article_title if c.isalnum() or c in '-_ '])[:20].strip().replace(' ', '_')
            print(f"Sanitized title: {sanitized_title}")
            filename = f"{sanitized_title}.bib"

        if not filename.endswith('.bib'):
            filename += '.bib'

        # Continue processing citations as before
        #full_pmcid = self._convert_doi_to_pmcid(doi)
        
        pmids = [pmid]
        pmids_to_explore = [pmid]
        rounds = 1

        for i in range(rounds):
            _obtained_pmids = []
            print("Explore with the following pmids:", pmids_to_explore)
            for pmid in pmids_to_explore:
                if self.get_references:
                    _obtained_pmids.extend(self._explore_from_pmid(pmid, get_references=True))
                if self.get_citations:
                    _obtained_pmids.extend(self._explore_from_pmid(pmid, get_citations=True))
                if self.also_viewed:
                    _obtained_pmids.extend(self._explore_from_pmid(pmid, also_viewed=True))
            pmids_to_explore = _obtained_pmids
            pmids.extend(_obtained_pmids)

        if pmids:
           metadata = self._fetch_metadata_from_pubmed(pmids)
           if metadata:
               metadata_list.extend(metadata)

        if not metadata_list:
            print("No additional metadata could be fetched.")
            return

        # Save all the metadata to a BibTeX file
        with open(filename, 'w') as file:
            for metadata in metadata_list:
                bibtex_entry = self._convert_to_bibtex(metadata)
                file.write(bibtex_entry + "\n\n")
        print(f"BibTeX entries saved to {filename}")

        return filename

    def build_library_from_keywords(self, filename=None, max_results=20, sort_order="relevance", 
                                  explore_depth=1, year_from=None, year_to=None, date_range=None):
        """
        Build a BibTeX library from keyword search results.
        
        Args:
            filename (str, optional): Output filename. Auto-generated if not provided.
            max_results (int): Maximum number of initial search results
            sort_order (str): Sort order for search results
            explore_depth (int): Number of rounds to explore citations/references
            year_from (int, optional): Start year for date filtering (e.g., 2020)
            year_to (int, optional): End year for date filtering (e.g., 2024)
            date_range (str, optional): Predefined date range - 'last_year', 'last_5_years', 'last_10_years'
        
        Returns:
            str: Filename of the created BibTeX file
        """
        if self.mode != 'keywords':
            print("This method is only available in keyword search mode")
            return None
            
        print(f"Building library from keywords: {', '.join(self.keywords)}")
        
        # Search for articles using keywords
        initial_pmids = self._search_pubmed_by_keywords(max_results, sort_order, year_from, year_to, date_range)
        if not initial_pmids:
            print("No articles found for the given keywords")
            return None
        
        # Fetch metadata for initial results
        metadata_list = self._fetch_metadata_from_pubmed(initial_pmids)
        if not metadata_list:
            print("No metadata could be fetched for initial results")
            return None
        
        # Generate filename if not provided
        if filename is None:
            keywords_str = "_".join([kw.replace(" ", "_") for kw in self.keywords])[:30]
            filename = f"keyword_search_{keywords_str}.bib"
            
        if not filename.endswith('.bib'):
            filename += '.bib'
        
        # Explore citations and references if requested
        all_pmids = initial_pmids.copy()
        pmids_to_explore = initial_pmids.copy()
        
        for round_num in range(explore_depth):
            print(f"Exploration round {round_num + 1}/{explore_depth}")
            new_pmids = []
            
            for pmid in pmids_to_explore:
                if self.get_references:
                    refs = self._explore_from_pmid(pmid, get_references=True)
                    new_pmids.extend(refs)
                if self.get_citations:
                    cites = self._explore_from_pmid(pmid, get_citations=True)
                    new_pmids.extend(cites)
                if self.also_viewed:
                    viewed = self._explore_from_pmid(pmid, also_viewed=True)
                    new_pmids.extend(viewed)
            
            # Remove duplicates and already processed PMIDs
            new_pmids = list(set(new_pmids) - set(all_pmids))
            if not new_pmids:
                print(f"No new PMIDs found in round {round_num + 1}")
                break
                
            print(f"Found {len(new_pmids)} new PMIDs in round {round_num + 1}")
            
            # Fetch metadata for new PMIDs
            if new_pmids:
                new_metadata = self._fetch_metadata_from_pubmed(new_pmids)
                if new_metadata:
                    metadata_list.extend(new_metadata)
                    
            all_pmids.extend(new_pmids)
            pmids_to_explore = new_pmids[:50]  # Limit exploration to avoid overwhelming results
        
        print(f"Total articles collected: {len(metadata_list)}")
        
        # Save all metadata to BibTeX file
        with open(filename, 'w') as file:
            file.write(f"% BibTeX library generated from keyword search: {', '.join(self.keywords)}\n")
            file.write(f"% Total entries: {len(metadata_list)}\n")
            file.write(f"% Generated by PubmedExplorer\n\n")
            
            for metadata in metadata_list:
                bibtex_entry = self._convert_to_bibtex(metadata)
                file.write(bibtex_entry + "\n\n")
                
        print(f"BibTeX library saved to {filename}")
        return filename

    def build_library_hybrid(self, filename=None, initial_max_results=10, pmid_max_results=5, 
                           sort_order="relevance", explore_depth=2, pmid_selection_strategy="top",
                           year_from=None, year_to=None, date_range=None):
        """
        Build a BibTeX library using hybrid approach: keyword search + individual PMID exploration.
        
        This method first searches for articles using keywords, then treats each found article
        as a seed for individual exploration (like DOI mode) to find their citations, references,
        and related articles.
        
        Args:
            filename (str, optional): Output filename. Auto-generated if not provided.
            initial_max_results (int): Maximum number of initial keyword search results
            pmid_max_results (int): Maximum related articles to fetch per PMID
            sort_order (str): Sort order for initial keyword search
            explore_depth (int): Exploration depth for each individual PMID
            pmid_selection_strategy (str): How to select PMIDs for exploration ('top', 'random', 'all')
            year_from (int, optional): Start year for date filtering (e.g., 2020)
            year_to (int, optional): End year for date filtering (e.g., 2024)
            date_range (str, optional): Predefined date range - 'last_year', 'last_5_years', 'last_10_years'
        
        Returns:
            str: Filename of the created BibTeX file
        """
        if self.mode != 'hybrid':
            print("This method is only available in hybrid mode")
            return None
            
        print(f"Building hybrid library from keywords: {', '.join(self.keywords)}")
        print(f"Strategy: Keyword search → Individual PMID exploration")
        
        # Phase 1: Initial keyword search
        print("\n=== Phase 1: Initial Keyword Search ===")
        initial_pmids = self._search_pubmed_by_keywords(initial_max_results, sort_order, year_from, year_to, date_range)
        if not initial_pmids:
            print("No articles found for the given keywords")
            return None
        
        # Fetch metadata for initial results
        initial_metadata = self._fetch_metadata_from_pubmed(initial_pmids)
        if not initial_metadata:
            print("No metadata could be fetched for initial results")
            return None
        
        print(f"Found {len(initial_pmids)} seed articles from keyword search")
        
        # Generate filename if not provided
        if filename is None:
            keywords_str = "_".join([kw.replace(" ", "_") for kw in self.keywords])[:25]
            filename = f"hybrid_{keywords_str}.bib"
            
        if not filename.endswith('.bib'):
            filename += '.bib'
        
        # Phase 2: Individual PMID exploration
        print(f"\n=== Phase 2: Individual PMID Exploration ===")
        print(f"Exploring each of {len(initial_pmids)} articles individually")
        
        # Select PMIDs for exploration based on strategy
        if pmid_selection_strategy == "top":
            pmids_to_explore = initial_pmids[:min(len(initial_pmids), 10)]  # Limit to prevent overwhelm
        elif pmid_selection_strategy == "random":
            import random
            pmids_to_explore = random.sample(initial_pmids, min(len(initial_pmids), 10))
        else:  # "all"
            pmids_to_explore = initial_pmids
        
        all_metadata = initial_metadata.copy()
        all_pmids = set(initial_pmids)
        
        for i, seed_pmid in enumerate(pmids_to_explore, 1):
            print(f"\nExploring seed article {i}/{len(pmids_to_explore)}: PMID {seed_pmid}")
            
            # Get seed article title for reference
            seed_title = "Unknown"
            for meta in initial_metadata:
                if meta.get('uid') == seed_pmid:
                    seed_title = meta.get('title', 'Unknown')[:50] + "..."
                    break
            print(f"  Title: {seed_title}")
            
            # Explore this PMID like in DOI mode but with limited depth
            seed_related_pmids = []
            current_pmids = [seed_pmid]
            
            for depth in range(explore_depth):
                print(f"    Depth {depth + 1}/{explore_depth}")
                next_pmids = []
                
                for current_pmid in current_pmids[:pmid_max_results]:  # Limit per round
                    if self.get_references:
                        refs = self._explore_from_pmid(current_pmid, get_references=True)
                        next_pmids.extend(refs[:pmid_max_results])
                    if self.get_citations:
                        cites = self._explore_from_pmid(current_pmid, get_citations=True)
                        next_pmids.extend(cites[:pmid_max_results])
                    if self.also_viewed:
                        viewed = self._explore_from_pmid(current_pmid, also_viewed=True)
                        next_pmids.extend(viewed[:pmid_max_results])
                
                # Remove duplicates and already processed
                next_pmids = list(set(next_pmids) - all_pmids)
                if not next_pmids:
                    print(f"      No new articles found at depth {depth + 1}")
                    break
                
                print(f"      Found {len(next_pmids)} new articles")
                seed_related_pmids.extend(next_pmids)
                all_pmids.update(next_pmids)
                current_pmids = next_pmids[:pmid_max_results]  # Limit for next iteration
            
            # Fetch metadata for articles found from this seed
            if seed_related_pmids:
                print(f"  Fetching metadata for {len(seed_related_pmids)} related articles")
                seed_metadata = self._fetch_metadata_from_pubmed(seed_related_pmids)
                if seed_metadata:
                    all_metadata.extend(seed_metadata)
        
        print(f"\n=== Final Results ===")
        print(f"Initial keyword search articles: {len(initial_pmids)}")
        print(f"Total articles after exploration: {len(all_metadata)}")
        print(f"Articles added through exploration: {len(all_metadata) - len(initial_pmids)}")
        
        # Save all metadata to BibTeX file
        with open(filename, 'w') as file:
            file.write(f"% Hybrid BibTeX library generated from keywords: {', '.join(self.keywords)}\n")
            file.write(f"% Initial keyword search results: {len(initial_pmids)}\n")
            file.write(f"% Total entries after PMID exploration: {len(all_metadata)}\n")
            file.write(f"% Exploration strategy: {pmid_selection_strategy}\n")
            file.write(f"% Exploration depth: {explore_depth}\n")
            file.write(f"% Generated by PubmedExplorer (Hybrid Mode)\n\n")
            
            for metadata in all_metadata:
                bibtex_entry = self._convert_to_bibtex(metadata)
                file.write(bibtex_entry + "\n\n")
                
        print(f"Hybrid library saved to {filename}")
        return filename

    def build_library_also_viewed(self, keyword_lists, filename=None, max_results_per_keyword=10, 
                                 sort_order="relevance", explore_depth=2, deduplicate=True,
                                 year_from=None, year_to=None, date_range=None,
                                 use_citation_filter=False, citation_threshold=5, max_papers_per_round=20):
        """
        Build a BibTeX library using ONLY PubMed's 'also viewed' functionality for exploration.
        
        This method focuses on discovering papers through user viewing patterns rather than 
        citation networks, potentially finding more diverse and interdisciplinary papers.
        
        Args:
            keyword_lists (list): List of keyword strings or lists for initial searches
            filename (str, optional): Output filename
            max_results_per_keyword (int): Max results per keyword search
            sort_order (str): Sort order for searches
            explore_depth (int): Exploration depth using only 'also viewed'
            deduplicate (bool): Remove duplicate PMIDs across searches
            year_from/year_to/date_range: Date filtering options
            use_citation_filter (bool): Filter papers by citation count for quality
            citation_threshold (int): Minimum citations required to keep a paper
            max_papers_per_round (int): Maximum papers to keep per round after filtering
            
        Returns:
            str: Filename of the created BibTeX file
        """
        if self.mode not in ['keywords', 'hybrid']:
            print("This method requires keyword or hybrid mode")
            return None
        
        print(f"🔍 Building library using ONLY 'also viewed' exploration")
        print(f"📚 Keywords: {len(keyword_lists)} searches")
        print(f"🔗 Exploration strategy: PubMed 'also viewed' patterns")
        print(f"📊 Depth: {explore_depth} levels")
        print()
        
        original_keywords = self.keywords  # Store original
        all_pmids = set()
        all_metadata = []
        
        for i, keywords in enumerate(keyword_lists, 1):
            print(f"🔍 Search {i}/{len(keyword_lists)}: Processing keywords")
            
            # Process keywords for this search
            if isinstance(keywords, list):
                processed_keywords = []
                for kw in keywords:
                    if isinstance(kw, str) and ' ' in kw and not (kw.startswith('"') and kw.endswith('"')):
                        processed_keywords.append(f'"{kw}"')
                    else:
                        processed_keywords.append(str(kw))
                self.keywords = [" AND ".join(processed_keywords)]
                search_display = " AND ".join(processed_keywords)
            else:
                self.keywords = self._process_keywords(keywords)
                search_display = str(keywords)
            
            print(f"  Query: {search_display}")
            
            # Initial keyword search
            pmids = self._search_pubmed_by_keywords(
                max_results_per_keyword, sort_order, year_from, year_to, date_range
            )
            
            if pmids:
                print(f"  Initial search found: {len(pmids)} articles")
                
                # Deduplication check
                if deduplicate:
                    new_pmids = set(pmids) - all_pmids
                    if new_pmids:
                        all_pmids.update(new_pmids)
                        pmids_to_fetch = list(new_pmids)
                        print(f"  {len(new_pmids)} new unique articles (after deduplication)")
                    else:
                        pmids_to_fetch = pmids
                        
                    if pmids_to_fetch:
                        # Use optimized method that fetches metadata + citations together
                        if use_citation_filter:
                            metadata = self._fetch_metadata_with_citations(pmids_to_fetch)
                            if metadata:
                                filtered_metadata = self._filter_by_citations(
                                    metadata, max_papers_per_round, citation_threshold
                                )
                                all_metadata.extend(filtered_metadata)
                                seed_pmids = [m['uid'] for m in filtered_metadata]
                        else:
                            metadata = self._fetch_metadata_from_pubmed(pmids_to_fetch)
                            if metadata:
                                all_metadata.extend(metadata)
                                seed_pmids = [m['uid'] for m in metadata]
                else:
                    print(f"  0 new articles (all duplicates)")
                    continue
                    
                # 🔗 ALSO VIEWED exploration (multiple depths)
                if explore_depth > 0 and seed_pmids:
                    print(f"  🔗 Starting 'also viewed' exploration from {len(seed_pmids)} seed papers...")
                    
                    explored_pmids = set(seed_pmids)
                    
                    for depth in range(explore_depth):
                        print(f"    📊 Also viewed depth {depth + 1}/{explore_depth}")
                        
                        round_pmids = set()
                        
                        # Explore 'also viewed' for each paper in current round
                        for pmid in explored_pmids:
                            # Use only 'also viewed' functionality
                            also_viewed_pmids = self._explore_from_pmid(
                                pmid, 
                                get_citations=False,      # Disable citations
                                get_references=False,     # Disable references  
                                also_viewed=True          # ONLY also viewed
                            )
                            round_pmids.update(also_viewed_pmids)
                        
                        if round_pmids:
                            print(f"      Found {len(round_pmids)} 'also viewed' articles")
                            
                            # Remove already processed PMIDs
                            new_round_pmids = round_pmids - all_pmids if deduplicate else round_pmids
                            if new_round_pmids:
                                print(f"      Found {len(new_round_pmids)} new articles from 'also viewed'")
                                all_pmids.update(new_round_pmids)
                                
                                # Fetch metadata for exploration results (optimized)
                                if use_citation_filter:
                                    exp_metadata = self._fetch_metadata_with_citations(list(new_round_pmids))
                                    if exp_metadata:
                                        filtered_exp_metadata = self._filter_by_citations(
                                            exp_metadata, max_papers_per_round//2, citation_threshold//2
                                        )
                                        all_metadata.extend(filtered_exp_metadata)
                                        explored_pmids = {m['uid'] for m in filtered_exp_metadata}
                                else:
                                    exp_metadata = self._fetch_metadata_from_pubmed(list(new_round_pmids))
                                    if exp_metadata:
                                        all_metadata.extend(exp_metadata)
                                        explored_pmids = {m['uid'] for m in exp_metadata}
                            else:
                                print(f"      No new articles found at depth {depth + 1}")
                                break
                        else:
                            print(f"      No 'also viewed' articles found at depth {depth + 1}")
                            break
            else:
                print(f"  No articles found for this search")
        
        # Restore original keywords
        self.keywords = original_keywords
        
        print(f"\n📊 Also Viewed Exploration Complete:")
        print(f"  Total unique articles: {len(all_pmids)}")
        print(f"  Articles with metadata: {len(all_metadata)}")
        
        # Save to BibTeX file
        if filename is None:
            safe_keywords = "_".join([str(kw).replace(" ", "_")[:15] for kw in keyword_lists[:3]])
            filename = f"also_viewed_{safe_keywords}.bib"
        
        with open(filename, 'w', encoding='utf-8') as file:
            for metadata in all_metadata:
                bibtex_entry = self._convert_to_bibtex(metadata)
                file.write(bibtex_entry + "\n\n")
                
        print(f"🔗 Also Viewed library saved to {filename}")
        return filename
        
    def build_library_from_multiple_keywords(self, keyword_lists, filename=None, max_results_per_keyword=10, 
                                           sort_order="relevance", explore_depth=1, deduplicate=True,
                                           year_from=None, year_to=None, date_range=None,
                                           use_citation_filter=False, citation_threshold=5, max_papers_per_round=20):
        """
        Build a BibTeX library from multiple keyword searches with deduplication.
        
        Args:
            keyword_lists (list): List of keyword strings or lists
                Examples: 
                - ["coral", "machine learning"] 
                - ['"coral metabolomics"', '"machine learning"']
                - [["coral", "reef"], ["AI", "algorithm"]]
            filename (str, optional): Output filename
            max_results_per_keyword (int): Max results per keyword search
            sort_order (str): Sort order for searches
            explore_depth (int): Exploration depth for each search
            deduplicate (bool): Remove duplicate PMIDs across searches
            year_from/year_to/date_range: Date filtering options
            use_citation_filter (bool): Filter papers by citation count for quality
            citation_threshold (int): Minimum citations required to keep a paper
            max_papers_per_round (int): Maximum papers to keep per round after filtering
            
        Returns:
            str: Filename of the created BibTeX file
        """
        if self.mode not in ['keywords', 'hybrid']:
            print("This method requires keyword or hybrid mode")
            return None
        
        print(f"Building library from {len(keyword_lists)} keyword searches")
        print(f"Keywords: {keyword_lists}")
        
        all_pmids = set()  # Use set for automatic deduplication
        all_metadata = []
        search_results = {}  # Track which PMIDs came from which keywords
        
        # Generate filename if not provided
        if filename is None:
            keywords_preview = "_".join([str(kw)[:15].replace(" ", "_").replace('"', '') for kw in keyword_lists[:3]])
            if len(keyword_lists) > 3:
                keywords_preview += f"_and_{len(keyword_lists)-3}_more"
            filename = f"multi_keyword_{keywords_preview}.bib"
        
        if not filename.endswith('.bib'):
            filename += '.bib'
        
        # Process each keyword search
        for i, keywords in enumerate(keyword_lists, 1):
            print(f"\n=== Keyword Search {i}/{len(keyword_lists)}: {keywords} ===")
            
            # Temporarily set keywords for this search
            original_keywords = self.keywords
            
            # Handle the keywords correctly:
            # If it's a list like ["coral", "machine learning"], join with AND
            # If it's a string, use as-is
            if isinstance(keywords, list):
                # Join list items with AND, wrap phrases in quotes if they contain spaces
                processed_keywords = []
                for kw in keywords:
                    if isinstance(kw, str) and ' ' in kw and not (kw.startswith('"') and kw.endswith('"')):
                        processed_keywords.append(f'"{kw}"')  # Quote phrases with spaces
                    else:
                        processed_keywords.append(str(kw))
                self.keywords = [" AND ".join(processed_keywords)]
            else:
                self.keywords = self._process_keywords(keywords)
            
            try:
                # Search for articles
                pmids = self._search_pubmed_by_keywords(
                    max_results_per_keyword, sort_order, year_from, year_to, date_range
                )
                
                if pmids:
                    print(f"Found {len(pmids)} articles for keywords: {keywords}")
                    search_results[str(keywords)] = pmids
                    
                    # Add to global set (automatic deduplication)
                    new_pmids = set(pmids) - all_pmids if deduplicate else set(pmids)
                    all_pmids.update(pmids)
                    
                    if new_pmids or not deduplicate:
                        # Fetch metadata for new articles
                        if deduplicate:
                            pmids_to_fetch = list(new_pmids)
                            print(f"  {len(new_pmids)} new unique articles (after deduplication)")
                        else:
                            pmids_to_fetch = pmids
                            
                        if pmids_to_fetch:
                            # Use optimized method that fetches metadata + citations together
                            if use_citation_filter:
                                metadata = self._fetch_metadata_with_citations(pmids_to_fetch)
                                if metadata:
                                    filtered_metadata = self._filter_by_citations(
                                        metadata, max_papers_per_round, citation_threshold
                                    )
                                    all_metadata.extend(filtered_metadata)
                            else:
                                metadata = self._fetch_metadata_from_pubmed(pmids_to_fetch)
                                if metadata:
                                    all_metadata.extend(metadata)
                    else:
                        print(f"  0 new articles (all duplicates)")
                        
                    # Optional: Explore citations/references for this keyword search
                    if explore_depth > 0:
                        print(f"  Exploring citations/references (depth: {explore_depth})")
                        explored_pmids = set(pmids)
                        
                        for depth in range(explore_depth):
                            print(f"    Exploration round {depth + 1}/{explore_depth}")
                            round_pmids = []
                            
                            for pmid in list(explored_pmids)[:10]:  # Limit to prevent overwhelm
                                if self.get_references:
                                    refs = self._explore_from_pmid(pmid, get_references=True)
                                    round_pmids.extend(refs[:5])  # Limit per PMID
                                if self.get_citations:
                                    cites = self._explore_from_pmid(pmid, get_citations=True)
                                    round_pmids.extend(cites[:5])
                                if self.also_viewed:
                                    viewed = self._explore_from_pmid(pmid, also_viewed=True)
                                    round_pmids.extend(viewed[:5])
                            
                            # Remove duplicates and already processed
                            new_round_pmids = set(round_pmids) - all_pmids if deduplicate else set(round_pmids)
                            if new_round_pmids:
                                print(f"      Found {len(new_round_pmids)} new articles from exploration")
                                all_pmids.update(new_round_pmids)
                                
                                # Fetch metadata for exploration results (optimized)
                                if use_citation_filter:
                                    exp_metadata = self._fetch_metadata_with_citations(list(new_round_pmids))
                                    if exp_metadata:
                                        filtered_exp_metadata = self._filter_by_citations(
                                            exp_metadata, max_papers_per_round//2, citation_threshold//2
                                        )
                                        all_metadata.extend(filtered_exp_metadata)
                                else:
                                    exp_metadata = self._fetch_metadata_from_pubmed(list(new_round_pmids))
                                    if exp_metadata:
                                        all_metadata.extend(exp_metadata)
                                    
                                explored_pmids = new_round_pmids
                            else:
                                print(f"      No new articles found at depth {depth + 1}")
                                break
                else:
                    print(f"No articles found for keywords: {keywords}")
                    
            except Exception as e:
                print(f"Error processing keywords '{keywords}': {e}")
                
            finally:
                # Restore original keywords
                self.keywords = original_keywords
        
        print(f"\n=== Final Results ===")
        print(f"Total unique articles collected: {len(all_metadata)}")
        print(f"Articles from {len(search_results)} keyword searches")
        
        if deduplicate:
            print(f"Duplicates removed: {len(all_pmids) - len(all_metadata)} PMIDs were duplicates")
        
        # Save to BibTeX file
        if all_metadata:
            with open(filename, 'w') as file:
                file.write(f"% Multi-keyword BibTeX library\n")
                file.write(f"% Generated from {len(keyword_lists)} keyword searches\n")
                file.write(f"% Keywords: {', '.join([str(kw) for kw in keyword_lists])}\n")
                file.write(f"% Total unique entries: {len(all_metadata)}\n")
                file.write(f"% Deduplication: {'enabled' if deduplicate else 'disabled'}\n")
                if year_from or year_to or date_range:
                    file.write(f"% Date filter: {year_from}-{year_to if year_to else 'present'}")
                    if date_range:
                        file.write(f" ({date_range})")
                    file.write("\n")
                file.write(f"% Generated by PubmedExplorer (Multi-keyword Mode)\n\n")
                
                # Add search breakdown
                file.write("% Search breakdown:\n")
                for kw, pmids in search_results.items():
                    file.write(f"% {kw}: {len(pmids)} articles\n")
                file.write("\n")
                
                for metadata in all_metadata:
                    bibtex_entry = self._convert_to_bibtex(metadata)
                    file.write(bibtex_entry + "\n\n")
                    
            print(f"Multi-keyword library saved to {filename}")
            return filename
        else:
            print("No articles found across all keyword searches")
            return None


# Usage Examples
if __name__ == "__main__":
    """
    Example usage of PubmedExplorer for both DOI-based and keyword-based searches.
    """
    
    # Example 1: DOI-based search
    print("=== DOI-based Library Building ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com", 
            doi="10.1038/nature12373",
            get_citations=True,
            get_references=True,
            also_viewed=False
        ) as explorer:
            filename = explorer.build_library()
            print(f"DOI-based library created: {filename}")
    except Exception as e:
        print(f"DOI example failed: {e}")
    
    print("\n" + "="*50 + "\n")
    
    # Example 2: Single keyword search
    print("=== Single Keyword Search ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com",
            keywords="machine learning",
            get_citations=False,
            get_references=True,
            also_viewed=False
        ) as explorer:
            filename = explorer.build_library(
                max_results=10,
                explore_depth=1,
                sort_order="relevance"
            )
            print(f"Single keyword library created: {filename}")
    except Exception as e:
        print(f"Single keyword example failed: {e}")
    
    print("\n" + "="*50 + "\n")
    
    # Example 3: Multiple keywords search
    print("=== Multiple Keywords Search ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com",
            keywords=["deep learning", "neural networks", "computer vision"],
            get_citations=True,
            get_references=True,
            also_viewed=True
        ) as explorer:
            filename = explorer.build_library(
                filename="deep_learning_cv_library.bib",
                max_results=15,
                explore_depth=2,
                sort_order="pub_date"
            )
            print(f"Multiple keywords library created: {filename}")
    except Exception as e:
        print(f"Multiple keywords example failed: {e}")
    
    print("\n" + "="*50 + "\n")
    
    # Example 4: Direct keyword search without exploration
    print("=== Direct Keyword Search (No Exploration) ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com",
            keywords=["CRISPR", "gene editing"],
            get_citations=False,
            get_references=False,
            also_viewed=False
        ) as explorer:
            filename = explorer.build_library(
                max_results=20,
                explore_depth=0
            )
            print(f"Direct search library created: {filename}")
    except Exception as e:
        print(f"Direct search example failed: {e}")
    
    print("\n" + "="*50 + "\n")
    
    # Example 5: Hybrid mode - keyword search + individual PMID exploration
    print("=== Hybrid Mode (Keyword + PMID Exploration) ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com",
            keywords=["artificial intelligence", "medical diagnosis"],
            hybrid_mode=True,
            get_citations=True,
            get_references=True,
            also_viewed=False
        ) as explorer:
            filename = explorer.build_library(
                filename="ai_medical_hybrid.bib",
                initial_max_results=8,
                pmid_max_results=3,
                explore_depth=2,
                pmid_selection_strategy="top"
            )
            print(f"Hybrid library created: {filename}")
    except Exception as e:
        print(f"Hybrid example failed: {e}")
    
    print("\n" + "="*50 + "\n")
    
    # Example 6: Hybrid mode with random selection strategy
    print("=== Hybrid Mode (Random Selection) ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com",
            keywords="quantum computing",
            hybrid_mode=True,
            get_citations=True,
            get_references=True,
            also_viewed=True
        ) as explorer:
            filename = explorer.build_library(
                initial_max_results=12,
                pmid_max_results=4,
                explore_depth=1,
                pmid_selection_strategy="random"
            )
            print(f"Random hybrid library created: {filename}")
    except Exception as e:
        print(f"Random hybrid example failed: {e}")
    
    print("\n" + "="*50 + "\n")
    
    # Example 7: Year filtering - Recent articles only
    print("=== Year Filtering - Recent Articles (2020-2024) ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com",
            keywords=["COVID-19", "vaccine"],
            get_citations=False,
            get_references=True,
            also_viewed=False
        ) as explorer:
            filename = explorer.build_library(
                filename="covid_vaccine_recent.bib",
                max_results=15,
                year_from=2020,
                year_to=2024,
                sort_order="pub_date"
            )
            print(f"Recent articles library created: {filename}")
    except Exception as e:
        print(f"Year filtering example failed: {e}")
    
    print("\n" + "="*50 + "\n")
    
    # Example 8: Predefined date range filtering
    print("=== Predefined Date Range - Last 5 Years ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com",
            keywords="climate change coral reefs",
            hybrid_mode=True,
            get_citations=True,
            get_references=True,
            also_viewed=False
        ) as explorer:
            filename = explorer.build_library(
                filename="climate_coral_last5years.bib",
                initial_max_results=10,
                pmid_max_results=3,
                explore_depth=1,
                date_range="last_5_years"
            )
            print(f"Last 5 years library created: {filename}")
    except Exception as e:
        print(f"Date range example failed: {e}")
    
    print("\n" + "="*50 + "\n")
    
    # Example 9: Multiple keywords with deduplication
    print("=== Multiple Keywords with Deduplication ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com",
            keywords="dummy",  # Will be overridden
            get_citations=False,
            get_references=True,
            also_viewed=False
        ) as explorer:
            filename = explorer.build_library_from_multiple_keywords(
                keyword_lists=["coral", "machine learning", '"artificial intelligence"'],
                filename="multi_keyword_demo.bib",
                max_results_per_keyword=5,
                explore_depth=1,
                deduplicate=True,
                year_from=2020
            )
            print(f"Multi-keyword library created: {filename}")
    except Exception as e:
        print(f"Multi-keyword example failed: {e}")
    
    print("\n" + "="*50 + "\n")
    
    # Example 10: Complex multi-keyword search
    print("=== Complex Multi-Keyword Search ===")
    try:
        with PubmedExplorer(
            email="your.email@example.com",
            keywords="dummy",  # Will be overridden
            get_citations=True,
            get_references=True,
            also_viewed=False
        ) as explorer:
            filename = explorer.build_library_from_multiple_keywords(
                keyword_lists=[
                    '"coral metabolomics"',
                    '"machine learning" AND "healthcare"',
                    ["climate", "change"],
                    "CRISPR"
                ],
                filename="complex_multi_search.bib",
                max_results_per_keyword=8,
                explore_depth=1,
                deduplicate=True,
                date_range="last_5_years"
            )
            print(f"Complex multi-search library created: {filename}")
    except Exception as e:
        print(f"Complex multi-search example failed: {e}")
    
    print("\nAll examples completed!")
    
    print("\n=== Usage Notes ===")
    print("1. Replace 'your.email@example.com' with your actual email address")
    print("2. Three modes available:")
    print("   - DOI mode: doi='10.1234/example'")
    print("   - Keyword mode: keywords='machine learning'")
    print("   - Hybrid mode: keywords='AI' + hybrid_mode=True")
    print("3. For keyword searches, you can use:")
    print("   - Single keyword: keywords='machine learning'")
    print("   - Multiple keywords: keywords=['AI', 'robotics', 'automation']")
    print("4. Available sort orders: 'relevance', 'pub_date', 'Author', 'JournalName'")
    print("5. explore_depth controls how many rounds of citation/reference exploration to perform")
    print("6. Set get_citations, get_references, also_viewed to control what related articles to fetch")
    print("7. Hybrid mode parameters:")
    print("   - initial_max_results: Number of seed articles from keyword search")
    print("   - pmid_max_results: Max related articles per seed article")
    print("   - pmid_selection_strategy: 'top', 'random', or 'all'")
    print("8. Year filtering options:")
    print("   - year_from/year_to: Specific year range (e.g., year_from=2020, year_to=2024)")
    print("   - date_range: Predefined ranges ('last_year', 'last_5_years', 'last_10_years')")
    print("9. Multi-keyword search options:")
    print("   - Simple list: ['coral', 'machine learning']")
    print("   - Quoted phrases: ['\"coral metabolomics\"', '\"machine learning\"']")
    print("   - Complex queries: ['\"AI\" AND \"healthcare\"', ['climate', 'change']]")
    print("   - Automatic deduplication by PMID across all searches")
    print("10. Hybrid mode combines the best of both worlds: targeted keyword search + deep exploration")
    print("11. Year filtering works with all modes for temporal relevance")
    print("12. Multi-keyword mode builds comprehensive libraries from diverse search terms")