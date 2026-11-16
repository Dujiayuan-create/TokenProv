"""Recompute F-section tables from retained protocol microbenchmark CSVs.

No benchmark is executed. Relative paths are anchored to this repository.
"""
from __future__ import annotations

import csv
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from paper_paths import DATA, PAPER, output_dir, read_csv
HERE = DATA
ROOT = PAPER
OUT = output_dir('f')
HERE = OUT
SOURCES = {
    'rule_gating': DATA / 'f' / 'G1_security_ablation.csv',
    'cumulative_cost': DATA / 'f' / 'G2_performance_ablation.csv',
}

VARIANTS = [
    ("Vanilla", "Empty baseline"),
    ("Vanilla + Lifecycle", "+ Lifecycle"),
    ("+ Bilateral Handoff", "+ Handoff"),
    ("+ Provider Binding", "+ Provider"),
    ("TokenProv-Full", "+ Settlement"),
]
RULES = [
    ("R_life", "TokenProv-w/o-Life", "A2", "Call insertion"),
    ("R_hand", "TokenProv-w/o-Handoff", "A6", "Handoff tampering"),
    ("R_prov", "TokenProv-w/o-R_link", "A12", "Evidence splicing"),
    ("R_set", "TokenProv-w/o-ExactSettlement", "A14", "Bill manipulation"),
]


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(name, rows):
    with (HERE / name).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def mean_sd(values):
    return statistics.fmean(values), statistics.stdev(values)


def fmt(mean, sd, places=3):
    return f"{mean:.{places}f} $\\pm$ {sd:.{places}f}"


