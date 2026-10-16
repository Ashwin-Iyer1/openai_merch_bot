"""Compatibility entry point for the local Supply Observatory report builder.

The former automatic polling and Telegram loop is retained in Git history.
Use --demo, --input, or explicit --fetch; no work occurs on import.
"""
from studio import main

if __name__ == '__main__':
    raise SystemExit(main())
