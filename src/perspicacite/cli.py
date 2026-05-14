"""Command-line interface for Perspicacité v2."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import click

from perspicacite import __version__
from perspicacite.config import load_config
from perspicacite.logging import get_logger, setup_logging

logger = get_logger("perspicacite.cli")


@click.group()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Path to config file",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging",
)
@click.pass_context
def cli(ctx: click.Context, config: Path | None, verbose: bool) -> None:
    """Perspicacité v2 - AI-powered scientific literature research assistant."""
    # Ensure context dict exists
    ctx.ensure_object(dict)

    # Load config
    try:
        cfg = load_config(str(config) if config else None)
    except Exception as e:
        click.echo(f"Error loading config: {e}", err=True)
        sys.exit(1)

    # Override log level if verbose
    if verbose:
        cfg.logging.level = "DEBUG"

    # Setup logging. `serve` is the long-running daemon and conventionally
    # logs to stdout (redirectable via `> logs/serve.out`). Every other
    # CLI subcommand routes structured logs to stderr so the user's
    # actual output (list-kb table, --json blob, query answer, etc.)
    # stays clean on stdout.
    log_stream = sys.stdout if ctx.invoked_subcommand == "serve" else sys.stderr
    setup_logging(cfg.logging, stream=log_stream)
    logger.info("perspicacite_started", version=__version__)

    # Store config in context
    ctx.obj["config"] = cfg


@cli.command()
@click.option(
    "--host",
    default=None,
    help="Server host (default: from config)",
)
@click.option(
    "--port",
    "-p",
    type=int,
    default=None,
    help="Server port (default: from config)",
)
@click.option(
    "--reload",
    is_flag=True,
    help="Enable auto-reload (development)",
)
@click.option(
    "--no-mcp",
    is_flag=True,
    help="Disable MCP server",
)
@click.option(
    "--no-ui",
    is_flag=True,
    help="Headless mode (API only)",
)
@click.pass_context
def serve(
    ctx: click.Context,
    host: str | None,
    port: int | None,
    reload: bool,
    no_mcp: bool,
    no_ui: bool,
) -> None:
    """Start the Perspicacité server."""
    config = ctx.obj["config"]

    # Override with CLI args
    if host:
        config.server.host = host
    if port:
        config.server.port = port
    if reload:
        config.server.reload = True
    if no_mcp:
        config.mcp.enabled = False

    logger.info(
        "starting_server",
        host=config.server.host,
        port=config.server.port,
        mcp_enabled=config.mcp.enabled,
    )

    click.echo(f"🚀 Starting Perspicacité v{__version__}")
    click.echo(f"   Server: http://{config.server.host}:{config.server.port}")
    if config.mcp.enabled:
        click.echo(f"   MCP: http://{config.mcp.host}:{config.mcp.port}")

    # Hand the resolved config path to the web app via env var
    import os

    os.environ["PERSPICACITE_CONFIG"] = str(config)

    import uvicorn
    from perspicacite.web import app

    if config.mcp.enabled:
        # Start MCP server alongside FastAPI
        _start_mcp_and_web(config, app)
    else:
        uvicorn.run(
            app,
            host=config.server.host,
            port=config.server.port,
            reload=config.server.reload,
        )


@cli.command()
@click.argument("name")
@click.option(
    "--description",
    "-d",
    help="Knowledge base description",
)
@click.option(
    "--from-bibtex",
    "-b",
    type=click.Path(exists=True, path_type=Path),
    help="Create from BibTeX file (.bib)",
)
@click.option(
    "--session-db",
    type=click.Path(path_type=Path),
    default=None,
    help="SQLite DB for KB metadata (default: data/perspicacite.db, same as web app)",
)
@click.option(
    "--chroma-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Chroma persist directory (default: from config database.chroma_path)",
)
@click.pass_context
def create_kb(
    ctx: click.Context,
    name: str,
    description: str | None,
    from_bibtex: Path | None,
    session_db: Path | None,
    chroma_dir: Path | None,
) -> None:
    """Create a new knowledge base (from BibTeX when --from-bibtex is set)."""
    config = ctx.obj["config"]

    if not from_bibtex:
        # Empty-KB creation: register the metadata + provision the Chroma
        # collection. Papers can be added later via `add-to-kb` or via
        # POST /api/kb/{name}/dois/async.
        from perspicacite.web.state import AppState
        from perspicacite.models.kb import KnowledgeBase, chroma_collection_name_for_kb

        async def _create_empty() -> dict[str, Any]:
            state = AppState()
            await state.initialize()
            existing = await state.session_store.get_kb_metadata(name)
            if existing is not None:
                raise FileExistsError(f"KB '{name}' already exists")
            kb = KnowledgeBase(
                name=name,
                description=description,
                collection_name=chroma_collection_name_for_kb(name),
                embedding_model=config.knowledge_base.embedding_model,
            )
            await state.vector_store.create_collection(kb.collection_name)
            await state.session_store.save_kb_metadata(kb)
            return {
                "name": kb.name,
                "collection_name": kb.collection_name,
                "embedding_model": kb.embedding_model,
            }

        try:
            result = asyncio.run(_create_empty())
        except FileExistsError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        except ValueError as e:
            # pydantic name-pattern violation, etc.
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        click.echo("\n✅ Empty knowledge base created")
        click.echo(f"   Name: {result['name']}")
        click.echo(f"   Chroma collection: {result['collection_name']}")
        click.echo(f"   Embedding model: {result['embedding_model']}")
        click.echo(
            "\nAdd papers with:\n"
            f"   perspicacite add-to-kb {result['name']} --from-bibtex refs.bib\n"
            f"   curl -X POST http://localhost:8000/api/kb/{result['name']}/dois/async \\\n"
            "        -H 'Content-Type: application/json' -d '{\"dois\":[\"10.…\"]}'"
        )
        return

    session_db = session_db or Path("data/perspicacite.db")
    chroma_dir = chroma_dir or config.database.chroma_path

    click.echo(f"Creating knowledge base '{name}' from {from_bibtex}...")
    click.echo(f"  Session DB: {session_db}")
    click.echo(f"  Chroma: {chroma_dir}")
    click.echo(f"  Embedding model (from config): {config.knowledge_base.embedding_model}")

    try:
        result = asyncio.run(
            _create_kb_from_bibtex(
                config=config,
                kb_name=name,
                bib_path=from_bibtex,
                description=description,
                session_db=session_db,
                chroma_dir=chroma_dir,
            )
        )
    except FileExistsError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("create_kb_failed", error=str(e))
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo("\n✅ Knowledge base created")
    click.echo(f"   Name (sanitized): {result['name']}")
    click.echo(f"   Chroma collection: {result['collection_name']}")
    click.echo(f"   Papers: {result['papers']}, chunks: {result['chunks_added']}")
    st = result["pdf_stats"]
    click.echo(
        f"   PDF download: attempted={st['attempted']} success={st['success']} "
        f"failed={st['failed']} no_doi={st['skipped_no_doi']}"
    )


@cli.command("add-to-kb")
@click.argument("name")
@click.option(
    "--from-bibtex",
    "-b",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="BibTeX file to import (.bib)",
)
@click.option(
    "--session-db",
    type=click.Path(path_type=Path),
    default=None,
    help="SQLite DB for KB metadata (default: data/perspicacite.db)",
)
@click.option(
    "--chroma-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Chroma persist directory (default: from config database.chroma_path)",
)
@click.pass_context
def add_to_kb(
    ctx: click.Context,
    name: str,
    from_bibtex: Path,
    session_db: Path | None,
    chroma_dir: Path | None,
) -> None:
    """Add papers from a BibTeX file to an existing knowledge base."""
    config = ctx.obj["config"]

    session_db = session_db or Path("data/perspicacite.db")
    chroma_dir = chroma_dir or config.database.chroma_path

    click.echo(f"Adding papers from {from_bibtex} to knowledge base '{name}'...")

    try:
        result = asyncio.run(
            _add_bibtex_to_existing_kb(
                config=config,
                kb_name=name,
                bib_path=from_bibtex,
                session_db=session_db,
                chroma_dir=chroma_dir,
            )
        )
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("add_to_kb_failed", error=str(e))
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(f"\n  Papers added: {result['new_papers']}, new chunks: {result['chunks_added']}")
    st = result["pdf_stats"]
    click.echo(
        f"  PDF download: attempted={st['attempted']} success={st['success']} "
        f"failed={st['failed']} no_doi={st['skipped_no_doi']}"
    )
    click.echo(f"  KB total: {result['total_papers']} papers, {result['total_chunks']} chunks")


@cli.command()
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Emit JSON instead of a formatted table.")
@click.pass_context
def list_kb(ctx: click.Context, as_json: bool) -> None:
    """List all knowledge bases (name, paper count, chunk count, embedding model)."""
    import asyncio
    from perspicacite.web.state import AppState

    async def _run() -> None:
        state = AppState()
        await state.initialize()
        kbs = await state.session_store.list_kbs()
        if as_json:
            import json as _json
            click.echo(_json.dumps(
                [
                    {
                        "name": k.name,
                        "description": k.description,
                        "paper_count": k.paper_count,
                        "chunk_count": k.chunk_count,
                        "embedding_model": k.embedding_model,
                        "created_at": k.created_at.isoformat() if k.created_at else None,
                    }
                    for k in kbs
                ],
                indent=2,
                default=str,
            ))
            return
        click.echo("📚 Knowledge Bases:")
        if not kbs:
            click.echo("  (none yet — create one with `perspicacite create-kb …`)")
            return
        # Sort by paper count desc, then name
        kbs_sorted = sorted(kbs, key=lambda k: (-(k.paper_count or 0), k.name))
        # Compute column widths so the table stays aligned for long KB names
        name_w = max(4, max(len(k.name) for k in kbs_sorted))
        click.echo(
            f"  {'NAME':<{name_w}}  {'PAPERS':>7}  {'CHUNKS':>7}  EMBEDDING MODEL"
        )
        for k in kbs_sorted:
            click.echo(
                f"  {k.name:<{name_w}}  "
                f"{(k.paper_count or 0):>7}  "
                f"{(k.chunk_count or 0):>7}  "
                f"{k.embedding_model or '?'}"
            )

    asyncio.run(_run())


@cli.command()
@click.argument("query")
@click.option(
    "--kb",
    "-k",
    default="default",
    help="Knowledge base to query",
)
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["basic", "advanced", "profound", "contradiction"]),
    default="basic",
    help="RAG mode. agentic + literature_survey aren't supported by the CLI ask path.",
)
@click.option(
    "--provider",
    "-p",
    default="anthropic",
    help="LLM provider",
)
@click.option(
    "--model",
    default=None,
    help="LLM model",
)
@click.pass_context
def query(
    ctx: click.Context,
    query: str,
    kb: str,
    mode: str,
    provider: str,
    model: str | None,
) -> None:
    """Query a knowledge base."""
    config = ctx.obj["config"]

    click.echo(f"🔍 Querying '{kb}' with {mode} mode...")
    click.echo(f"   Query: {query}")
    click.echo(f"   Provider: {provider}")

    # Run async query
    asyncio.run(_run_query(config, query, kb, mode, provider, model))


@cli.command("ingest-local")
@click.option("--kb", required=True, help="Target KB name")
@click.option(
    "--path",
    "paths",
    required=True,
    multiple=True,
    type=click.Path(path_type=Path),
    help="File or directory; can repeat",
)
@click.option("--recursive/--no-recursive", default=True)
@click.pass_context
def ingest_local(
    ctx: click.Context,
    kb: str,
    paths: tuple[Path, ...],
    recursive: bool,
) -> None:
    """Ingest local files/directories into a KB (no server needed)."""
    from perspicacite.integrations.local_docs import ingest_local_documents
    from perspicacite.web.state import AppState

    async def _run() -> None:
        state = AppState()
        await state.initialize()

        class _Reg:
            async def publish(self, jid, ev):
                pass

            async def finish(self, jid, res):
                self._res = res

            async def fail(self, jid, err):
                self._err = err

        reg = _Reg()
        result = await ingest_local_documents(
            kb_name=kb,
            paths=list(paths),
            app_state=state,
            registry=reg,
            job_id="cli",
            recursive=recursive,
        )
        click.echo(f"Done: {result}")

    asyncio.run(_run())


def _start_mcp_and_web(config, app) -> None:
    """Start MCP server and web server on a single port."""
    import uvicorn
    from contextlib import asynccontextmanager

    # Initialize MCP state
    from perspicacite.mcp.server import mcp, mcp_state
    import asyncio

    asyncio.get_event_loop().run_until_complete(mcp_state.initialize(config))

    # Get MCP ASGI app
    mcp_app = mcp.http_app()

    # Combine web app + MCP app lifespans
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app_instance):
        async with original_lifespan(app_instance):
            async with mcp_app.lifespan(app_instance):
                yield

    app.router.lifespan_context = combined_lifespan

    # Mount MCP ASGI app — its internal routes are at /mcp
    app.mount("/", mcp_app)

    # Run single server
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        reload=config.server.reload,
    )


async def _create_kb_from_bibtex(
    config: Any,
    *,
    kb_name: str,
    bib_path: Path,
    description: str | None,
    session_db: Path,
    chroma_dir: Path,
) -> dict[str, Any]:
    from perspicacite.pipeline.bibtex_kb import create_kb_from_bibtex

    return await create_kb_from_bibtex(
        config,
        kb_name=kb_name,
        bib_path=bib_path,
        description=description,
        session_db=session_db,
        chroma_dir=chroma_dir,
    )


async def _add_bibtex_to_existing_kb(
    config: Any,
    *,
    kb_name: str,
    bib_path: Path,
    session_db: Path,
    chroma_dir: Path,
) -> dict[str, Any]:
    from perspicacite.pipeline.bibtex_kb import add_bibtex_to_existing_kb

    return await add_bibtex_to_existing_kb(
        config,
        kb_name=kb_name,
        bib_path=bib_path,
        session_db=session_db,
        chroma_dir=chroma_dir,
    )


async def _run_query(
    config: Any,
    query: str,
    kb: str,
    mode: str,
    provider: str,
    model: str | None,
) -> None:
    """Run a RAG query and print the answer + sources to stdout."""
    from perspicacite.web.state import AppState
    from perspicacite.models.rag import RAGMode, RAGRequest

    state = AppState()
    await state.initialize()

    # Verify the KB exists so we fail fast with a clear message instead
    # of letting the RAG engine spit a chroma error.
    if await state.session_store.get_kb_metadata(kb) is None:
        click.echo(f"\nError: KB '{kb}' not found. List with: perspicacite list-kb", err=True)
        sys.exit(1)

    mode_map = {
        "basic": RAGMode.BASIC,
        "advanced": RAGMode.ADVANCED,
        "profound": RAGMode.PROFOUND,
        "contradiction": RAGMode.CONTRADICTION,
    }
    rag_mode = mode_map.get(mode, RAGMode.BASIC)

    # Effective model/provider: explicit flag → config default → dataclass default.
    eff_provider = provider or config.llm.default_provider
    eff_model = model or config.llm.default_model

    request = RAGRequest(
        query=query,
        kb_name=kb,
        mode=rag_mode,
        stream=False,
        provider=eff_provider,
        model=eff_model,
    )

    # Use the same RAGEngine the web/MCP layers use.
    full_answer_parts: list[str] = []
    sources: list[dict[str, Any]] = []
    try:
        async for event in state.rag_engine.query_stream(request):
            etype = getattr(event, "event", None)
            data = getattr(event, "data", None)
            if etype == "content" and data:
                # data is a JSON envelope { "delta": "..." }
                try:
                    import json as _json
                    delta = _json.loads(data).get("delta", "")
                except Exception:
                    delta = str(data)
                if delta:
                    full_answer_parts.append(delta)
            elif etype == "source" and data:
                try:
                    import json as _json
                    s = _json.loads(data)
                    sources.append(s)
                except Exception:
                    pass
            elif etype == "error" and data:
                click.echo(f"\n❌ Error from RAG engine: {data}", err=True)
                sys.exit(1)
    except Exception as exc:
        click.echo(f"\n❌ Query failed: {exc}", err=True)
        sys.exit(1)

    answer = "".join(full_answer_parts).strip()
    click.echo("\n📝 Answer:")
    if not answer:
        click.echo("  (no answer — KB might be empty for this query)")
    else:
        click.echo(answer)
    if sources:
        click.echo("\n📎 Sources:")
        for i, s in enumerate(sources, 1):
            title = s.get("title") or s.get("doi") or "(untitled)"
            year = s.get("year")
            doi = s.get("doi")
            tag = f" ({year})" if year else ""
            doi_tag = f"  doi:{doi}" if doi else ""
            click.echo(f"  [{i}] {title}{tag}{doi_tag}")


@cli.command(name="screen-papers")
@click.option(
    "--input",
    "input_bib",
    required=True,
    type=click.Path(exists=True),
    help="Reference .bib file (defines the topic)",
)
@click.option(
    "--candidates",
    "cand_bib",
    required=True,
    type=click.Path(exists=True),
    help="Candidate .bib file to screen",
)
@click.option(
    "--output",
    "output_bib",
    required=True,
    type=click.Path(),
    help="Output .bib file of kept papers",
)
@click.option(
    "--method",
    type=click.Choice(["bm25", "llm"]),
    default="bm25",
    help="Screening method",
)
@click.option(
    "--threshold",
    type=float,
    default=0.3,
    help="Keep papers scoring >= this (0..1)",
)
@click.option(
    "--csv",
    "csv_path",
    type=click.Path(),
    default=None,
    help="Optional CSV report of all candidates + scores",
)
def screen_papers_cmd(
    input_bib: str,
    cand_bib: str,
    output_bib: str,
    method: str,
    threshold: float,
    csv_path: str | None,
) -> None:
    """Screen candidate papers for relevance to a reference set's topic (BM25)."""
    import csv as _csv

    import bibtexparser

    from perspicacite.search.screening import screen_papers

    if method == "llm":
        raise click.ClickException(
            "LLM screening from the CLI is not wired in this version; "
            "use --method bm25 (or the screen_papers MCP tool for LLM scoring)."
        )

    ref_entries = bibtexparser.loads(Path(input_bib).read_text()).entries
    cand_db = bibtexparser.loads(Path(cand_bib).read_text())
    cand_entries = cand_db.entries
    if not cand_entries:
        raise click.ClickException("No entries found in the candidates .bib file.")

    refs = [f"{e.get('title', '')} {e.get('abstract', '')}".strip() for e in ref_entries]
    cands = [
        {"title": e.get("title", ""), "abstract": e.get("abstract", ""), "_entry": e}
        for e in cand_entries
    ]
    results = screen_papers(cands, reference=refs or "", method="bm25", threshold=threshold)

    kept_entries = [r.item["_entry"] for r in results if r.kept]
    out_db = bibtexparser.bibdatabase.BibDatabase()
    out_db.entries = kept_entries
    Path(output_bib).write_text(bibtexparser.dumps(out_db))

    if csv_path:
        with open(csv_path, "w", newline="") as fh:
            w = _csv.writer(fh)
            w.writerow(["id", "title", "score", "kept"])
            for r in results:
                e = r.item["_entry"]
                w.writerow([e.get("ID", ""), r.item.get("title", ""), r.score, r.kept])

    click.echo(f"Kept {len(kept_entries)}/{len(cand_entries)} candidates -> {output_bib}")


