# Paper artifacts

This directory contains the retained inputs and final selected outputs for
Sections B--F and the qualitative token-provenance appendix. It does not require
access to the authors' original workspace or model server to regenerate figures.
All measured rounds are retained, including the slower DeepSeek third round.

## Inspect or regenerate

From the source release root, with Python 3.11 or newer:

```bash
python paper/reproduce.py --verify-only
python -m pip install -e '.[plots]'
python paper/reproduce.py --section all
```

`--verify-only` requires only Python's standard library. It checks SHA-256 hashes
of every bundled data file and published snapshot against [manifest.json](manifest.json).
This checks file integrity, not the truth of measurements or proof validity.

Use `--section b1`, `b2`, `b3_b4`, `c`, `d`, `e`, `f`, or `cases` for one section.
`--output /path/to/new-outputs` changes the output root. Inputs under `data/` and
the final snapshots under `published/` are protected from use as output paths.
The default output is `generated/`, which is excluded from the source release.

The generators use the final paper's plotting geometry, colors, hatches and font
sizes. They require Times New Roman, with Microsoft YaHei additionally required
for the original Chinese provenance examples. Windows normally supplies these
fonts. On another system, supply a directory of legally installed font files:

```bash
python paper/reproduce.py --section all --font-dir /path/to/fonts
```

Fonts and third-party plotting packages are not redistributed. The retained
snapshots remain viewable without installing fonts. Rendering used Matplotlib
3.10.0, NumPy 2.5.4 and Pillow 13.0.0; the exact locally validated versions are
also recorded in the release validation report. Different renderer versions can
change text placement or PDF metadata while leaving numeric results unchanged.

## Section-to-artifact map

| Paper result | Retained inputs | Generator | Final output in `published/` |
| --- | --- | --- | --- |
| B1 request latency and throughput | [7,680 request observations](data/b1/request_latency_data.csv), [60 round rows](data/five_model_three_round_metrics.csv) | [plot_b1.py](scripts/plot_b1.py) | [B1](published/b1/): `b1_five_model_latency_throughput.pdf` |
| B2 component costs | [7,680 request timing extracts](data/b2/request_measurements.jsonl), [retained round means](data/b2/component_round_means.csv) | [plot_b2.py](scripts/plot_b2.py) | [B2](published/b2/): figure and five-model component appendix |
| B3 protocol and serving concurrency | [10 protocol configurations](data/b3_b4/protocol_scaling.csv), [12 concurrency observations](data/b3_b4/concurrency.csv) | [plot_b3_b4.py](scripts/plot_b3_b4.py) | [B3/B4](published/b3_b4/): combined figure and appendix |
| B4 cross-model variation | [All round metrics](data/five_model_three_round_metrics.csv), [retained summary](data/five_model_three_round_variability.csv) | [plot_b3_b4.py](scripts/plot_b3_b4.py) | [B3/B4](published/b3_b4/): five-model range and mean/SD tables |
| C scoped proofs, deferred proving and audit rates | [Round metrics](data/c/round_metrics.csv), [operation measurements](data/c/measurements.jsonl), [signing measurements](data/c/signing/) | [plot_c.py](scripts/plot_c.py), [write_c.py](scripts/write_c.py) | [C](published/c/): final figure, comparison table, complete text and appendix |
| D evidence and settlement | [60 current round rows](data/d/current_round_data.csv), [historical storage scan](data/d/E1_storage_vs_calls.csv) | [plot_d.py](scripts/plot_d.py) | [D](published/d/): final figure and appendix tables |
| E security and accountability | [Current adaptive observations](data/e/security.jsonl), [historical trials](data/e/) | [tables_e.py](scripts/tables_e.py), [compose_e.py](scripts/compose_e.py) | [E](published/e/): final combined single-column table and appendix; the superseded E figure is omitted |
| F rule gating and cumulative validation | [Rule-gating trials](data/f/G1_security_ablation.csv), [25 cumulative cost rows](data/f/G2_performance_ablation.csv) | [plot_f.py](scripts/plot_f.py), [tables_f.py](scripts/tables_f.py) | [F](published/f/): waterfall/variation figure, rule table and appendices |
| Qualitative provenance appendix | [Three recorded display extracts](data/cases/case_figure_data.json) | [plot_cases.py](scripts/plot_cases.py) | [Cases](published/cases/): final single-column example figure |

`published/` contains immutable final snapshots; it is not a generated cache.
PDF and SVG are vector figures; PNG is supplied for preview and direct reuse at
the final export resolution. LaTeX table fragments use `booktabs`, `graphicx`,
and `xcolor` with the `table` option. Adapt `\input` and `\includegraphics` paths
to the manuscript. Static prose is retained as a manuscript fragment; generators
recompute numerical tables, not natural-language analysis from scratch.

## Data provenance and statistical meaning

[SOURCE_PROVENANCE.json](SOURCE_PROVENANCE.json) records original relative source
names and SHA-256 values, plus transformations made for portability. Directly
copied measurement files retain their original bytes. The B2 request dataset
selects unsigned timing/count fields from every formal request; it omits messages,
endpoint addresses and full evidence bundles, without changing timings. The
qualitative cases are the original figure's display extracts, not full independently
auditable request archives. Their receipt/settlement fields and recorded audit
statuses are retained; the renderer checks arithmetic and displayed token counts.

The live-serving cohort comprises five models, four schemes, three rounds and
128 requests per model/scheme/round: 7,680 requests and 61,440 model calls. B1's
request index concatenates three rounds. Throughput is completed requests divided
by a batch's wall time, not a new per-request instantaneous measurement. B2
aggregates request components within each round and then reports mean/sample SD
across round means. B3 protocol envelopes span event-density configurations, not
repeated-run uncertainty. B3 concurrency has one original round per configuration.
B4 ranges span all three round observations; shared GPU conditions and generated
lengths vary. Historical D/E/F fixtures are explicitly separate from this cohort.

C uses retained scoped Groth16 CPU measurements from the B300 host and a separate
local TEE-based signing component. It does not establish full confidential LLM
inference. C's verification summaries record the original run's checks; plotting
does not rerun cryptographic proofs. The signing dataset includes six public
signature examples that can be checked with the portable signing report tool.
For example, from the release root:

```bash
python experiments/tee_signing/report.py paper/data/c/signing --output results/retained-signing-check
```

Full proof files, proving keys, full server archives and models are excluded.

The reproduction runner compares regenerated CSV values against retained snapshots
with numeric tolerance and checks required output files. It never selects favorable
rounds or replaces a measured value to improve a figure. See
[the experiment coverage matrix](../docs/EXPERIMENT_COVERAGE.md) to run fresh
measurements, and [ASSURANCE.md](../docs/ASSURANCE.md) for assurance boundaries.
