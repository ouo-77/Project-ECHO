"""Replay ECHO-002 in new temporary databases. Output files contain synthetic measurements."""

import argparse
import csv
import json
import tempfile
from collections import Counter
from pathlib import Path

from echo_memory import store
from echo_memory.relationship import Relationship
from .fixtures import HERE, SEED, events


def replay(output):
    fixture = [json.loads(line) for line in (HERE / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    if fixture != list(events(SEED)):
        raise ValueError("Fixture does not match generator and seed")
    trace, probes = [], []
    with tempfile.TemporaryDirectory() as tmp:
        actors = {}
        cold = Relationship(Path(tmp) / "empty.sqlite", "unexposed")
        for row in fixture:
            person = row["person"]
            if person not in actors:
                actors[person] = Relationship(Path(tmp) / f"{person}.sqlite", person)
            actor = actors[person]
            kind, domain = row["kind"], row["domain"]
            if row["step"] == 1:
                trace.append(dict(person=person, step=0, kind="initial", trust=.5, metrology=.5,
                                  logistics=.5, closeness=.5, familiarity=0, **actor.state()["affect"]))
            agreement = {"p02": "friend", "p06": "companion", "p08": "family"}.get(person)
            if row["step"] == 9 and agreement:
                actor.set_policy(role=agreement, person_agrees=True, agent_agrees=True)
            mid = store.add(actor.path, kind="experience", content=row["summary"],
                            memory_id=row["memory_id"], source_ref=row["source_ref"],
                            origin=row["origin"], occurred_at=row["occurred_at"], confidence=row["confidence"],
                            tags=[f"person={person}", f"domain={domain}", f"event={kind}"])
            s = actor.observe(mid, kind, domain)
            trace.append(dict(person=person, step=row["step"], kind=kind, trust=s["trust"],
                              metrology=s["domain_trust"].get("metrology", .5),
                              logistics=s["domain_trust"].get("logistics", .5),
                              closeness=s["closeness"], familiarity=s["familiarity"], **s["affect"]))
            # Same metrology advice probe under all conditions. No write during a probe.
            on = actor.decide("metrology")
            off = actor.decide("metrology", retrieve=False)
            empty = cold.decide("metrology", retrieve=False)
            probes.append(dict(person=person, step=row["step"], memory_on=on["strategy"],
                               recall_off=off["strategy"], no_experience=empty["strategy"],
                               style=on["style"], evidence=",".join(on["evidence"])))
        conflicts = {p: sum(bool(r["conflicts"]) for r in a.history()) for p, a in actors.items()}
        roles = {p: a.state()["relationship_type"] for p, a in actors.items()}
        explanation = actors["p05"].explain("metrology")
    output.mkdir(parents=True, exist_ok=True)
    for name, records in (("trajectory", trace), ("probes", probes)):
        with (output / (name + ".csv")).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    summary = dict(seed=SEED, people=len(actors), events=len(fixture), conflicts=conflicts, roles=roles,
                   retrieved=sum(bool(p["evidence"]) for p in probes),
                   recall_changes=sum(p["memory_on"] != p["recall_off"] for p in probes),
                   state_changes=sum(p["recall_off"] != p["no_experience"] for p in probes),
                   strategies=dict(Counter(p["memory_on"] for p in probes)),
                   explanation=explanation)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.output)
    print({k: v for k, v in result.items() if k != "explanation"})
