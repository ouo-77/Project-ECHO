"""First acceptance tests; run with `python -m unittest discover -s tests -v`."""

import json
import tempfile
import unittest
from pathlib import Path

from echo_memory import store


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite"
        store.init(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_survives_reopening(self):
        mid = store.add(self.path, kind="fact", content="ECHO stores memory across restarts")
        self.assertEqual(store.get(self.path, mid)["content"], "ECHO stores memory across restarts")
        self.assertEqual(store.search(self.path, "across restarts")[0]["id"], mid)

    def test_revision_preserves_history(self):
        old = store.add(self.path, kind="fact", content="Prototype version zero")
        new = store.add(self.path, kind="fact", content="Prototype version one", supersedes=old)
        self.assertEqual([m["id"] for m in store.search(self.path, "Prototype version")], [new])
        self.assertEqual({m["id"] for m in store.search(self.path, "Prototype version", include_history=True)}, {old, new})
        self.assertEqual(store.get(self.path, new)["supersedes"], old)

    def test_default_private_cannot_export(self):
        store.add(self.path, kind="experience", content="PRIVATE_SECRET_DO_NOT_PUBLISH")
        store.add(self.path, kind="decision", content="Public test decision", visibility="public")
        export_path = Path(self.tmp.name) / "public.jsonl"
        count = store.export_public(self.path, export_path)
        self.assertEqual(count, 1)
        raw = export_path.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_SECRET_DO_NOT_PUBLISH", raw)
        self.assertEqual(json.loads(raw)["content"], "Public test decision")

    def test_deletion(self):
        mid = store.add(self.path, kind="reflection", content="Delete this after review")
        self.assertTrue(store.delete(self.path, mid))
        self.assertFalse(store.search(self.path, "Delete this"))
        self.assertIsNone(store.get(self.path, mid))

    def test_public_seed_is_idempotent(self):
        seed_file = Path(self.tmp.name) / "seed.jsonl"
        seed_file.write_text(json.dumps({"id": "seed-x", "occurred_at": "2026-11-10T00:00:00+00:00",
                                         "kind": "fact", "content": "Public sample", "importance": 3,
                                         "visibility": "public", "tags": [], "origin": "test"}) + "\n")
        self.assertEqual(store.seed(self.path, seed_file), 1)
        self.assertEqual(store.seed(self.path, seed_file), 0)

    def test_nonpublic_seed_rejected(self):
        seed_file = Path(self.tmp.name) / "seed.jsonl"
        seed_file.write_text(json.dumps({"kind": "fact", "content": "PRIVATE", "importance": 3,
                                         "visibility": "private", "occurred_at": "2026-11-10T00:00:00+00:00",
                                         "id": "not-public"}) + "\n")
        with self.assertRaises(ValueError):
            store.seed(self.path, seed_file)


if __name__ == "__main__":
    unittest.main()
