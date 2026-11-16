# Combined B3/B4 scalability and variability figure

The figure has two side-by-side groups under one figure number; each group
contains two vertically stacked flat plots sharing its x axis. Group (a) shows
protocol construction above audit for 1, 8, 32, 128 or 512 calls/request. The mist-blue
solid circles and coral dashed squares identify four/eight lifecycle events per call
in both plots, with one shared legend in the upper plot. Call-count labels appear
only on the lower plot. Both axes in (a) are logarithmic. All 10 configurations
(20 construction/audit values) remain visible. Connecting lines are visual guides.
There is one measurement per configuration, so no error bars or fitted curves.
The colored envelopes span the pointwise minimum and maximum ACROSS THE TWO EVENT
DENSITIES. They are configuration ranges, NOT repeated-run variation, SD or CI.
Pointwise min/max is used because the two construction curves cross at one call.
The construction inset shows exactly the four observations at 128 and 512 calls;
the audit inset shows exactly the four observations at 1 and 8 calls. Insets use
linear axes with visible ticks, while the main protocol axes remain logarithmic.
Each source rectangle matches its inset limits exactly and connects with one
thin gray line. Insets are lifted into the lower-right empty region, with y ticks
on the right and reserved space inside the parent frame. Tick labels are checked
to stay clear of the parent x axis and right frame. The old endpoint numbers/arrows are removed.
The group labels (a)/(b) are centered below their respective stacked plots.
The fixed native canvas is 3.50 x 2.28 inches for single-column placement.
Both groups remain side by side, with reduced horizontal and vertical gaps.
Subtitles sit just below the bottom axis labels, with clearance checked on export.
The round legend uses the full Round 1/2/3 labels. Main frames are 0.60 pt, inset
frames 0.50 pt and grids 0.35 pt. All text is Times New Roman. Compact model
labels Q3 and DS denote Qwen3 and DeepSeek-R1-Distill-Llama, respectively; the
caption defines these abbreviations. The inset endpoint ticks identify the
requested ranges without a redundant internal text label. No explanatory
footer is printed inside the image.

Group (b) shows five models, each with three TokenProv round observations.
The upper plot shows online non-LLM cost and the lower plot shows latency change.
Both share the same categorical model axis, labeled only at the bottom.
The lavender rectangular background spans that model's observed min-max round range;
the o/s/^ markers are Round 1/2/3 and the dark-indigo horizontal bar is the arithmetic mean.
The compact two-row marker/range legend in the upper plot applies to both plots. These backgrounds
are observed round ranges, not SD or confidence intervals. Model categories are
not connected by lines or continuous shaded envelopes. Non-LLM (ms) is T_online-T_llm;
Latency delta (%) is 100*(mean_pipeline_TP/mean_pipeline_Vanilla-1) within each round.
All rounds, including DeepSeek R3 +42.71%, remain visible. The panels use the full
five-model cohort of 7680 requests/61440 calls, with 128 requests/system/round.
The plotted TP points themselves summarize 1920 requests. This does not establish
uniform end-to-end improvements; shared GPU and output lengths vary. Qwen3/1.7B,
Qwen3/4B, Phi-3/3.8B, DeepSeek/8B and Qwen3/14B are discrete category labels;
DeepSeek/8B is DeepSeek-R1-Distill-Llama-8B, Phi-3/3.8B is Phi-3-mini-4k-instruct.

Construction is the complete build_honest_protocol call, including four identity
key pairs, authorization, lifecycle records, provider deployment registration,
bilateral handoffs, provider receipts, request closure, Merkle root and settlement.
It uses an in-memory synthetic fixture, not real model inference. Audit is the
host-side signature-profile protocol audit of that fixture. The benchmark excludes
model inference, network serving, evidence persistence, ledger I/O, TEE, and ZK
proof generation. It differs in scope from B2 serving timings and must not be added
to or substituted for them. A small synthetic auxiliary fee is included by the
fixture; this is not a real auxiliary model/tool call or a change to serving defaults.
All 10 audits succeed. At 512 calls/4096 events, construction is 764.819513 ms and
audit is 864.690295 ms: each phase is below one second; their sum is not.
These observations do not establish strict linearity or cross-round stability.
The lifecycle fixture is a serial causal chain; branch shape and parallel DAG
topology are not independently varied in this experiment.

The appendix table contains the original Qwen3-1.7B concurrency cohort only:
four schemes x concurrency 1/8/32 = 12 rows, 1088 measured requests, 8704 calls.
There is one original round at each configuration. The concurrency-8 entries are
the original 128-request round, not the later three-round average. At concurrency
1 each scheme has 16 requests; at 8 and 32 it has 128. Warmups are excluded.
Protocol and concurrency curves use the bundled retained observations.
Cross-model statistics are recomputed from all round-level measurements. Latency is mean complete request processing time;
throughput is successful requests/batch wall time; output rate is generated tokens/
batch wall time; actual output length is shown because the output cap alone does
not fix generation work. No unsupported p95 at n=16 or cross-round SD is invented.
These requests overlap earlier B1 data; they are not additional independent runs.

Files:
- b3_b4_scalability_variability.{pdf,png,svg}: combined main-text figure.
- b3_b4_scalability_variability_grayscale.{pdf,png}: print check.
- b3_protocol_scalability.*: compatibility copies of the same combined figure,
  replacing the former two-panel B3 figure so existing open PDFs also update.
- b3_protocol_scaling.csv: all ten original protocol observations.
- b4_model_round_values.csv: all 15 TP observations and paired Vanilla latency.
- b4_model_range_summary.csv: five min/max/mean/SD summaries for each B4 metric.
- b3_appendix_concurrency_table.tex: drop-in appendix table*, same booktabs style as B2.
- b3_appendix_section.tex: short appendix section with definitions and table input.
- b3_appendix_protocol_table.tex: five call sizes and the two event-density costs.
- b4_appendix_cross_model_table.tex: all 20 model/scheme groups, three-round mean/SD.
- b4_appendix_section.tex: B4 methodology and the complete cross-model table.
- b3_b4_appendix.tex: self-contained combined B3/B4 appendix, ready to input.
- b3_b4_appendix_preview.tex: compilable preview of both appendix sections.
- appendix_verification.json: counts and cross-checks for the appendix tables.
- b3_appendix_preview.tex: compilable IEEEtran preview wrapper.
- b3_b4_paper_integration.tex: shared figure/caption, B3(a) and B4(b) references.
- b3_paper_integration.tex: same updated integration snippet for existing use.
- b3_concurrency_original_round.csv: all twelve serving observations, unrounded.
- verification.json: scope, counts, raw-value checks and figure layout checks.

No inference, proof, network call, or server experiment is started by this script.
