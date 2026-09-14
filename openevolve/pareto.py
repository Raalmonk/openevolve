"""Opt-in vector selection; scalar database behavior is untouched when disabled.

Dominance/front loops adapted from keepkeen/openevolve-bspc-evolve,
900b44f1c80f3cf44668e5df35545f867aee8de8 (Apache-2.0).
Project adaptation: no hypervolume, scalar tie breaks, or front truncation.
Graph layering adapted from pymoo fast_non_dominated_sort/Dominator,
23110c155aa8f31b5f1b86928227fb3931ba7f00 (Apache-2.0).
Uniform-front parent policy follows REPS sample_pareto,
ca5b2826e60d5678b98d86af0d9cb8666c349c01 (MIT, Zach Khorozian).
"""

import functools
import hashlib
import json
import math
import os
import random
import tempfile


def objectives(config):
    names = tuple(config.pareto_objectives)
    if not names or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError("Pareto objectives must be explicit, nonempty, unique names")
    return names


def vector(program, names):
    values = tuple(program.metrics.get(n) for n in names)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError(f"Program {program.id} requires finite numeric Pareto objectives")
    return values


def dominates(candidate, incumbent):
    if not candidate or len(candidate) != len(incumbent):
        raise ValueError("Dominance requires nonempty equal-length vectors")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (*candidate, *incumbent)):
        raise ValueError("Dominance requires finite numeric vectors")
    better = False
    for a, b in zip(candidate, incumbent):
        if a < b:
            return False
        if a > b:
            better = True
    return better


def fronts(programs, names):
    """Nondomination layers of equal-vector groups, stable by program ID."""
    groups = {}
    for program in sorted(programs, key=lambda p: p.id):
        groups.setdefault(vector(program, names), []).append(program)
    vectors = list(groups)
    successors = [[] for _ in vectors]
    counts = [0] * len(vectors)
    for i, a in enumerate(vectors):
        for j in range(i + 1, len(vectors)):
            if dominates(a, vectors[j]):
                successors[i].append(j)
                counts[j] += 1
            elif dominates(vectors[j], a):
                successors[j].append(i)
                counts[i] += 1
    layer = [i for i, count in enumerate(counts) if count == 0]
    layers = []
    while layer:
        layers.append([groups[vectors[i]] for i in layer])
        next_layer = []
        for i in layer:
            for j in successors[i]:
                counts[j] -= 1
                if counts[j] == 0:
                    next_layer.append(j)
        layer = sorted(next_layer)
    return layers


def ranked(programs, names, rng):
    """One representative per vector, with neutral random order in each layer."""
    result = []
    for layer in fronts(programs, names):
        rng.shuffle(layer)
        result.extend(rng.choice(group) for group in layer)
    return result


def spec(db):
    return {"version": 1, "mode": "uniform-distinct-vector-front", "objectives": list(objectives(db.config)),
            "directions": "maximize", "archive_cap": "soft-complete-front", "population_cap": "soft-complete-front",
            "population_size": db.config.population_size, "archive_size": db.config.archive_size,
            "num_islands": db.config.num_islands,
            "exploration_ratio": db.config.exploration_ratio, "exploitation_ratio": db.config.exploitation_ratio,
            "migration_rate": db.config.migration_rate, "migration_interval": db.config.migration_interval}


def rng(db):
    if not hasattr(db, "pareto_rng"):
        db.pareto_rng = random.Random(db.config.random_seed)
    return db.pareto_rng


def candidates(db, island=None):
    ids = set().union(*db.islands) if island is None else db.islands[island]
    return [db.programs[pid] for pid in sorted(ids) if pid in db.programs]


def refresh(db):
    layers = fronts(candidates(db), objectives(db.config))
    db.archive = {p.id for group in (layers[0] if layers else []) for p in group}
    db.best_program_id = min(db.archive) if db.archive else None  # reporting representative only
    db.island_best_programs = []
    for island in range(len(db.islands)):
        local = fronts(candidates(db, island), objectives(db.config))
        db.island_best_programs.append(min((p.id for g in local[0] for p in g), default=None) if local else None)


