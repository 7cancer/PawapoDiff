"""Browser UI launcher for the standalone terminology checker."""

from .__main__ import main as launch


def main():
    return launch(mode="consistency")


if __name__ == "__main__":
    raise SystemExit(main())
