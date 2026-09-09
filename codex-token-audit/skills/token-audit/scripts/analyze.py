#!/usr/bin/env python3
"""token-audit entry point for Codex CLI rollouts. Standard library only; no network access."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tokenaudit.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