@cli.command(name="pubmed-search")
@click.argument("query")
@click.option("--max", "max_results", type=int, default=20, help="Max results")
@click.option("--year-min", type=int, default=None, help="Earliest publication year")
@click.option("--year-max", type=int, default=None, help="Latest publication year")
@click.option(
    "--email",
    default=None,
    help="NCBI email (else taken from config: scilex.pubmed_email or pdf_download.unpaywall_email)",
)
@click.option(
    "--output",
    "output_bib",
    type=click.Path(),
    default=None,
    help="Write results to a .bib file",
)
@click.pass_context
def pubmed_search_cmd(
    ctx: click.Context,
    query: str,
    max_results: int,
    year_min: int | None,
    year_max: int | None,
    email: str | None,
    output_bib: str | None,
) -> None:
    """Deep PubMed search via NCBI Entrez."""
    import bibtexparser

    import perspicacite.search.pubmed as _pm

    cfg = ctx.obj.get("config") if isinstance(ctx.obj, dict) else None
    eff_email = (
        email
        or (getattr(getattr(cfg, "scilex", None), "pubmed_email", "") if cfg else "")
        or (getattr(getattr(cfg, "pdf_download", None), "unpaywall_email", "") if cfg else "")
        or ""
    )
    try:
        adapter = _pm.PubMedSearchAdapter(email=eff_email)
    except _pm.PubMedConfigError as e:
        raise click.ClickException(str(e)) from e

    papers = asyncio.run(
        adapter.search(query, max_results=max_results, year_min=year_min, year_max=year_max)
    )
    click.echo(f"Found {len(papers)} papers")
    for p in papers[:10]:
        click.echo(f"  - {p.year or '????'}  {(p.title or '')[:90]}")

    if output_bib:
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = [
            {
                "ENTRYTYPE": "article",
                "ID": (p.doi or f"pmid{p.metadata.get('pmid', '')}")
                .replace("/", "_")
                .replace(".", "_")
                or f"entry{i}",
                "title": p.title or "",
                "year": str(p.year or ""),
                "doi": p.doi or "",
                "journal": p.journal or "",
                "abstract": p.abstract or "",
                "author": " and ".join(a.name for a in (p.authors or [])),
            }
            for i, p in enumerate(papers)
        ]
        Path(output_bib).write_text(bibtexparser.dumps(db))
        click.echo(f"Wrote {len(papers)} entries to {output_bib}")


