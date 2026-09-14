# Using the opt-in Pareto mode

The default configuration remains scalar. Enable the vector selector explicitly:

```yaml
database:
  use_pareto_archive: true
  pareto_objectives:
    - quality
    - stability
    - efficiency
  population_size: 1000
  archive_size: 100
  num_islands: 5
  exploration_ratio: 0.2
  exploitation_ratio: 0.7
  random_seed: 42
```

An evaluator can return its ordinary flat metric dictionary, or an
`EvaluationResult(metrics=..., artifacts=...)`. The names above are examples;
configure the exact keys your evaluator provides. Every configured objective
must be finite and higher-is-better. Other metrics are not additional objectives.
The selector neither rescales scores nor supplies a `combined_score`.

The model provider and evaluation interfaces are unchanged. A custom configured
system message is retained; make its instructions consistent with vector
optimization, without asking the model to maximize an average.

## Exact semantics

The first front contains every active program not dominated on all configured
objectives. Complete first-front membership is independent of MAP cell ownership.
Equal metric vectors form one exposure group, but all their program records are
retained. Exploitation samples frontier groups uniformly, then chooses a program
inside that group. Exploration and the residual sampling probability sample
all distinct vectors in the selected island. Empty islands fall back to the
available global active population.

Historical context and migration use nondomination layers with seeded random
order within each layer. Population pressure retires worst dominated groups
first. Retired records remain stored for provenance but are not sampled. The
first front is protected even if it alone exceeds a configured capacity:
`population_size` and `archive_size` are therefore soft bounds in this mode.

The selector uses a dedicated seeded RNG. Its state and the selection settings
are saved in `pareto.json`, together with active island membership and committed
checkpoint file identities. This binds a saved checkpoint to its actual
selector; it does not promise identical asynchronous completion order or
identical external model responses across runs.

## Output and restoration

`pareto_front.json` contains all first-front programs at checkpoints and in the
final `best/` output directory. The legacy `best_program.py` and return value
remain available for API compatibility, but represent only one member of the
front. `best_program_info.json` marks `representative_only: true`; it is not a
universal winner or an aggregate-score champion.

Scalar checkpoints are not converted automatically. Loading with changed
selection settings, overwriting a scalar checkpoint with a Pareto checkpoint,
or interpreting a Pareto checkpoint in scalar mode raises an error. Interrupted
mixed writes are detected rather than loaded with a mismatched RNG. Preserve
the original checkpoint and establish any application-level continuation
explicitly; the library does not launch or migrate an existing run.

## Supported boundary

This mode keeps the native asynchronous process controller, parser, provider,
evaluator, migration copies and ring topology. It uses a vector-oriented default
user prompt while preserving the configured system prompt and evaluator
artifacts. The selected parent's artifacts are included even when the general
snapshot artifact limit has already been reached.

Scalar `target_score`/early stopping, optional embedding/LLM novelty filters,
and custom user-template directories/overrides are not supported in this mode.
They are rejected rather than silently changing selection semantics. Crowding,
hypervolume, reference-point weighting and automatic convergence detection are
not implemented. See [design and source comparison](pareto-design.md).

## Offline checks

From this checkout, with its ordinary development dependencies installed:

```sh
python -m unittest tests.test_pareto tests.test_database tests.test_island_migration tests.test_prompt_sampler tests.test_process_parallel
```

The dedicated tests use synthetic vectors, fixed model responses and a fixed
mock evaluator. They cover frontier retention, duplicate exposure, malformed
values, same-cell collisions, population pressure, checkpoint restoration,
worker context and parser wiring, migration artifacts and final output. They
do not call a model service or perform scientific evaluations.

The inherited repository workflows include real-model integration tests.
GitHub Actions is disabled on this fork; publication does not start those jobs.
