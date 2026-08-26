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
from scripts.utils.colors import GREEN, GRAY, RED, RESET


def main():
    ap = argparse.ArgumentParser(description="Retire memory files from all stores")
    ap.add_argument("names", nargs="+", help="memory file name(s), e.g. feedback_foo.md")
    args = ap.parse_args()
    exit_code = 0
    for name in args.names:
        removed, failed = retire_memory(name)
        if failed:
            # A delete that did not stick is not a retirement. Reported here and
            # in the exit code, because the operator's next step is to fix the
            # permission and run it again, not to assume the file is gone.
            print(f"{RED}NOT retired{RESET} {name}: {len(removed)} store(s) cleared, "
                  f"{len(failed)} refused")
            for path, why in failed:
                print(f"    {path}: {why}")
            exit_code = 1
        elif removed:
            print(f"{GREEN}retired{RESET} {name}: {len(removed)} store(s)")
        else:
            print(f"{GRAY}not found{RESET} {name}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