@cli.command("build-capsule")
@click.option("--paper", "paper_id", required=True, help="Paper ID (e.g. doi:10.1234/abc)")
@click.option("--kb", required=True, help="KB to enumerate papers from")
@click.option("--force", is_flag=True, default=False)
@click.pass_context
def build_capsule_cmd(ctx, paper_id: str, kb: str, force: bool) -> None:
    """Build (or rebuild) a per-paper capsule."""
    import asyncio
    from perspicacite.pipeline.capsule_builder import (
        build_capsule as _build,
        resolve_paper_from_metadata,
        locate_cached_pdf,
    )
    from perspicacite.web.state import AppState

    async def _run() -> None:
        state = AppState()
        await state.initialize()
        kb_meta = await state.session_store.get_kb_metadata(kb)
        if kb_meta is None:
            click.echo(f"Error: KB '{kb}' not found", err=True)
            raise SystemExit(1)
        rows = await state.vector_store.list_paper_metadata(kb_meta.collection_name)
        _norm_pid = paper_id[4:] if paper_id.startswith("doi:") else paper_id
        row = next((r for r in rows if r.get("paper_id") == _norm_pid), None)
        if row is None:
            click.echo(f"Error: paper '{paper_id}' not in KB '{kb}'", err=True)
            raise SystemExit(1)
        paper = resolve_paper_from_metadata(row)
        pdf_path = locate_cached_pdf(row)
        res = await _build(
            paper=paper, pdf_path=pdf_path,
            kb_name=kb, app_state=state, force=force,
        )
        click.echo(f"Done: {res}")
    asyncio.run(_run())


