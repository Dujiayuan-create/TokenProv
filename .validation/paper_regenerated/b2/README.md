# B2 cost breakdown

Main figure: Qwen3-1.7B, concurrency 8, eight sequential model calls/request,
four schemes, three rounds of 128 measured requests/system/round (1536 requests).
All five models are also derived for the appendix, using all 7680 formal requests.
Warmups are excluded. No inference, proof generation, or experiment rerun occurs.
Subplot labels (a)/(b) and their short titles are centered below each panel;
each panel has its own time-axis label. No titles are above plots.
The graphics have a fixed native size of 3.50 x 1.78 inches (single-column width)
with two side-by-side panels and shared scheme rows. Axis labels are 7.5 pt,
subtitles 7.7 pt, x ticks 7.1 pt, scheme labels and total labels 7.4 pt,
and internal legends 6.4 pt. Scheme names stay horizontal. Full component names
appear in one-column legends alongside the short baseline stacks. Receipts/evidence
includes provider commitments. The inter-panel frame gap is 8.82 pt, and the
subtitles sit just below their time-axis labels, with clearance checked on export.
Both panels retain the same 0--132 ms scale.
Frames are 0.65 pt and grids
0.40 pt. Muted coral/apricot/mist-blue/lavender fills use darker opaque hatches,
and TokenProv labels use deep indigo. Legends use square 0.40-pt frames and are
checked not to obscure a bar or its total label. Saving preserves the native
page size rather than changing it with a tight crop. The grayscale file is
checked for equal RGB channels in addition to a visual print-readability check.

Definitions (all quantities are wall time per request):
- Online non-LLM = T_online - T_llm.
- Remaining request processing = request_pipeline_ns - T_online. This includes
  work outside the online boundary both before and after it; it is not entirely
  background execution or entirely post-online work.
- Complete non-LLM = request_pipeline_ns - T_llm = the sum of the above two totals.
- T_llm is cumulative provider HTTP wall time, including its queue/network/service
  boundary; it is not isolated GPU compute time.
- Settlement in the figure = T_close + T_merkle + T_settle_local = T_settlement;
  the overlapping aggregate and components are never both summed.
- Other online work is the online residual after the six measured online stages.
- Other pipeline work includes wrapper/setup, checks, serialization, logging and
  other remaining unitemized time. These residuals are derived, not separately timed.

Stacks are means of three round means. Error bars are sample SD of the relevant
total over the three round means; SDs of components must not be summed for total SD.
Numbers at bar ends are the stack means in ms/request. The parts are internal
measurements, not incremental overhead relative to Vanilla. The complete B1 request
latency includes both panels. No online proof workers or TEE were used.
SignedLog/Vanilla do not implement all TokenProv stages; absent stages have no bar
segment. The CSV zeros describe absent components rather than measured zero-cost
cryptographic operations.

Files:
- b2_reference_cost_breakdown.{pdf,png,svg}: main figure, Times New Roman.
- b2_reference_cost_breakdown_grayscale.{pdf,png}: grayscale print check.
- b2_appendix_component_table.tex: drop-in table* for the appendix, five TP models,
  including all measured components, residuals, stage totals and complete total.
- b2_appendix_preview.tex: a standalone IEEEtran preview wrapper for that table.
- b2_paper_integration.tex: suggested main-figure and appendix placement snippets.
- b2_component_round_means.csv: all 60 model/scheme/round observations.
- b2_component_summary.csv: all 20 model/scheme groups, component mean and sample SD.
- b2_main_plot_values.csv: exact grouped components shown in the main figure.
- verification.json: sample counts, numeric closure, font and layout checks.

Copy the main figure and the table fragment into the paper's existing graphics /
table paths and adapt only the input paths. The appendix table is supplementary to
the main reference-model explanation. B4 should cover cross-model variability.