def add(db, original, program, iteration=None, target_island=None):
    names = objectives(db.config)
    vector(program, names)  # validate before any mutation
    if program.id in db.programs:
        if db.programs[program.id].to_dict() != program.to_dict():
            raise ValueError("Cannot replace an existing Pareto program identity")
        return program.id
    parent = db.programs.get(program.parent_id)
    island = (target_island if target_island is not None else
              parent.metadata.get("island", db.current_island) if parent else db.current_island) % len(db.islands)
    if iteration is not None:
        program.iteration_found = iteration
        db.last_iteration = max(db.last_iteration, iteration)
    db.programs[program.id] = program
    program.metadata["island"] = island
    db.islands[island].add(program.id)
    # MAP cells describe coverage only; they never own the population/front.
    key = db._feature_coords_to_key(db._calculate_feature_coords(program))
    db.island_feature_maps[island].setdefault(key, program.id)
    layers = fronts(candidates(db), names)
    count = sum(len(g) for layer in layers for g in layer)
    for layer in reversed(layers[1:]):
        while layer and count > db.config.population_size:
            group = layer.pop(rng(db).randrange(len(layer)))
            for victim in group:
                for index, members in enumerate(db.islands):
                    members.discard(victim.id)
                    db.island_feature_maps[index] = {k: v for k, v in db.island_feature_maps[index].items() if v != victim.id}
                count -= 1
    refresh(db)
    if db.config.db_path:
        db._save_program(program)
    return program.id


def sample_from_island(db, original, island_id, num_inspirations=None):
    island_id %= len(db.islands)
    pool = candidates(db, island_id) or candidates(db)
    if not pool:
        raise ValueError("No eligible Pareto programs")
    layers = fronts(pool, objectives(db.config))
    draw = rng(db).random()
    if db.config.exploration_ratio <= draw < db.config.exploration_ratio + db.config.exploitation_ratio:
        groups = layers[0]
    else:
        groups = [g for layer in layers for g in layer]
    parent = rng(db).choice(rng(db).choice(groups))
    others = [p for p in pool if vector(p, objectives(db.config)) != vector(parent, objectives(db.config))]
    inspirations = ranked(others, objectives(db.config), rng(db))[:5 if num_inspirations is None else num_inspirations]
    return parent, inspirations


def sample(db, original, num_inspirations=None):
    return sample_from_island(db, original, db.current_island, num_inspirations)


def get_top_programs(db, original, n=10, metric=None, island_idx=None):
    if metric is not None:
        raise ValueError("Scalar metric ranking is disabled in Pareto mode")
    return ranked(candidates(db, island_idx), objectives(db.config), rng(db))[:n]


def get_best_program(db, original, metric=None):
    if metric is not None:
        raise ValueError("Scalar metric ranking is disabled in Pareto mode")
    return db.programs.get(db.best_program_id)


def export_front(db, destination):
    with open(os.path.join(destination, "pareto_front.json"), "w") as stream:
        json.dump({"selector": spec(db), "representative_id": db.best_program_id,
                   "programs": [db.programs[pid].to_dict() for pid in sorted(db.archive)]}, stream)


def save(db, original, path=None, iteration=0):
    destination = path or db.config.db_path
    if not destination:
        return
    sidecar = os.path.join(destination, "pareto.json")
    if os.path.exists(os.path.join(destination, "metadata.json")):
        if not os.path.exists(sidecar) or json.load(open(sidecar))["selector"] != spec(db):
            raise ValueError("Refusing checkpoint conversion/selector overwrite")
    original(db, path, iteration)
    export_front(db, destination)
    files = ["metadata.json", "pareto_front.json"] + ["programs/" + name for name in sorted(os.listdir(os.path.join(destination, "programs"))) if name.endswith(".json")]
    hashes = {}
    for name in files:
        with open(os.path.join(destination, name), "rb") as stream:
            hashes[name] = hashlib.sha256(stream.read()).hexdigest()
    # Commit the identity/RNG record last. An interrupted overwrite is rejected,
    # never accepted as a checkpoint with a stale RNG/active population.
    with tempfile.NamedTemporaryFile(mode="w", dir=destination, delete=False) as stream:
        temporary = stream.name
        json.dump({"selector": spec(db), "rng": rng(db).getstate(), "front_ids": sorted(db.archive),
                   "active_islands": [sorted(s) for s in db.islands], "files_sha256": hashes}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, sidecar)


def tuplify(value):
    return tuple(tuplify(v) for v in value) if isinstance(value, list) else value


