import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Colex personal CoCo plugin")
    parser.add_argument("action", choices=["serve", "install", "uninstall"])
    args = parser.parse_args()
    if args.action == "serve":
        from colex.server import mcp

        mcp.run(transport="stdio")
    else:
        from colex.install import configure

        configure(uninstall=args.action == "uninstall")


if __name__ == "__main__":
    main()
