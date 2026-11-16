# D figure and appendix

Regenerate using the workspace embedded Python and
artifacts/server_20261113/plot_d_evidence_settlement.py. No experiments are rerun.

- d_evidence_settlement.pdf/png/svg: 3.5-inch single-column figure; a/b above c; Times New Roman.
- d_evidence_settlement_grayscale.pdf/png: grayscale and marker/number check.
- Both PNG exports use 1200 dpi; PDF/SVG preserve vector labels and geometry.
- d_paper_integration.tex: figure at columnwidth and caption; fig:d-evidence-settlement.
- d_appendix.tex: appendix prose and table inputs; app:d-evidence-settlement.
- d_appendix_current_table.tex: current five-model storage and stage times.
- d_appendix_storage_table.tex: historical FullTrace comparison, 1--64 calls.
- d_preview.tex/pdf: standalone IEEEtran figure and appendix preview.
- d_storage_plot_values.csv, d_current_plot_values.csv: exact plotted values.
- d_figure_verification.json: source hashes, statistics and layout validation.

Panel (a) is an earlier protocol microbenchmark: FullTrace retains the same
authenticated protocol plus available raw messages/responses. It is not a full
GPU/KV/inference trace. Its absolute storage sizes are not combined with the
current implementation. Five repetitions have identical byte counts, so there
is no artificial uncertainty band.

Panels (b)/(c) use only current TokenProv signature-path measurements: five models,
three rounds, 128 non-warmup requests per round, eight sequential calls per
request, concurrency eight. Shaded intervals span the min--max of the three round
means, with all three round markers and a black mean tick. They are not confidence
intervals. Appendix tables retain sample SDs. Persistence includes serialization/hash/write/flush/fsync; protocol
audit is in-memory, not cold retrieval. The four settlement/ledger stages do
not overlap. Total ranges and means are computed from each round's total.
The 4x5 stage heatmap uses a single linear scale from 0 to 9 ms/request;
every cell states its mean. The nearly constant storage matrix has uniform-light
cells, so color does not exaggerate small cross-model differences.
Local ledger append is not a Fabric transaction or consensus.

Short model labels match the B figures. Phi-3 is Phi-3-mini-4k-instruct (3.8B);
DeepSeek is DeepSeek-R1-Distill-Llama-8B. Current bundle and ledger sizes appear
in the matrix under (b) and the appendix, not pooled with the historical curve.
Panel (a) connects the seven measured call-count configurations and shades the
gap between FullTrace and TokenProv. That gap encodes storage savings, not
uncertainty. The lower seven-cell strip states the relative savings. The older
blue per-call explanatory sentence is removed. Panel (b) uses a single-row
operation legend; the round/mean legend is shared from panel (c). Compact model
names in (b) are expanded in the figure caption and matched by panel (c)'s labels.
The constant ledger size is stated once in a merged cell.
The Saved strip is shortened vertically so Calls/request aligns with the model
row in (b). The upper subtitles share one baseline; (c) is centered on the
exported figure including its row labels. The lower group moves up accordingly.
No modeled sampling distributions are introduced for the three current rounds.
