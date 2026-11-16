# C-section main figure and component table

Single-column native width: 3.50 inches. Figure: 3.50 x 2.38 inches.
PNG exports are 1200 dpi; PDF and SVG retain vector text and geometry.
Latin text uses Times New Roman. Frames: 0.65 pt. Panel titles are below axes.
Colors, fine grid lines, markers, and internal legends match B2/B3--B4.
Upper/lower plot frames align within each main panel. Y-axis titles use a
compact 0.6 pt gap from their own tick labels; y-tick padding is 1.0 pt.
The column gap is 25.2 pt. Outer margins, row gaps, and subtitle spacing are
compact; renderer checks keep neighboring labels and plot frames separate.
The two upper y-axes and the lower-left time x-axis use explicit x10^3 scale
factors with their original millisecond units. All underlying data are unchanged.

The left panel shows paired online-cost bars on a common linear scale, with
Deferred enlarged on a clearly labelled 0--13 ms linear inset. The lower panel
uses audit-cost dumbbells, with calls/request as rows and audit time as columns.
Coral denotes Immediate; blue denotes Deferred. The shared mode legend sits
inside the lower left plot. Online time includes witness preparation/persistence.
The 99.28% annotation refers only to the eight-call online protocol time.

The right upper plot separates proving and verification work at all six actual
audit rates. The label Audit cost (ms/request) denotes the sum of individual
operation wall times divided by all 100 batch requests, not aggregate CPU time.
The lower label Audit time (s) denotes actual elapsed two-worker completion time.
Eight calls/request.
The right lower scatter plot adds actual elapsed audit batch time and proof-job
counts (8, 40, 80, 200, 400, 800). Gray dots retain all three round observations;
their horizontal display offsets are -2, 0, and +2 pt for visibility, with all
measured times unchanged. Hollow diamonds and error bars show their means and
sample SDs. Two proof workers.
All scales are linear. Error bars are sample SD, not confidence intervals.
Proving work, elapsed batch time, and proof-job count are distinct quantities.

The table groups server CPU scoped proofs and local TEE-based receipt signing.
Proof size is serialized JSON bytes; signing uses paired ECDSA-P256 controls.
The table preview is an image convenience; insert the .tex table in the paper.
The TEE signing paired-increment SD is recomputed from three paired session differences.

Files:
- c_deferred_proving_audit_rate.{png,pdf,svg}: main figure.
- c_deferred_proving_audit_rate_grayscale.{png,pdf}: grayscale print check.
- c_assurance_components_table.tex: drop-in single-column table.
- c_assurance_components_table_preview.{png,pdf,svg}: visual table preview.
- c_paper_integration.tex: main-figure and table inclusion snippet.
- c_preview.tex: optional standalone IEEEtran preview wrapper.
- c2_phase_plot_values.csv, c3_audit_rate_plot_values.csv: plotted means and SDs.
- c_assurance_components.csv: exact table inputs.
- c_figure_verification.json: data hashes, units, fonts, and containment checks.
