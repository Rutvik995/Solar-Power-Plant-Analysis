"""
scripts/list_models.py — Print the model IDs available to the configured Groq API key.

Usage:
    python scripts/list_models.py
"""

from __future__ import annotations

import os
import sys

# Add project root to sys.path so settings can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from solar_agent.config.settings import settings


def main() -> None:
    api_key = os.getenv("GROQ_API_KEY") or getattr(settings, "groq_api_key", "")
    if not api_key:
        print(
            "Error: GROQ_API_KEY is not set in the environment or .env file.\n"
            "Please set GROQ_API_KEY=<your-key> to list available models.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from groq import Groq
    except ImportError:
        print(
            "Error: 'groq' package is not installed.\n"
            "Install it via: pip install langchain-groq",
            file=sys.stderr,
        )
        sys.exit(1)

    client = Groq(api_key=api_key)
    try:
        models_page = client.models.list()
        models = sorted(models_page.data, key=lambda m: getattr(m, "id", ""))
        print(f"Available Groq Models ({len(models)} found):")
        print("-" * 50)
        for m in models:
            m_id = getattr(m, "id", str(m))
            owned_by = getattr(m, "owned_by", "")
            if owned_by:
                print(f"  • {m_id} (owned by: {owned_by})")
            else:
                print(f"  • {m_id}")
    except Exception as exc:
        print(f"Error querying Groq models: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
