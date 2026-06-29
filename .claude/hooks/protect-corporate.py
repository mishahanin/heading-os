#!/usr/bin/env python3
"""Compatibility shim — delegates to _dispatch.py.

Kept so exec workspaces whose settings.local.json was provisioned with
this filename keep working without re-provisioning. Direct execution of
this file produces identical behaviour to _dispatch.py — provided
_dispatch.py does not branch on sys.argv[0], __spec__, or any other
entry-point discriminator (it does not today).

If _dispatch.py is missing (e.g., on an exec workspace where this shim
exists but the dispatcher was not synced), the shim fails safe-open:
log a warning to stderr and exit 0, letting the tool call proceed.
The exec's actual security is via its own registered hooks.
"""
import os
import runpy
import sys

# realpath follows symlinks so the dispatcher is found relative to the
# shim's physical file, not a symlink location.
dispatcher = os.path.join(os.path.dirname(os.path.realpath(__file__)), "_dispatch.py")

# sys.argv inherited; _dispatch.py does not use it.
try:
    runpy.run_path(dispatcher, run_name="__main__")
except FileNotFoundError:
    print(f"[shim] dispatcher not found at {dispatcher}; exec workspace skip-open.", file=sys.stderr)
    sys.exit(0)
