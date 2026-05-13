"""Unit tests for the Obsidian vault export builder."""
import io
import zipfile

from perspicacite.integrations.obsidian import build_obsidian_vault


def test_vault_structure_and_wikilinks():
    kb = {"name": "default", "embedding_model": "text-embed",
          "paper_count": 2, "chunk_count": 5}
    papers = [
        {"doi": "10.1/a", "title": "Paper A", "year": 2024, "journal": "J",
         "authors": ["X Y"], "content_type": "full_text", "content_source": "pmc",
         "abstract": "abs A"},
        {"doi": "10.1/b", "title": "Paper B", "year": 2020, "journal": "K",
         "authors": ["Z"], "content_type": "abstract", "content_source": "openalex",
         "abstract": "abs B"},
    ]
    conversations = [
        {"id": "conv-1", "title": "Q on microbiome",
         "messages": [
             {"role": "user", "content": "What is X?"},
             {"role": "assistant", "content": "Per (10.1/a) ...",
              "sources": [{"doi": "10.1/a", "title": "Paper A"}]},
         ]},
    ]
    blob = build_obsidian_vault(kb=kb, papers=papers, conversations=conversations)
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    assert any("default/Papers/10-1-a.md" in n for n in names)
    assert any("default/Papers/10-1-b.md" in n for n in names)
    assert any(n.startswith("default/Conversations/") and n.endswith(".md") for n in names)
    assert any(n.endswith("default/Index.md") for n in names)
    a = z.read(next(n for n in names if "default/Papers/10-1-a.md" in n)).decode()
    assert a.startswith("---")
    assert "doi: 10.1/a" in a
    conv_md = z.read(next(n for n in names if "Conversations/" in n and n.endswith(".md"))).decode()
    # The cited DOI in assistant content should become a wikilink to the paper note
    assert "[[10-1-a]]" in conv_md


def test_vault_handles_paper_without_doi():
    kb = {"name": "kb"}
    papers = [{"title": "Untitled paper"}]
    blob = build_obsidian_vault(kb=kb, papers=papers, conversations=[])
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    assert any(n.endswith("kb/Index.md") for n in names)
    # Some paper note should exist (slugified)
    assert any(n.startswith("kb/Papers/") and n.endswith(".md") for n in names)


def test_vault_filename_sanitization():
    kb = {"name": "test-kb"}
    papers = [{"doi": "10.1234/abc:def/ghi", "title": "Special / Chars: Test", "year": 2024}]
    blob = build_obsidian_vault(kb=kb, papers=papers, conversations=[])
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    # Only safe ASCII slug + hyphens + .md
    paper_files = [n for n in names if "Papers/" in n and n.endswith(".md")]
    assert paper_files
    for n in paper_files:
        slug = n.split("/")[-1].rsplit(".md", 1)[0]
        assert all(ch.isalnum() or ch == "-" for ch in slug), slug
