"""Explicit local scheduler host. Only existing, person-bound databases are accepted."""

import argparse
import json
import time
from pathlib import Path

from echo_memory import store
from echo_memory.reappraisal import EvidenceReview
from echo_memory.relationship import Relationship
from .engine import MODES, Reflect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--person", required=True)
    parser.add_argument("--event-id")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("command", choices=(*MODES, "tick", "watch", "paused", "disabled", "enabled"))
    args = parser.parse_args()
    if not args.db.is_file() or args.interval < 1:
        parser.error("Existing experimental database and positive interval required")
    with store.connection(args.db) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='relation_model'").fetchone():
            parser.error("Not a relationship experiment database")
        if con.execute("SELECT person FROM relation_model").fetchone()[0] != args.person:
            parser.error("Person binding mismatch")
    engine = Reflect(EvidenceReview(Relationship(args.db, args.person)))
    if args.command in ("paused", "disabled", "enabled"):
        engine.control(args.command)
    elif args.command in MODES:
        print(json.dumps(engine.run(args.command, event_id=args.event_id)))
    else:
        while True:
            print(json.dumps(engine.tick()), flush=True)
            if args.command != "watch":
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