@cli.command("build-capsules")
@click.option("--kb", "kb_name", required=True, help="KB name")
@click.option("--force", is_flag=True, default=False)
@click.pass_context
def build_capsules_cmd(ctx, kb_name: str, force: bool) -> None:
    """Build capsules for every paper in a KB."""
    import asyncio
    from perspicacite.pipeline.capsule_builder import (
        build_capsule as _build,
        resolve_paper_from_metadata,
        locate_cached_pdf,
    )
    from perspicacite.web.state import AppState

    async def _run() -> None:
        state = AppState()
        await state.initialize()
        kb_meta = await state.session_store.get_kb_metadata(kb_name)
        if kb_meta is None:
            click.echo(f"Error: KB '{kb_name}' not found", err=True)
            raise SystemExit(1)
        rows = await state.vector_store.list_paper_metadata(kb_meta.collection_name)
        counts = {"built": 0, "skipped": 0, "errored": 0}
        for row in rows:
            paper = resolve_paper_from_metadata(row)
            pdf_path = locate_cached_pdf(row)
            try:
                res = await _build(
                    paper=paper, pdf_path=pdf_path,
                    kb_name=kb_name, app_state=state, force=force,
                )
                status = res.get("status", "errored")
                counts[status] = counts.get(status, 0) + 1
                click.echo(f"  {paper.id}: {status}")
            except Exception as exc:
                counts["errored"] += 1
                click.echo(f"  {paper.id}: errored — {exc}", err=True)
        click.echo(f"Summary: {counts}")
    asyncio.run(_run())


