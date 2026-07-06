#!/usr/bin/env python3
"""Retire memory file(s) from ALL stores (canonical + native) so the delete sticks.

Usage:
    python scripts/retire-memory.py NAME [NAME ...]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.memory_stores import retire_memory
from scripts.utils.colors import GREEN, GRAY, RESET


def main():
    ap = argparse.ArgumentParser(description="Retire memory files from all stores")
    ap.add_argument("names", nargs="+", help="memory file name(s), e.g. feedback_foo.md")
    args = ap.parse_args()
    for name in args.names:
        removed = retire_memory(name)
        if removed:
            print(f"{GREEN}retired{RESET} {name}: {len(removed)} store(s)")
        else:
            print(f"{GRAY}not found{RESET} {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
