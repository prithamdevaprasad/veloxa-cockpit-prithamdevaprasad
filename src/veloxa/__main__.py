from .cli import main

if __name__ == "__main__":         # required: the C06 parser runs in a spawned process, which re-imports this module
    raise SystemExit(main())