@cli.command("fetch-resources")
@click.option("--paper", "paper_id", required=True, help="Paper ID (e.g. doi:10.1234/abc)")
@click.option("--kb", "kb_name", required=True, help="KB containing the paper")
@click.option("--include", "include", default=None,
              help="Comma-separated kinds to fetch: github,zenodo,doi (default: all supported)")
@click.option("--ingest/--no-ingest", default=True,
              help="Route fetched text-like files into the KB as is_external chunks")
@click.option("--force", is_flag=True, default=False,
              help="(reserved) Re-fetch even if cached")
@click.pass_context
def fetch_resources_cmd(
    ctx, paper_id: str, kb_name: str,
    include: str | None, ingest: bool, force: bool,
) -> None:
    """Fetch external resources mined into the paper's capsule resources.json."""
    import asyncio

    from perspicacite.pipeline.capsule_builder import (
        capsule_dir_for,
        resolve_paper_from_metadata,
    )
    from perspicacite.pipeline.external.fetch_orchestrator import (
        fetch_paper_resources,
    )
    from perspicacite.web.state import AppState

    kinds: list[str] | None = (
        [k.strip() for k in include.split(",") if k.strip()] if include else None
    )

    async def _run() -> None:
        state = AppState()
        await state.initialize()
        kb_meta = await state.session_store.get_kb_metadata(kb_name)
        if kb_meta is None:
            click.echo(f"Error: KB '{kb_name}' not found", err=True)
            raise SystemExit(1)
        rows = await state.vector_store.list_paper_metadata(kb_meta.collection_name)
        _norm_pid = paper_id[4:] if paper_id.startswith("doi:") else paper_id
        row = next((r for r in rows if r.get("paper_id") == _norm_pid), None)
        if row is None:
            click.echo(f"Error: paper '{paper_id}' not in KB '{kb_name}'", err=True)
            raise SystemExit(1)
        paper = resolve_paper_from_metadata(row)
        cap_dir = capsule_dir_for(paper, root=state.config.capsule.root)
        setattr(paper, "_kb_name", kb_name)

        class _CLIRegistry:
            async def publish(self, _job_id, payload):
                kind = payload.get("kind", "")
                ident = payload.get("identifier", "")
                status = payload.get("status", "")
                click.echo(f"  {kind} {ident}: {status}")
            async def finish(self, _job_id, _payload):
                pass
            async def fail(self, _job_id, msg):
                click.echo(f"  ERROR: {msg}", err=True)

        registry = _CLIRegistry()
        result = await fetch_paper_resources(
            paper=paper, capsule_dir=cap_dir, kinds=kinds,
            app_state=state, registry=registry, job_id="cli",
            ingest=ingest, force=force,
        )
        click.echo(f"Summary: {result}")
    asyncio.run(_run())


