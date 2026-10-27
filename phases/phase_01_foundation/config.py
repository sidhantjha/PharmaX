"""Explicit opt-in configuration; never searches parent checkouts for secrets."""
import os
import re
from pathlib import Path


def load_env(path: str | None, *, prefix: str | None = None):
    if not path:
        return
    # Read in place; never execute shell, copy credentials, or override process settings.
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
            raise ValueError('Invalid environment file format')
        if prefix and not key.strip().startswith(prefix):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)
