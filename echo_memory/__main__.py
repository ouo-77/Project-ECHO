"""Small command line interface for the offline prototype."""

from __future__ import annotations

import argparse
import json

from . import store


def main() -> None:
    parser = argparse.ArgumentParser(description="Project ECHO local memory prototype (offline)")
    parser.add_argument("--db", default=None, help="SQLite file path (overrides ECHO_MEMORY_DB)")
    cmds = parser.add_subparsers(dest="cmd", required=True)
    cmds.add_parser("init", help="Create local SQLite schema")
    add = cmds.add_parser("add", help="Add a record (private by default)")
    add.add_argument("--kind", choices=sorted(store.KINDS), required=True)
    add.add_argument("--content", required=True)
    add.add_argument("--importance", type=int, default=3)
    add.add_argument("--tags", default="", help="Comma-separated tags")
    add.add_argument("--origin", default="manual")
    add.add_argument("--visibility", choices=sorted(store.VISIBILITIES), default="private")
    add.add_argument("--supersedes", default=None)
    add.add_argument("--claim-key", help="Stable key for a single-valued fact")
    add.add_argument("--experience-id", help="Evidence for a reflection")
    search = cmds.add_parser("search", help="Case-insensitive substring search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--history", action="store_true")
    show = cmds.add_parser("show", help="Display one record")
    show.add_argument("id")
    delete = cmds.add_parser("delete", help="Delete one record")
    delete.add_argument("id")
    seed = cmds.add_parser("seed", help="Import a public JSONL seed file")
    seed.add_argument("--file", required=True)
    export = cmds.add_parser("export-public", help="Export ONLY explicitly public active records")
    export.add_argument("--output", required=True)
    cmds.add_parser("stats", help="Memory statistics")

    args = parser.parse_args()
    path = args.db or store.database_path()
    if args.cmd == "init":
        store.init(path)
        print("Initialized local SQLite database:", path)
    elif args.cmd == "add":
        mid = store.add(path, kind=args.kind, content=args.content,
                        importance=args.importance,
                        tags=[t.strip() for t in args.tags.split(",") if t.strip()],
                        origin=args.origin, visibility=args.visibility,
                        supersedes=args.supersedes, claim_key=args.claim_key,
                        experience_id=args.experience_id)
        print("Saved:", mid, "(visibility:", args.visibility + ")")
    elif args.cmd == "search":
        print(json.dumps(store.search(path, args.query, args.limit, args.history), ensure_ascii=False, indent=2))
    elif args.cmd == "show":
        print(json.dumps(store.get(path, args.id), ensure_ascii=False, indent=2))
    elif args.cmd == "delete":
        print("Deleted" if store.delete(path, args.id) else "Not found")
    elif args.cmd == "seed":
        print("Imported", store.seed(path, args.file), "public example records")
    elif args.cmd == "export-public":
        print("Exported", store.export_public(path, args.output), "public records")
    elif args.cmd == "stats":
        print(json.dumps(store.stats(path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
