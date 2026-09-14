"""Offline fixed-data checks. Set OPENEVOLVE_PARETO_SOURCE to the patched source root."""
import asyncio
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("OPENEVOLVE_PARETO_SOURCE")
if SOURCE:
    sys.path.insert(0, SOURCE)

from openevolve.config import Config, DatabaseConfig
from openevolve.database import Program, ProgramDatabase
from openevolve.pareto import fronts, ranked, rng
from openevolve.prompt.sampler import PromptSampler
from openevolve import process_parallel as worker


def config(**kwargs):
    return DatabaseConfig(use_pareto_archive=True, pareto_objectives=["a", "b"], num_islands=1,
                          **kwargs)


def program(pid, a, b):
    return Program(id=pid, code=f"x = {len(pid)}", metrics={"a": a, "b": b})


class ParetoBackportTests(unittest.TestCase):
    def test_complete_front_and_population(self):
        db = ProgramDatabase(config(population_size=2, archive_size=1))
        db._calculate_feature_coords = lambda p: [0, 0]
        for p in [program("a", .4, 1), program("b", 1, .5), program("bad", .1, .1), program("dup", .4, 1)]:
            db.add(p)
        self.assertEqual(db.archive, {"a", "b", "dup"})
        self.assertEqual(db.islands[0], db.archive)
        self.assertIn("bad", db.programs)  # source record retained after eviction
        self.assertEqual(len(db.island_feature_maps[0]), 1)

    def test_five_axis_tradeoff(self):
        names = list("abcde")
        db = ProgramDatabase(DatabaseConfig(use_pareto_archive=True, pareto_objectives=names, num_islands=1))
        vectors = [(5, 5, 7, 7, 9),
                   (4, 4, 8, 8, 6)]
        for i, values in enumerate(vectors):
            db.add(Program(id=str(i), code=str(i), metrics=dict(zip(names, values))))
        self.assertEqual(db.archive, {"0", "1"})

    def test_duplicate_group_mass_and_invariance(self):
        base = [program("a", .4, 1), program("b", 1, .5), program("c", -1, 0)]
        augmented = base + [program(f"dup{i}", .4, 1) for i in range(20)]
        self.assertEqual([len(layer) for layer in fronts(augmented, ["a", "b"])], [2, 1])
        self.assertEqual(len(ranked(augmented, ["a", "b"], random.Random(1))), 3)
        altered = [program(p.id, p.metrics["b"] * 3, p.metrics["a"] * 7) for p in base]
        self.assertEqual({p.id for g in fronts(base, ["a", "b"])[0] for p in g},
                         {p.id for g in fronts(altered, ["a", "b"])[0] for p in g})
        db = ProgramDatabase(config(exploration_ratio=0, exploitation_ratio=1))
        for p in augmented:
            db.add(p)
        counts = {"a": 0, "b": 0}
        for _ in range(400):
            parent, _ = db.sample(0)
            counts["a" if parent.metrics["a"] == .4 else "b"] += 1
        self.assertTrue(140 < counts["a"] < 260, counts)

    def test_invalid_before_mutation(self):
        db = ProgramDatabase(config())
        db.add(program("negative", -2, 0))
        for value in [float("nan"), float("inf"), None, True, "3"]:
            with self.assertRaises(ValueError):
                db.add(program("invalid", value, 1))
        self.assertEqual(set(db.programs), {"negative"})
        for names in [[], ["a", "a"]]:
            with self.assertRaises(ValueError):
                ProgramDatabase(DatabaseConfig(use_pareto_archive=True, pareto_objectives=names))

    def test_checkpoint_front_rng_and_mismatch(self):
        db = ProgramDatabase(config())
        db.add(program("a", .4, 1)); db.add(program("b", 1, .5))
        with tempfile.TemporaryDirectory() as folder:
            db.save(folder)
            expected = [db.sample()[0].id for _ in range(10)]
            loaded = ProgramDatabase(config(db_path=folder))
            self.assertEqual(loaded.archive, {"a", "b"})
            self.assertEqual([loaded.sample()[0].id for _ in range(10)], expected)
            with self.assertRaises(ValueError):
                ProgramDatabase(DatabaseConfig(db_path=folder))
            other = ProgramDatabase(config())
            other.config.pareto_objectives = ["b", "a"]
            with self.assertRaises(ValueError):
                other.load(folder)
        with tempfile.TemporaryDirectory() as legacy:
            ProgramDatabase(DatabaseConfig()).save(legacy)
            with self.assertRaises(ValueError):
                db.load(legacy)
            with self.assertRaises(ValueError):
                db.save(legacy)

    def test_eviction_checkpoint_and_worker_config(self):
        cfg = Config(database=config(population_size=1))
        db = ProgramDatabase(cfg.database)
        db.add(program("good", 1, 1)); db.add(program("bad", 0, 0))
        with tempfile.TemporaryDirectory() as folder:
            db.save(folder)
            loaded = ProgramDatabase(config(population_size=1, db_path=folder))
            self.assertEqual(loaded.islands[0], {"good"})
            self.assertIn("bad", loaded.programs)
            self.assertEqual({p["id"] for p in json.loads(Path(folder, "pareto_front.json").read_text())["programs"]}, {"good"})
        controller = worker.ProcessParallelController(cfg, "UNUSED", db)
        worker._worker_init(controller._serialize_config(cfg), "UNUSED")
        self.assertTrue(worker._worker_config.database.use_pareto_archive)
        self.assertEqual(worker._worker_config.database.pareto_objectives, ["a", "b"])

    def test_migration_uses_native_copies(self):
        cfg = DatabaseConfig(use_pareto_archive=True, pareto_objectives=["a", "b"], num_islands=2)
        db = ProgramDatabase(cfg)
        source = program("source", 1, 1)
        source.artifacts_json = json.dumps({"insights": "retained"})
        db.add(source, target_island=0)
        db.migrate_programs()
        self.assertEqual(len(db.islands[1]), 1)
        copied = db.programs[next(iter(db.islands[1]))]
        self.assertNotEqual(copied.id, "source")
        self.assertEqual(copied.parent_id, "source")
        self.assertEqual(copied.metadata["island"], 1)
        self.assertTrue(copied.metadata["migrant"])
        self.assertEqual(copied.artifacts_json, source.artifacts_json)

    def test_balanced_front_and_transaction_detection(self):
        db = ProgramDatabase(config(population_size=1, archive_size=1))
        for p in [program("left", 0, 1), program("middle", .6, .6), program("right", 1, 0)]:
            db.add(p)
        self.assertEqual(db.archive, {"left", "middle", "right"})
        with tempfile.TemporaryDirectory() as folder:
            db.save(folder)
            metadata = Path(folder, "metadata.json")
            value = json.loads(metadata.read_text())
            value["last_iteration"] += 1
            metadata.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                ProgramDatabase(config(population_size=1, archive_size=1)).load(folder)

    def test_final_controller_export(self):
        from openevolve.controller import OpenEvolve
        cfg = Config(database=config())
        db = ProgramDatabase(cfg.database)
        db.add(program("a", 0, 1)); db.add(program("b", 1, 0))
        with tempfile.TemporaryDirectory() as folder:
            controller = OpenEvolve.__new__(OpenEvolve)
            controller.database, controller.config = db, cfg
            controller.output_dir, controller.file_extension = folder, ".py"
            controller._save_best_program()
            info = json.loads(Path(folder, "best", "best_program_info.json").read_text())
            self.assertTrue(info["representative_only"])
            front = json.loads(Path(folder, "best", "pareto_front.json").read_text())
            self.assertEqual({p["id"] for p in front["programs"]}, {"a", "b"})

    def test_submit_keeps_parent_artifact_beyond_snapshot_cap(self):
        cfg = Config(database=config(max_snapshot_artifacts=0))
        db = ProgramDatabase(cfg.database)
        db.add(program("parent", 1, 1))
        controller = worker.ProcessParallelController(cfg, "UNUSED", db)
        captured = {}
        class Executor:
            def submit(self, function, iteration, snapshot, parent_id, inspiration_ids):
                captured.update(snapshot)
                return "submitted"
        controller.executor = Executor()
        with patch.object(db, "get_artifacts", return_value={"insights": "all retained"}):
            self.assertEqual(controller._submit_iteration(1), "submitted")
        self.assertEqual(captured["artifacts"]["parent"], {"insights": "all retained"})
        self.assertIn("pareto_context_rng", captured)

    def test_real_worker_prompt_and_fixed_evaluator(self):
        cfg = Config(database=config())
        cfg.prompt.num_top_programs = 2
        cfg.prompt.num_diverse_programs = 0
        cfg.diff_based_evolution = True
        db = ProgramDatabase(cfg.database)
        for p in [program("a", .4, 1), program("b", 1, .5), program("dominated", 0, 0)]:
            db.add(p)
        controller = worker.ProcessParallelController(cfg, "UNUSED", db)
        snapshot = controller._create_database_snapshot()
        snapshot["pareto_context_rng"] = rng(db).getstate()
        snapshot["artifacts"]["a"] = {"insight": "retained feedback"}
        class Model:
            async def generate_with_context(self, **kwargs):
                return "<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE"
        class Evaluator:
            async def evaluate_program(self, code, pid):
                self.code = code
                return {"a": .6, "b": .8}
            def get_pending_artifacts(self, pid):
                return {}
        evaluator = Evaluator()
        with patch.object(worker, "_lazy_init_worker_components", lambda: None), \
             patch.object(worker, "_worker_config", cfg, create=True), \
             patch.object(worker, "_worker_llm_ensemble", Model(), create=True), \
             patch.object(worker, "_worker_evaluator", evaluator, create=True), \
             patch.object(worker, "_worker_prompt_sampler", PromptSampler(cfg.prompt), create=True):
            result = worker._run_iteration_worker(1, snapshot, "a", [])
        self.assertIsNone(result.error)
        self.assertEqual(evaluator.code, "x = 2")
        self.assertIn('"id": "a"', result.prompt["user"])
        self.assertIn('"id": "b"', result.prompt["user"])
        self.assertNotIn('"id": "dominated"', result.prompt["user"])
        self.assertIn("retained feedback", result.prompt["user"])
        self.assertNotIn("Fitness:", result.prompt["user"])
        self.assertNotIn("FITNESS SCORE", result.prompt["user"])

    def test_scalar_default_and_predispatch_rejection(self):
        db = ProgramDatabase(DatabaseConfig(num_islands=1))
        db.add(program("a", .4, 1)); db.add(program("b", 1, .5))
        self.assertEqual(db.get_best_program().id, "b")
        cfg = Config(database=config())
        controller = worker.ProcessParallelController(cfg, "UNUSED", ProgramDatabase(cfg.database))
        with self.assertRaisesRegex(ValueError, "Scalar"):
            asyncio.run(controller.run_evolution(0, 1, target_score=1))


if __name__ == "__main__":
    unittest.main()
