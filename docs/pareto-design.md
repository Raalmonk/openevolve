# Explicit Pareto selection for OpenEvolve

This fork keeps the OpenEvolve 0.3.2 controller, asynchronous workers, model
provider interface, diff parser and evaluator interface. The opt-in selection
path treats named metrics as separate maximization objectives. It does not
compute an average, weighted sum, lexicographic utility or hypervolume score.

Base: `algorithmicsuperintelligence/openevolve@411fb59c886c18704caaffb611e17cf9e7d824d2`.
The unmodified scalar path remains the default. This is an adapted integration,
not an assertion that upstream OpenEvolve already implements these semantics.

## Source comparison

The following public implementations were inspected at fixed revisions. Similar
terminology does not necessarily mean the same selection algorithm.

| Implementation | Observed behavior | Integration decision |
|---|---|---|
| [BSPC-Evolve](https://github.com/keepkeen/openevolve-bspc-evolve/tree/900b44f1c80f3cf44668e5df35545f867aee8de8) | Real vector dominance and global/island archives, but scalar fallback remains in cell replacement, eviction, migration and worker context | Reuse dominance/archive ideas; repair the scalar paths rather than copy its complete research framework |
| [REPS](https://github.com/zkhorozianbc/reps/tree/ca5b2826e60d5678b98d86af0d9cb8666c349c01) | Computes a complete vector frontier and samples its programs uniformly; missing per-instance values can fall back to scalar scores | Reuse uniform frontier exposure; require explicit complete objectives and group exact equal vectors |
| [pymoo](https://github.com/anyoptimization/pymoo/tree/23110c155aa8f31b5f1b86928227fb3931ba7f00) | Actual nondominated sorting and rank/crowding survival | Reuse dependency-light domination-count graph layering; do not import its optimizer, evaluator or compiled default sorting path |
| [DEAP](https://github.com/DEAP/deap/tree/8a96fd3a75026f7b30e835f595a5199c75634ddf) | Actual NSGA-II sorting/crowding and historical Pareto archive | Algorithmic cross-check only; no LGPL code transplanted |
| [GEPA](https://github.com/gepa-ai/gepa/tree/15ee314f9c7d34ec153b809d401f42f55c4dcd76) | Default Pareto selector uses per-case/per-objective champion coverage, rather than the complete nondominated vector set | Do not substitute champion coverage for the requested vector frontier |
| [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve/tree/9912af12d423504b8d580f4179fd15f5f88b8c50) | Correct Pareto plotting helper; inspected search archive/parent paths remain scalar or embedding-based | Do not mistake a plotted frontier or embedding crowding for objective-space Pareto selection |
| [EvoX](https://github.com/EMI-Group/evox/tree/dad0c2d474ff505ecb569a3d7de9345aec6ff2bf) | Genuine nondominated sorting, crowding and NSGA-II survival | Algorithmic comparison only; no GPL/PyTorch/compiled machinery added |
| [OpenEvolve PR 458](https://github.com/algorithmicsuperintelligence/openevolve/pull/458) | Vector-valued score proposal based on lexicographic comparison; reviewed head `e4fa74799e3b8001c8a9b0f3688d307a961e1a4c` | Not a replacement for partial-order Pareto selection |

For example, `(0.8, 0.8)` is nondominated alongside `(1, 0)` and `(0, 1)`,
despite being neither coordinate's champion. All three belong to a complete
front. Likewise, distinct programs with identical vectors are not evidence of
distinct objective-space coverage.

## Selection contract

- Specify objective names explicitly. Values must be finite numeric scalars;
  missing values, booleans, NaN and infinity are not silently converted to zero
  or negative infinity. Finite negative scores remain valid.
- A dominates B only when A is no worse on every objective and better on at
  least one. There is no tolerance bucket or preferred coordinate.
- Preserve the full first front independently of MAP-Elites cell ownership.
  A cell is descriptive coverage, not permission to delete an incomparable
  program.
- Exploitation samples distinct frontier vectors uniformly, then chooses a
  program within an equal-vector group. Exploration can still sample dominated
  programs. Equal-vector grouping controls exposure, not evidence deletion.
- Context and migration selection use nondomination layers with seeded neutral
  ordering within a layer, not scalar sorting hidden behind a Pareto flag.
- Population pressure removes dominated layers first. The complete first front
  is protected; capacity is a soft bound if the front alone exceeds it.
- Persist selector settings and its RNG alongside checkpoint membership.
  Scalar and Pareto checkpoints cannot be silently interchanged.
- Preserve every evaluator metric and artifact. The selector has no access to
  targets, structural calculations or task-specific data.

Uniform exposure is intentional. Crowding distance is a valid alternative that
favors sparse regions and extremes, while hypervolume adds a reference-point
preference. Neither is equivalent to uniform sampling; neither is silently
enabled here. This fork is not a full NSGA-II implementation.

## Review-driven repairs

The BSPC source's same-cell path can lose A=`(0.4, 1.0)` after adding incomparable
B=`(1.0, 0.5)`, because B has the larger mean. Its population cap can similarly
remove a frontier extreme while protecting a newly inserted dominated child.
The backport must retain both incomparable programs, evict dominated candidates
first, and apply the same semantics inside worker prompt construction.

The implementation also keeps selected-parent artifacts in worker snapshots
even when the general artifact snapshot limit is reached. A frontier-selected
parent must not lose its evaluator feedback merely because it was inserted late.

Only generic source and synthetic tests belong in this repository. Integrating
this library does not restart an application, convert its old checkpoints or
change its evaluator. Existing scalar-search results remain scalar-search
history; extracting a frontier from them does not relabel the past search.
