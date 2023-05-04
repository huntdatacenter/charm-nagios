#!/usr/bin/env python3
import os
import sys

HOOKS = os.path.join(os.path.dirname(__file__), "..", "hooks")
sys.path.append(HOOKS)

from monitors_relation_changed import main  # noqa: E402

main(sys.argv, full_rewrite=True)