def load(db, original, path):
    sidecar = os.path.join(path, "pareto.json")
    if not os.path.exists(sidecar):
        raise ValueError("Legacy checkpoint requires explicit offline Pareto conversion")
    with open(sidecar) as stream:
        saved = json.load(stream)
    if saved["selector"] != spec(db):
        raise ValueError("Checkpoint Pareto selector mismatch")
    expected_files = {"metadata.json", "pareto_front.json"} | {"programs/" + name for name in os.listdir(os.path.join(path, "programs")) if name.endswith(".json")}
    if set(saved.get("files_sha256", {})) != expected_files:
        raise ValueError("Checkpoint record manifest mismatch")
    for name, digest in saved["files_sha256"].items():
        with open(os.path.join(path, name), "rb") as stream:
            if hashlib.sha256(stream.read()).hexdigest() != digest:
                raise ValueError("Checkpoint transaction/record hash mismatch")
    # Read all records before committing. Stock reconstruction assumes one owner per cell.
    from openevolve.database import Program
    with open(os.path.join(path, "metadata.json")) as stream:
        metadata = json.load(stream)
    records = {}
    for name in sorted(os.listdir(os.path.join(path, "programs"))):
        if name.endswith(".json"):
            with open(os.path.join(path, "programs", name)) as stream:
                p = Program.from_dict(json.load(stream))
            vector(p, objectives(db.config))
            records[p.id] = p
    islands = [set(s) for s in saved["active_islands"]]
    if len(islands) != db.config.num_islands or not set().union(*islands) <= records.keys():
        raise ValueError("Checkpoint island/record mismatch")
    if sum(map(len, islands)) != len(set().union(*islands)) or any(records[pid].metadata.get("island") != index for index, ids in enumerate(islands) for pid in ids):
        raise ValueError("Checkpoint requires unique matching island ownership")
    if (not 0 <= metadata["current_island"] < len(islands)
            or len(metadata["island_generations"]) != len(islands)
            or len(metadata["island_feature_maps"]) != len(islands)
            or any(not set(mapping.values()) <= islands[index] for index, mapping in enumerate(metadata["island_feature_maps"]))):
        raise ValueError("Checkpoint island bookkeeping mismatch")
    active = [records[p] for p in set().union(*islands)]
    layers = fronts(active, objectives(db.config))
    front = {p.id for group in (layers[0] if layers else []) for p in group}
    if front != set(saved["front_ids"]):
        raise ValueError("Checkpoint Pareto front mismatch")
    restored_rng = random.Random()
    restored_rng.setstate(tuplify(saved["rng"]))
    db.programs, db.islands, db.pareto_rng = records, islands, restored_rng
    db.feature_stats = db._deserialize_feature_stats(metadata.get("feature_stats", {}))
    db._pareto_loaded_feature_stats = db.feature_stats
    for field in ("island_feature_maps", "last_iteration", "current_island", "island_generations", "last_migration_generation"):
        setattr(db, field, metadata[field])
    refresh(db)


def pareto_database(cls):
    """Explicit conditional wrappers keep the upstream scalar implementation intact."""
    init = cls.__init__
    @functools.wraps(init)
    def initialize(self, config):
        if config.use_pareto_archive:
            objectives(config)
            if config.embedding_model or config.novelty_llm:
                raise ValueError("Pareto mode does not support optional embedding/LLM novelty filters")
            if config.population_size < 1 or config.num_islands < 1:
                raise ValueError("Pareto population_size and num_islands must be positive")
        init(self, config)
        if hasattr(self, "_pareto_loaded_feature_stats"):
            self.feature_stats = self._pareto_loaded_feature_stats
            del self._pareto_loaded_feature_stats
    cls.__init__ = initialize
    for name in ("add", "sample", "sample_from_island", "get_top_programs", "get_best_program", "save", "load"):
        original, replacement = getattr(cls, name), globals()[name]
        def wrap(original, replacement):
            @functools.wraps(original)
            def dispatch(self, *args, **kwargs):
                if self.config.use_pareto_archive:
                    return replacement(self, original, *args, **kwargs)
                if original.__name__ in ("load", "save"):
                    path = (args[0] if args else kwargs.get("path")) or self.config.db_path
                    if path and os.path.exists(os.path.join(path, "pareto.json")):
                        raise ValueError("Refusing Pareto checkpoint in scalar mode")
                return original(self, *args, **kwargs)
            return dispatch
        setattr(cls, name, wrap(original, replacement))
    return cls