@cli.command("import-browser-cookies")
@click.option(
    "--browser", "browser_name",
    type=click.Choice(
        ["chrome", "brave", "firefox", "edge", "opera", "chromium", "safari", "arc"],
        case_sensitive=False,
    ),
    default="brave",
    help="Browser to read cookies from.",
)
@click.option(
    "--domain", "domains",
    multiple=True,
    help=(
        "Cookie host substring filter. Pass multiple times. "
        "E.g. --domain nature.com --domain wiley.com. "
        "Empty = all cookies (NOT recommended)."
    ),
)
@click.option(
    "--output", "output_path",
    type=click.Path(),
    default="~/.config/perspicacite/cookies.txt",
    help="Where to write the Netscape-format cookies.txt.",
)
@click.option(
    "--print-config-snippet/--no-print-config-snippet",
    default=True,
    help="Print the config.yml block to copy.",
)
def import_browser_cookies_cmd(
    browser_name: str,
    domains: tuple[str, ...],
    output_path: str,
    print_config_snippet: bool,
) -> None:
    """Export browser cookies for institutional-access PDF downloads.

    Server-side equivalent of how the Zotero Connector grabs paywalled
    PDFs. Reads cookies your browser already has (after you've logged
    in via your library proxy or SSO), writes a Netscape ``cookies.txt``
    that pdf_download.cookies_path can consume.

    On macOS, decrypting the browser's cookie store requires keychain
    access — the OS may prompt once.

    Examples:
        perspicacite import-browser-cookies --browser brave \\
            --domain nature.com --domain wiley.com
    """
    try:
        import browser_cookie3
    except ImportError:
        click.echo(
            "browser_cookie3 not installed. Install the cookies extras:\n"
            "    uv pip install -e \".[cookies]\"",
            err=True,
        )
        raise SystemExit(2)
    from http.cookiejar import MozillaCookieJar
    from pathlib import Path

    fn = getattr(browser_cookie3, browser_name.lower(), None)
    if fn is None:
        click.echo(f"Unsupported browser: {browser_name}", err=True)
        raise SystemExit(2)

    out = Path(output_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    click.echo(f"Reading cookies from {browser_name}…")
    try:
        # Pass domain_name to make extraction cheaper when filter is given.
        # browser_cookie3.<browser>() returns a CookieJar of all cookies.
        all_cookies = fn()
    except Exception as exc:
        click.echo(f"Failed to read {browser_name} cookies: {exc}", err=True)
        click.echo(
            "Make sure the browser is installed and you've logged in at "
            "least once. On macOS you may need to grant keychain access.",
            err=True,
        )
        raise SystemExit(1)

    domain_filters = [d.lower() for d in (domains or ())]
    jar = MozillaCookieJar()
    total = 0
    matched = 0
    seen_hosts: dict[str, int] = {}
    for c in all_cookies:
        total += 1
        host = (c.domain or "").lstrip(".").lower()
        if domain_filters and not any(df in host for df in domain_filters):
            continue
        jar.set_cookie(c)
        matched += 1
        seen_hosts[host] = seen_hosts.get(host, 0) + 1

    jar.save(str(out), ignore_discard=True, ignore_expires=True)
    try:
        out.chmod(0o600)
    except OSError:
        pass

    click.echo(
        f"Wrote {matched} of {total} cookies to {out}  "
        f"(filters: {', '.join(domain_filters) or '(none — all hosts)'})"
    )
    if matched == 0:
        click.echo(
            "No cookies matched. Either you're not logged in to those "
            "hosts in this browser profile, or the filter strings don't "
            "match the cookie domain. Run without --domain to dump "
            "everything and inspect.",
            err=True,
        )
    elif matched < 50:
        # Show the top hosts so the user can confirm they got what they expected
        top = sorted(seen_hosts.items(), key=lambda x: -x[1])[:10]
        click.echo("Top cookie hosts captured:")
        for h, n in top:
            click.echo(f"  {n:>4}  {h}")

    if print_config_snippet and matched:
        suggested = sorted({h for h in seen_hosts if not h.startswith(".")})[:10]
        click.echo("\nAdd to your config.yml:\n")
        click.echo("pdf_download:")
        click.echo(f"  cookies_path: \"{out}\"")
        click.echo("  cookie_domains:")
        for h in suggested:
            click.echo(f"    - \"{h}\"")


@cli.command()
def version() -> None:
    """Print version information."""
    click.echo(f"Perspicacité v{__version__}")


def main() -> None:
    """Entry point."""
    cli()


if __name__ == "__main__":
    main()
