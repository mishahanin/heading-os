#!/usr/bin/env python3
"""
perplexity-research.py — Query Perplexity Sonar Pro for deep research.

Usage:
  python perplexity-research.py "your research question here"
  python perplexity-research.py --query "question" --model sonar-pro
  python perplexity-research.py --query "question" --model sonar (faster, cheaper)
  python perplexity-research.py --domains "reuters.com,bbc.com" "geopolitical developments"
  python perplexity-research.py --exclude-domains "pinterest.com,quora.com" "cybersecurity threats"

Models:
  sonar-pro  — Best quality, deep search with citations (default)
  sonar      — Faster, lighter, still search-augmented

Domain Filtering:
  --domains (-d)          Comma-separated domains to include (max 20)
  --exclude-domains (-x)  Comma-separated domains to exclude (max 20)
  Cannot mix --domains and --exclude-domains in a single call.
  See reference/search-domains.md for curated domain lists per topic.

Environment:
  Reads PERPLEXITY_API_KEY from .env file in workspace root or environment variable.

Output:
  Prints the response text followed by a Sources section with citations.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.perplexity_client import research as _pplx_research

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(SCRIPT_DIR)


def query_perplexity(question, model="sonar-pro", system_prompt=None,
                     domains=None, exclude_domains=None):
    """Call Perplexity, print content + sources, return (content, citations)."""
    try:
        content, citations = _pplx_research(
            question, model=model, system_prompt=system_prompt,
            domains=domains, exclude_domains=exclude_domains,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(content)
    if citations:
        print("\n---\nSources:")
        for i, url in enumerate(citations, 1):
            print(f"  [{i}] {url}")
    return content, citations


def main():
    parser = argparse.ArgumentParser(description="Query Perplexity Sonar for deep research")
    parser.add_argument("question", nargs="?", help="Research question (positional)")
    parser.add_argument("--query", "-q", help="Research question (named)")
    parser.add_argument(
        "--model", "-m",
        default="sonar-pro",
        choices=["sonar-pro", "sonar"],
        help="Model to use (default: sonar-pro)",
    )
    parser.add_argument("--system", "-s", help="Custom system prompt")
    parser.add_argument(
        "--domains", "-d",
        help="Comma-separated domains to include (max 20). Example: reuters.com,bbc.com",
    )
    parser.add_argument(
        "--exclude-domains", "-x",
        help="Comma-separated domains to exclude (max 20). Example: pinterest.com,quora.com",
    )

    args = parser.parse_args()

    if args.domains and args.exclude_domains:
        parser.error("Cannot use --domains and --exclude-domains together")

    question = args.question or args.query
    if not question:
        if not sys.stdin.isatty():
            question = sys.stdin.read().strip()
        else:
            parser.error("Provide a research question as argument, --query, or via stdin")

    query_perplexity(
        question,
        model=args.model,
        system_prompt=args.system,
        domains=args.domains,
        exclude_domains=args.exclude_domains,
    )


if __name__ == "__main__":
    main()
