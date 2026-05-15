# Security Policy

## Scope

This policy covers the `perspicacite` Python package (this repository). It does not cover:

- Third-party dependencies (`chromadb`, `litellm`, `scilex`, etc.) — report those to their respective maintainers.
- The LLM providers you configure (Anthropic, OpenAI, DeepSeek, etc.) — report API or model vulnerabilities to those providers directly.
- Infrastructure you self-host (e.g., Ollama, your network proxy) — out of scope here.

## Supported versions

| Version | Supported |
|---------|-----------|
| 2.x (current) | Yes |
| < 2.0 | No |

The current package version is `2.0.0` (see `pyproject.toml`). Only the latest release on the `main` branch receives security fixes.

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email the maintainers at **louisfelix.nothias@gmail.com** with:

1. A concise description of the vulnerability.
2. Steps to reproduce (proof-of-concept or minimal repro code preferred).
3. The affected version(s) and environment (OS, Python version, install method).
4. Your assessment of impact and any suggested fix.

You will receive an acknowledgement within **5 business days**. We aim to provide a triage decision within **14 days** and a patch or mitigation within **60 days** for confirmed, exploitable issues.

## Disclosure policy

We follow a **coordinated disclosure** model. Please allow us reasonable time to develop and release a fix before making the vulnerability public. We will credit reporters in the release notes unless you request otherwise.

## Notes on data handling

Perspicacite-AI is local-first: your knowledge bases, PDFs, and conversation history stay on your machine. The only data sent outbound are LLM inference payloads (to your configured provider) and academic-API queries (Semantic Scholar, OpenAlex, PubMed, arXiv, etc.). If you find a code path that inadvertently exfiltrates data, that is in scope for this policy.