def tex_table(caption, label, columns, headings, body, sections=None):
    n = len(headings)
    lines = [r"\begin{table}[!t]", r"\centering", "\\caption{" + caption + "}",
             "\\label{" + label + "}", r"\footnotesize",
             r"\renewcommand{\arraystretch}{1.08}",
             r"\setlength{\tabcolsep}{3pt}",
             "\\begin{tabular*}{\\columnwidth}{@{\\hspace{3pt}}" + columns[0]
             + "@{\\extracolsep{\\fill}}" + columns[1:] + "@{\\hspace{3pt}}}",
             r"\toprule", " & ".join(headings) + r" \\",
             r"\midrule"]
    for row in body:
        lines.append(" & ".join(row) + r" \\")
    lines.extend([r"\bottomrule", r"\end{tabular*}", r"\end{table}", ""])
    return "\n".join(lines)


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    security = read_csv(SOURCES["rule_gating"])
    perf = read_csv(SOURCES["cumulative_cost"])
    assert len(security) == 4000
    assert len(perf) == 25
    rule_rows = []
    for relation, variant, attack, attack_name in RULES:
        full = [r for r in security if r["variant"] == "TokenProv-Full" and r["attack_id"] == attack]
        relaxed = [r for r in security if r["variant"] == variant and r["attack_id"] == attack]
        assert len(full) == len(relaxed) == 500
        assert {(r["seed"], r["trial"]) for r in full} == {(r["seed"], r["trial"]) for r in relaxed}
        assert {r["failed_relation_before_ablation_gate"] for r in full + relaxed} == {relation}
        assert sum(r["audit_accepted"] == "True" for r in full) == 0
        assert sum(r["audit_accepted"] == "True" for r in relaxed) == 500
        rule_rows.append(dict(relation=relation, attack_id=attack, attack=attack_name,
                              source_variant=variant, full_accepted=0, full_tested=500,
                              relaxed_accepted=500, relaxed_tested=500))
    write_csv("f_rule_gating.csv", rule_rows)

    run_rows, summary_rows, paired_rows = [], [], []
    by_run = {}
    for row in perf:
        key = (int(row["run"]), row["variant"])
        assert key not in by_run
        assert int(row["sample_count"]) == 1000
        by_run[key] = row
    for index, (variant, label) in enumerate(VARIANTS):
        rows = [by_run[(run, variant)] for run in range(1, 6)]
        wall = [float(r["mean_online_latency_ms"]) for r in rows]
        cpu = [float(r["mean_cpu_time_ms"]) for r in rows]
        size = [float(r["bytes_per_request"]) for r in rows]
        wall_mean, wall_sd = mean_sd(wall)
        cpu_mean, cpu_sd = mean_sd(cpu)
        bytes_mean, bytes_sd = mean_sd(size)
        summary_rows.append(dict(index=index, source_variant=variant, label=label,
                                 rounds=5, measured_samples=5000,
                                 wall_mean_ms=wall_mean, wall_sd_ms=wall_sd,
                                 cpu_mean_ms=cpu_mean, cpu_sd_ms=cpu_sd,
                                 bytes_mean=bytes_mean, bytes_sd=bytes_sd,
                                 payload_mean_kib=bytes_mean / 1024, payload_sd_kib=bytes_sd / 1024))
        for r in rows:
            run_rows.append(dict(index=index, source_variant=variant, label=label,
                                 run=int(r["run"]), seed=int(r["seed"]), sample_count=1000,
                                 wall_ms=float(r["mean_online_latency_ms"]),
                                 cpu_ms=float(r["mean_cpu_time_ms"]),
                                 selected_bytes=float(r["bytes_per_request"]),
                                 selected_kib=float(r["bytes_per_request"]) / 1024))
        for run, row in enumerate(rows, start=1):
            previous = by_run[(run, VARIANTS[index-1][0])] if index else None
            paired_rows.append(dict(index=index, label=label, run=run, seed=int(row["seed"]),
                                    wall_increment_ms=float(row["mean_online_latency_ms"]) -
                                    (float(previous["mean_online_latency_ms"]) if previous else 0),
                                    payload_increment_bytes=float(row["bytes_per_request"]) -
                                    (float(previous["bytes_per_request"]) if previous else 0)))
    increment_summary = []
    for index, (_, label) in enumerate(VARIANTS):
        rows = [r for r in paired_rows if r["index"] == index]
        wall_mean, wall_sd = mean_sd([r["wall_increment_ms"] for r in rows])
        bytes_mean, bytes_sd = mean_sd([r["payload_increment_bytes"] for r in rows])
        increment_summary.append(dict(index=index, label=label, wall_mean_ms=wall_mean,
                                      wall_sd_ms=wall_sd, bytes_mean=bytes_mean, bytes_sd=bytes_sd,
                                      payload_mean_kib=bytes_mean / 1024, payload_sd_kib=bytes_sd / 1024))
    assert abs(sum(r["wall_mean_ms"] for r in increment_summary) - summary_rows[-1]["wall_mean_ms"]) < 1e-10
    assert sum(r["bytes_mean"] for r in increment_summary) == summary_rows[-1]["bytes_mean"]
    write_csv("f_cost_rounds.csv", run_rows)
    write_csv("f_cost_summary.csv", summary_rows)
    write_csv("f_paired_increments.csv", paired_rows)
    write_csv("f_increment_summary.csv", increment_summary)

    rules_tex = tex_table(
        "Post-audit acceptance-rule sensitivity (accepted/tested).",
        "tab:f-rule-gating", "llcc",
        [r"\shortstack{\textbf{Relaxed}\\\textbf{rule}}", r"\textbf{Attack}",
         r"\shortstack{\textbf{Full}\\\textbf{decision}}", r"\shortstack{\textbf{Relaxed}\\\textbf{decision}}"],
        [["$R_{\\mathrm{" + r["relation"][2:] + "}}$", r["attack"], "0/500", "500/500"] for r in rule_rows])
    (HERE / "f_main_rule_table.tex").write_text(rules_tex, encoding="utf-8")

    cost_body = []
    for r in summary_rows:
        p = 5 if r["index"] == 0 else 3
        cost_body.append(["Baseline" if r["index"] == 0 else r["label"], fmt(r["wall_mean_ms"], r["wall_sd_ms"], p),
                          fmt(r["cpu_mean_ms"], r["cpu_sd_ms"], p),
                          fmt(r["payload_mean_kib"], r["payload_sd_kib"], 4 if r["index"] == 0 else 3)])
    cost_tex = tex_table(
        "Historical cumulative validation configurations (mean $\\pm$ sample SD "
        "of five run means; 1,000 measurements per configuration per run).",
        "tab:f-cumulative-cost", "lccc",
        [r"\shortstack{\textbf{Cumulative}\\\textbf{checks}}",
         r"\shortstack{\textbf{Wall time}\\\textbf{(ms)}}",
         r"\shortstack{\textbf{CPU time}\\\textbf{(ms)}}",
         r"\shortstack{\textbf{Selected payload}\\\textbf{(KiB)}}"], cost_body)
    (HERE / "f_appendix_cost_table.tex").write_text(cost_tex, encoding="utf-8")

    increment_body = []
    for r in increment_summary:
        p = 5 if r["index"] == 0 else 3
        increment_body.append([r["label"], fmt(r["wall_mean_ms"], r["wall_sd_ms"], p),
                               fmt(r["payload_mean_kib"], r["payload_sd_kib"], 4 if r["index"] == 0 else 3)])
    r = summary_rows[-1]
    increment_body.append([r"\textbf{Total}", fmt(r["wall_mean_ms"], r["wall_sd_ms"]),
                           fmt(r["payload_mean_kib"], r["payload_sd_kib"])])
    increment_tex = tex_table(
        "Marginal costs along the cumulative validation sequence. "
        "Stage differences are computed within matched runs before aggregation; "
        "the empty baseline is measured relative to zero.",
        "tab:f-paired-increments", "lcc",
        [r"\textbf{Added checks}", r"\shortstack{\textbf{Wall increment}\\\textbf{(ms)}}",
         r"\shortstack{\textbf{Payload increment}\\\textbf{(KiB)}}"], increment_body)
    (HERE / "f_appendix_increment_table.tex").write_text(increment_tex, encoding="utf-8")

    appendix = r"""\section{Additional Acceptance-Rule and Validation Measurements}
\label{app:f-ablation}

\noindent\textbf{Post-audit rule-gating controls.}
Table~\ref{tab:f-rule-gating} uses four historical attack templates: call
insertion (A2), handoff tampering (A6), cross-call evidence splicing (A12),
and aggregate bill manipulation (A14).
Each attack--decision configuration has five seed fixtures and 100 repetitions
per seed, yielding 500 observations per configuration and 4,000 records overall.
All 4,000 candidates first undergo the complete protocol audit.
For each relaxed decision, rejection is overridden when the first failed
relation matches the selected gate; all other decisions are unchanged.
The four controls relax $R_{\mathrm{life}}$, $R_{\mathrm{hand}}$,
$R_{\mathrm{prov}}$, and $R_{\mathrm{set}}$, respectively.
The archived variant named \texttt{w/o-R\_link} relaxes the entire
$R_{\mathrm{prov}}$ rejection gate, rather than removing only the linking check.
These controls measure acceptance-rule sensitivity and do not measure a
reimplemented protocol with a component removed.
Only the four matched attack--rule combinations were measured.

\noindent\textbf{Cumulative validation configurations.}
Table~\ref{tab:f-cumulative-cost} uses eight calls and 32 lifecycle events per
request. Five seed runs each execute the five cumulative configurations, with
100 warmups and 1,000 measured validations per configuration.
There are 25,000 measured validations in total.
Evidence is constructed before timing.
The empty baseline creates a request-ID payload; later configurations validate
lifecycle records, bilateral handoffs, provider receipts, and settlement in
that order, while selecting the corresponding payload fields.
Timing ends before canonical serialization of the selected payload.
Authorization verification is not included in these four validation stages.
Model inference, evidence generation and signing, serialization, persistence,
ledger operations, and proof generation are outside this timing boundary.
Selected payload size is the subsequent canonical encoding of those fields,
not the full persisted evidence bundle or a wire-protocol footprint.

\input{f_appendix_cost_table.tex}

\noindent\textbf{Matched increments and aggregation.}
For stage $j$ and seed run $r$, we compute the wall-time increment
$\Delta T_{j,r}=T_{j,r}-T_{j-1,r}$ and the corresponding byte difference
before taking their means and sample standard deviations across the five runs.
The empty baseline is measured relative to zero.
These differences characterize the specified activation order and are not
independent measurements of isolated components.
The total is aggregated directly from the final cumulative configuration;
its standard deviation is not the sum of stage standard deviations.
All reported deviations describe variation of five run means, rather than
the pooled variation of 25,000 individual validations.
Payload sizes are identical across the five runs of each configuration.
The archived RSS values come from a shared, continuously running process and
are omitted because they do not isolate configuration-specific memory costs.

\input{f_appendix_increment_table.tex}
"""
    (HERE / "f_appendix.tex").write_text(appendix, encoding="utf-8")
    appendix_full = appendix.replace(r"\input{f_appendix_cost_table.tex}", cost_tex)
    appendix_full = appendix_full.replace(r"\input{f_appendix_increment_table.tex}", increment_tex)
    (HERE / "f_appendix_full.tex").write_text(appendix_full, encoding="utf-8")
    summary = dict(
        sources={name: dict(path=str(path.relative_to(ROOT)), bytes=path.stat().st_size,
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for name, path in SOURCES.items()},
        rules=rule_rows, costs=summary_rows, increments=increment_summary,
        counts=dict(rule_control_rows=len(security), cost_run_means=len(perf),
                    validations=sum(int(r["sample_count"]) for r in perf),
                    runs=5, warmups_per_configuration=100, samples_per_configuration=1000),
        scope=dict(measurement_type="Historical protocol microbenchmark",
                   rule_gating="Complete audit followed by first-failure acceptance override",
                   performance="In-process cumulative validation; serialization excluded",
                   payload="Selected fields only; not current full bundle",
                   deviations="Sample SD across five run means",
                   memory="RSS omitted: not configuration-isolated"))
    (HERE / "f_data_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(status="verified", rows=len(security), validations=25000,
                          total_wall_ms=summary_rows[-1]["wall_mean_ms"],
                          total_payload_kib=summary_rows[-1]["payload_mean_kib"])))


if __name__ == "__main__":
    main()
