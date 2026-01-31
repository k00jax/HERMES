# HERMES Brain

Offline-first local reasoning system for ODROID-M1S.

## Milestone 1: Local-only Q&A
- Local indexing of .txt/.md documents under knowledge/
- Simple keyword-based retrieval
- CLI answers questions using retrieved context

## Quickstart on ODROID
1) Install dependencies:

- Run scripts/setup_odroid.sh

2) Add knowledge files:

- Put .txt or .md files under knowledge/core or knowledge/deep

3) Build index and ask a question:

- Run scripts/run_cli.sh "What is in my knowledge base?"

If no local LLM is installed, the CLI returns a helpful placeholder response plus sources.
