# Pareto integration attribution

The original OpenEvolve license and authorship remain unchanged. This branch
adds an adapted vector-selection path and integration tests.

- **BSPC-Evolve / keepkeen** — `900b44f1c80f3cf44668e5df35545f867aee8de8`,
  https://github.com/keepkeen/openevolve-bspc-evolve . Dominance/front-archive
  concepts and comparison loop, Apache-2.0 (see the repository `LICENSE`).
- **REPS / Zach Khorozian** — `ca5b2826e60d5678b98d86af0d9cb8666c349c01`,
  https://github.com/zkhorozianbc/reps . Uniform frontier sampling policy;
  Copyright (c) 2026 Zach Khorozian. Full MIT license:
  `licenses/REPS-MIT.txt`.
- **pymoo** — `23110c155aa8f31b5f1b86928227fb3931ba7f00`,
  https://github.com/anyoptimization/pymoo . Domination-count graph layering
  adapted from `pymoo/util/nds/fast_non_dominated_sort.py` and dominance
  relations. Full source license: `licenses/pymoo-Apache-2.0.txt`.

Changes include explicit finite maximization vectors, grouping equal vectors,
complete-front retention, seeded neutral exposure, checkpoint binding, worker
context, and conditional integration with OpenEvolve. The graph implementation
uses Python containers rather than importing pymoo's compiled/default runtime.

DEAP, GEPA, ShinkaEvolve and EvoX were inspected for comparison. Their code is
not transplanted. No application datasets, private results, credentials or
application-specific evaluator are included.
