"""Plot preserved protocol scaling and export the original concurrency cohort."""
import csv
import json
import math
import os
from pathlib import Path
import statistics
import sys

from paper_paths import DATA, PAPER, output_dir, read_csv
HERE = DATA
ROOT = PAPER
OUT = output_dir('b3_b4')
PROTOCOL_RUN = '20261113T072441Z_a1d078d06917'
CALLS = [1, 8, 32, 128, 512]
FACTORS = [4, 8]
SYSTEMS = ['Vanilla', 'SignedLog', 'SignedLogPlus', 'TokenProv-Full']
LABELS = ['Vanilla', 'SignedLog', 'SignedLog+', 'TokenProv']
MODELS = [
    ('qwen3-1.7b', 'Qwen3\n1.7B'), ('qwen3-4b', 'Qwen3\n4B'),
    ('phi-3-mini-4k-instruct', 'Phi-3\n3.8B'),
    ('deepseek-r1-distill-llama-8b', 'DeepSeek\n8B'),
    ('qwen3-14b', 'Qwen3\n14B'),
]



def long_path(path):
    return Path('\\\\?\\' + str(path.resolve())) if sys.platform == 'win32' else path


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def protocol_data():
    rows = read_csv(DATA / 'b3_b4/protocol_scaling.csv', numeric=True)
    assert len(rows)==10
    write_csv(OUT / 'b3_protocol_scaling.csv', rows)
    return rows



def concurrency_data():
    rows = read_csv(DATA / 'b3_b4/concurrency.csv', numeric=True)
    assert len(rows)==12 and sum(r['measured_requests'] for r in rows)==1088
    write_csv(OUT / 'b3_concurrency_original_round.csv',rows)
    return rows



def cross_model_data():
    with (HERE / 'five_model_three_round_metrics.csv').open(encoding='utf-8-sig', newline='') as stream:
        source = list(csv.DictReader(stream))
    with (HERE / 'five_model_three_round_variability.csv').open(encoding='utf-8-sig', newline='') as stream:
        summaries = list(csv.DictReader(stream))
    assert len(source) == 60 and sum(int(row['attempted_requests']) for row in source) == 7680
    assert all(int(row['failed_requests']) == 0 for row in source)
    rows, aggregate = [], []
    for model, _ in MODELS:
        for rnd in (1, 2, 3):
            selected = [row for row in source if row['model'] == model and int(row['round']) == rnd]
            assert len(selected) == 4
            tp = next(row for row in selected if row['system'] == 'TokenProv-Full')
            vanilla = next(row for row in selected if row['system'] == 'Vanilla')
            delta = 100 * (float(tp['mean_pipeline_ms']) / float(vanilla['mean_pipeline_ms']) - 1)
            assert math.isclose(delta, float(tp['pipeline_delta_vs_vanilla_percent']), abs_tol=1e-9)
            rows.append({'model': model, 'round': rnd, 'online_non_llm_ms': float(tp['mean_online_non_llm_ms']),
                'latency_change_percent': delta, 'measured_requests_per_scheme': int(tp['attempted_requests']),
                'tp_mean_pipeline_ms': float(tp['mean_pipeline_ms']),
                'vanilla_mean_pipeline_ms': float(vanilla['mean_pipeline_ms']),
                'tp_output_tokens_per_call': float(tp['mean_output_tokens_per_call']),
                'gpu_node': tp['gpu_node'], 'source': tp['source'], 'run_id': tp['run_id']})
        cohort = [row for row in rows if row['model'] == model]
        official = next(row for row in summaries if row['model'] == model and row['system'] == 'TokenProv-Full')
        summary = {'model': model, 'rounds': 3}
        for key, official_key in [('online_non_llm_ms', 'mean_online_non_llm_ms'),
                                  ('latency_change_percent', 'pipeline_delta_vs_vanilla_percent')]:
            values = [row[key] for row in cohort]
            for suffix, value, official_suffix in [
                ('mean', statistics.mean(values), 'mean'), ('sample_sd', statistics.stdev(values), 'sample_sd'),
                ('min', min(values), 'min'), ('max', max(values), 'max')]:
                assert math.isclose(value, float(official[official_key + '_' + official_suffix]), abs_tol=1e-8)
                summary[key + '_' + suffix] = value
        aggregate.append(summary)
    assert len(rows) == 15
    write_csv(OUT / 'b4_model_round_values.csv', rows)
    write_csv(OUT / 'b4_model_range_summary.csv', aggregate)
    return rows


def appendix(rows):
    text = [
        '% Requires booktabs and xcolor with the table option (or colortbl).',
        r'\begin{table*}[!t]', r'\centering',
        r'\caption{Concurrency measurements for Qwen3-1.7B (one original round per configuration).}',
        r'\label{tab:b3-concurrency}', r'\footnotesize',
        r'\renewcommand{\arraystretch}{1.10}',
        r'\begin{tabular*}{\textwidth}{@{\hspace{4pt}}l@{\extracolsep{\fill}}cccccc@{\hspace{4pt}}}',
        r'\toprule',
        r'\textbf{Scheme} & \textbf{Requests} & \shortstack{\textbf{Latency}\\\textbf{(s)}} & \shortstack{\textbf{Throughput}\\\textbf{(req/s)}} & \shortstack{\textbf{Output rate}\\\textbf{(tokens/s)}} & \shortstack{\textbf{Online non-LLM}\\\textbf{(ms)}} & \shortstack{\textbf{Output length}\\\textbf{(tokens/call)}} \\',
    ]
    for concurrency in (1, 8, 32):
        text += [r'\midrule', r'\rowcolor{gray!12}',
            r'\multicolumn{7}{l}{\textbf{Concurrency ' + str(concurrency) + r'}} \\']
        for system, label in zip(SYSTEMS, LABELS):
            row = next(row for row in rows if row['concurrency'] == concurrency and row['system'] == system)
            if system == 'TokenProv-Full':
                label = r'\textbf{\textsc{TokenProv}}'
            text.append(label + ' & {measured_requests:d} & {mean_pipeline_s:.3f} & '
                '{throughput_requests_s:.3f} & {generated_tokens_s:.2f} & '
                '{online_non_llm_ms:.2f} & {actual_output_tokens_per_call:.2f}'.format(**row) + r' \\')
    text += [r'\bottomrule', r'\end{tabular*}', r'\end{table*}', '']
    (OUT / 'b3_appendix_concurrency_table.tex').write_text('\n'.join(text), encoding='utf-8')
    section = r'''\section{Additional Concurrency Measurements}
\label{app:b3-concurrency}
Table~\ref{tab:b3-concurrency} reports the four schemes using Qwen3-1.7B with
eight sequential model calls per request and concurrency 1, 8, or 32.
Each configuration uses one original round, with 16 measured requests per scheme
at concurrency 1 and 128 at concurrency 8 and 32; warmups are excluded.
The concurrency-8 rows use the original round, rather than the three-round
aggregates reported elsewhere. Latency is mean complete request processing time;
output rate is the total generated tokens divided by batch wall time.
Online non-LLM time is $T_{\mathrm{online}}-T_{\mathrm{llm}}$, where
$T_{\mathrm{llm}}$ accumulates model-service HTTP wall time.
Every call has 2,048 input tokens and an output limit of 256 tokens, while
actual generated lengths vary as shown. In particular, \textsc{TokenProv}
averages 34.23 tokens/call at concurrency 8 and 64.90 at concurrency 32.
The configurations therefore have different realized output workloads;
request throughput alone does not isolate the effect of concurrency.
No cross-round error bars are reported for this single-round comparison.
\input{b3_appendix_concurrency_table.tex}
'''
    (OUT / 'b3_appendix_section.tex').write_text(section, encoding='utf-8')
    preview = r'''\documentclass[conference]{IEEEtran}
\usepackage{booktabs}
\usepackage[table]{xcolor}
\usepackage{mathptmx}
\begin{document}
\onecolumn
\appendices
\input{b3_appendix_section.tex}
\end{document}
'''
    (OUT / 'b3_appendix_preview.tex').write_text(preview, encoding='utf-8')
    main_figure = r'''% One main-text figure shared by B3 and B4. Requires graphicx.
\begin{figure}[!t]
  \centering
  \includegraphics[width=\columnwidth]{b3_b4_scalability_variability.pdf}
  \caption{Protocol scalability and cross-model variability.
  (a) CPU construction (top) and audit (bottom) with four or eight events per call,
  sharing the call-count axis;
  shaded envelopes span those two configurations, with one measurement per point.
  Both axes in (a) are logarithmic;
  model inference and persistence are excluded.
  Linear-scale insets show 128--512 calls for construction and 1--8 calls for audit.
  (b) TokenProv online non-LLM time (top) and request latency change relative to Vanilla
  within the same round (bottom), sharing the model axis. In (b), shading spans the minimum and maximum of
  three round observations per model; markers show the individual rounds and
  dark horizontal bars show their arithmetic mean. Shading is not a confidence interval.
  Q3 denotes Qwen3; DS denotes DeepSeek-R1-Distill-Llama.}
  \label{fig:b3-b4-performance}
  \label{fig:b3-protocol-scalability}
\end{figure}

% B3: Fig.~\ref{fig:b3-b4-performance}(a).
% B4: Fig.~\ref{fig:b3-b4-performance}(b).
% Additional concurrency measurements appear in Appendix~\ref{app:b3-concurrency}.

% In the existing appendix, after \appendices:
% \input{b3_appendix_section.tex}
'''
    (OUT / 'b3_paper_integration.tex').write_text(main_figure, encoding='utf-8')
    (OUT / 'b3_b4_paper_integration.tex').write_text(main_figure, encoding='utf-8')


def extended_appendix(protocol):
    """Export complete drop-in appendix sections without calling any model."""
    protocol_table = [
        r'\begin{table*}[!t]', r'\centering',
        r'\caption{Protocol construction and audit costs (one measurement per configuration).}',
        r'\label{tab:b3-protocol-scaling}', r'\footnotesize',
        r'\renewcommand{\arraystretch}{1.10}',
        r'\begin{tabular*}{\textwidth}{@{\hspace{4pt}}c@{\extracolsep{\fill}}cccc@{\hspace{4pt}}}',
        r'\toprule',
        r'\textbf{Calls/request} & \multicolumn{2}{c}{\textbf{4 events/call}} & \multicolumn{2}{c}{\textbf{8 events/call}} \\',
        r'\cmidrule(lr){2-3}\cmidrule(lr){4-5}',
        r' & \textbf{Build (ms)} & \textbf{Audit (ms)} & \textbf{Build (ms)} & \textbf{Audit (ms)} \\',
        r'\midrule',
    ]
    for calls in CALLS:
        values = []
        for factor in FACTORS:
            row = next(row for row in protocol if row['calls_per_request'] == calls and row['events_per_call'] == factor)
            values.extend([row['construction_ms'], row['audit_ms']])
        protocol_table.append(str(calls) + ' & ' + ' & '.join('{:.2f}'.format(value) for value in values) + r' \\')
    protocol_table += [r'\bottomrule', r'\end{tabular*}', r'\end{table*}', '']
    protocol_tex = '\n'.join(protocol_table)
    (OUT / 'b3_appendix_protocol_table.tex').write_text(protocol_tex, encoding='utf-8')
    b3_section = r'''\section{Additional Scalability Measurements}
\label{app:b3-scalability}
\label{app:b3-concurrency}

\noindent\textbf{Protocol scaling.}
Table~\ref{tab:b3-protocol-scaling} reports the ten CPU protocol configurations
underlying the scalability curves: five call counts and two lifecycle-event
densities. Construction includes in-memory protocol generation and identity
initialization; audit checks the resulting signature-profile transcript.
Model inference, evidence persistence, ledger I/O, and proof generation are excluded.
Each configuration is measured once. The shaded envelopes in the main figure
therefore span configurations, rather than repeated-run uncertainty.
At 512 calls and 4,096 events, construction and audit take 764.82 and 864.69\,ms,
respectively.
\input{b3_appendix_protocol_table.tex}

\noindent\textbf{Serving concurrency.}
Table~\ref{tab:b3-concurrency} reports the original Qwen3-1.7B concurrency
measurements. Each scheme uses one measured round per configuration, with 16
requests at concurrency 1 and 128 at concurrency 8 and 32; warmups are excluded.
The concurrency-8 entries are the original round, rather than the later
three-round averages; these observations are reused in the cross-model cohort.
Each request comprises eight sequential calls, with
2,048 input tokens per call and an output limit of 256 tokens.
Latency is mean request processing time, excluding waiting for a worker slot.
Request and output throughput
are completed requests and generated tokens, respectively, divided by batch wall time.
Online non-LLM time is $T_{\mathrm{online}}-T_{\mathrm{llm}}$, where
$T_{\mathrm{llm}}$ accumulates model-service HTTP wall time.
Actual output length is included because the realized generation workload differs
across concurrency settings; request throughput alone does not isolate concurrency.
\input{b3_appendix_concurrency_table.tex}
'''
    (OUT / 'b3_appendix_section.tex').write_text(b3_section, encoding='utf-8')
    with (HERE / 'five_model_three_round_metrics.csv').open(encoding='utf-8-sig', newline='') as stream:
        observations = list(csv.DictReader(stream))
    with (HERE / 'five_model_three_round_variability.csv').open(encoding='utf-8-sig', newline='') as stream:
        official = list(csv.DictReader(stream))
    assert len(observations) == 60 and sum(int(row['attempted_requests']) for row in observations) == 7680
    table = [
        r'\begin{table*}[!t]', r'\centering',
        r'\caption{Cross-model performance (mean $\pm$ sample SD across three rounds; 128 measured requests per scheme per round).}',
        r'\label{tab:b4-cross-model}', r'\footnotesize',
        r'\renewcommand{\arraystretch}{1.10}',
        r'\begin{tabular*}{\textwidth}{@{\hspace{4pt}}l@{\extracolsep{\fill}}ccccc@{\hspace{4pt}}}',
        r'\toprule',
        r'\textbf{Scheme} & \shortstack{\textbf{Latency}\\\textbf{(s)}} & \shortstack{\textbf{Throughput}\\\textbf{(req/s)}} & \shortstack{\textbf{Online non-LLM}\\\textbf{(ms)}} & \shortstack{\textbf{Latency change}\\\textbf{(\%)}} & \shortstack{\textbf{Output length}\\\textbf{(tokens/call)}} \\',
    ]
    model_names = [r'Qwen3-1.7B', r'Qwen3-4B', r'Phi-3-mini-4k-instruct (3.8B)',
                   r'DeepSeek-R1-Distill-Llama-8B', r'Qwen3-14B']
    checked = 0
    for (model, _), model_name in zip(MODELS, model_names):
        table += [r'\midrule', r'\rowcolor{gray!12}',
                  r'\multicolumn{6}{l}{\textbf{' + model_name + r'}} \\']
        for system, label in zip(SYSTEMS, LABELS):
            cohort = [row for row in observations if row['model'] == model and row['system'] == system]
            assert sorted(int(row['round']) for row in cohort) == [1, 2, 3]
            assert all(int(row['successful_requests']) == 128 and int(row['failed_requests']) == 0 for row in cohort)
            reference = next(row for row in official if row['model'] == model and row['system'] == system)
            for row in cohort:
                vanilla = next(v for v in observations if v['model'] == model and v['round'] == row['round']
                               and v['system'] == 'Vanilla')
                expected = 100 * (float(row['mean_pipeline_ms']) / float(vanilla['mean_pipeline_ms']) - 1)
                assert math.isclose(expected, float(row['pipeline_delta_vs_vanilla_percent']), abs_tol=1e-8)
            cells = []
            for key, divisor, places, signed in [
                ('mean_pipeline_ms', 1000, 2, False),
                ('throughput_requests_s', 1, 3, False),
                ('mean_online_non_llm_ms', 1, 2, False),
                ('pipeline_delta_vs_vanilla_percent', 1, 2, True),
                ('mean_output_tokens_per_call', 1, 2, False)]:
                raw = [float(row[key]) for row in cohort]
                mean, sd = statistics.mean(raw), statistics.stdev(raw)
                assert math.isclose(mean, float(reference[key + '_mean']), abs_tol=1e-8)
                assert math.isclose(sd, float(reference[key + '_sample_sd']), abs_tol=1e-8)
                mean, sd = mean / divisor, sd / divisor
                fmt = ('+' if signed and mean != 0 else '') + '.' + str(places) + 'f'
                cells.append(format(mean, fmt) + r' $\pm$ ' + format(sd, '.' + str(places) + 'f'))
                checked += 1
            if system == 'TokenProv-Full':
                label = r'\textbf{\textsc{TokenProv}}'
            table.append(label + ' & ' + ' & '.join(cells) + r' \\')
    table += [r'\bottomrule', r'\end{tabular*}', r'\end{table*}', '']
    b4_table = '\n'.join(table)
    (OUT / 'b4_appendix_cross_model_table.tex').write_text(b4_table, encoding='utf-8')
    b4_section = r'''\section{Additional Cross-Model Measurements}
\label{app:b4-cross-model}
Table~\ref{tab:b4-cross-model} summarizes five models and four schemes at
concurrency eight. Each model--scheme pair has three rounds of 128 measured
requests, giving 384 requests per pair and 7,680 requests with 61,440 model calls
overall; no measured requests failed. Warmups are excluded.
Each metric is first computed within a round. Entries are arithmetic means and
sample standard deviations of the three round-level observations, rather than
pooled per-request deviations or confidence intervals.
The online non-LLM column uses the same definition as above.

For model $m$, scheme $s$, and round $r$, the latency change is
\[
\delta_{m,s,r}=100\left(
\frac{\overline{T}_{m,s,r}}{\overline{T}_{m,\mathrm{Vanilla},r}}-1\right),
\]
where $\overline{T}$ is mean complete request processing time.
The table aggregates these three within-round changes; positive values indicate
longer processing time. The ranges in the main figure span the minimum and maximum
of the three observations. Online non-LLM time is an internal component, rather
than incremental latency relative to Vanilla.

Across the five models, \textsc{TokenProv}'s mean online non-LLM time is
53.98--65.98\,ms/request. Request-latency changes vary across rounds, as do shared
GPU load, placement, and actual generated lengths. All rounds are retained,
including the DeepSeek third-round latency change of $+42.71\%$.
\input{b4_appendix_cross_model_table.tex}
'''
    (OUT / 'b4_appendix_section.tex').write_text(b4_section, encoding='utf-8')
    # One self-contained fragment avoids path dependencies when copied into a paper.
    standalone = b3_section.replace(r'\input{b3_appendix_protocol_table.tex}', protocol_tex).replace(
        r'\input{b3_appendix_concurrency_table.tex}', (OUT / 'b3_appendix_concurrency_table.tex').read_text(encoding='utf-8'))
    standalone += '\n' + b4_section.replace(r'\input{b4_appendix_cross_model_table.tex}', b4_table)
    (OUT / 'b3_b4_appendix.tex').write_text(
        '% Insert after the existing \\appendices. Requires booktabs and xcolor[table].\n' + standalone, encoding='utf-8')
    preview = r'''\documentclass[conference]{IEEEtran}
\usepackage{booktabs}
\usepackage[table]{xcolor}
\usepackage{mathptmx}
\begin{document}
\onecolumn
\appendices
\input{b3_appendix_section.tex}
\clearpage
\input{b4_appendix_section.tex}
\end{document}
'''
    (OUT / 'b3_b4_appendix_preview.tex').write_text(preview, encoding='utf-8')
    (OUT / 'appendix_verification.json').write_text(json.dumps({
        'protocol_configurations': len(protocol), 'protocol_table_rows': 5,
        'concurrency_table_rows': 12, 'cross_model_table_rows': 20,
        'cross_model_observations': len(observations), 'mean_sd_cells_checked': checked,
        'cross_model_statistical_unit': 'three round-level observations',
        'latency_change_definition': 'within-round ratio to Vanilla, then mean and sample SD',
        'all_model_scheme_groups_retained': True, 'no_new_experiments': True}, indent=2) + '\n', encoding='utf-8')


def plot(rows, cross_model):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.colors import to_rgba_array
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.patches import Rectangle
    from matplotlib.patches import ConnectionPatch
    from matplotlib.ticker import NullLocator, ScalarFormatter
    import numpy as np

    fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('times.ttf', 'timesbd.ttf', 'timesi.ttf', 'timesbi.ttf'):
        if (fonts / name).is_file():
            font_manager.fontManager.addfont(str(fonts / name))
    font = font_manager.findfont('Times New Roman', fallback_to_default=False)
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.serif': ['Times New Roman'],
        'font.size': 7.3, 'axes.titlesize': 7.5, 'axes.labelsize': 7.5,
        'xtick.labelsize': 7.0, 'ytick.labelsize': 6.9, 'axes.linewidth': .6,
        'axes.edgecolor': '#3C464E', 'text.color': '#282A30',
        'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none'})
    # Two side-by-side groups, each with two flat plots sharing one x axis.
    figure_size = (3.5, 2.28)
    png_dpi = 1200
    fig, axes = plt.subplots(2, 2, figsize=figure_size, sharex='col')
    fig.subplots_adjust(left=.12, right=.985, bottom=.205, top=.975, wspace=.27, hspace=.10)
    protocol_colors = ['#527F98', '#B76252']
    protocol_fills = ['#B9D6E9', '#E2ABA2']
    round_fills = ['#E2ABA2', '#FDD6AF', '#B9D6E9']
    round_edges = ['#A65348', '#A66B32', '#527F98']
    compact_models = ['Q3\n1.7B', 'Q3\n4B', 'Phi-3\n3.8B', 'DS\n8B', 'Q3\n14B']
    protocol_bands = []
    zoom_axes, inset_checks = [], []
    for ax, key, label in [
        (axes[0, 0], 'construction_ms', 'Build'),
        (axes[1, 0], 'audit_ms', 'Audit')]:
        boundaries = [[next(row[key] for row in rows if row['calls_per_request'] == calls
                            and row['events_per_call'] == factor) for calls in CALLS] for factor in FACTORS]
        lower = [min(a, b) for a, b in zip(*boundaries)]
        upper = [max(a, b) for a, b in zip(*boundaries)]
        # These envelopes span configurations, not repeated measurements.
        ax.fill_between(CALLS, lower, upper, color='#B9D6E9', alpha=.48, linewidth=0, zorder=1)
        protocol_bands.append({'metric': key, 'meaning': 'range across 4 and 8 events/call',
                               'lower_ms': lower, 'upper_ms': upper})
        for factor, line, color, fill, marker in zip(FACTORS, ('-', '--'), protocol_colors, protocol_fills, ('o', 's')):
            cohort = [row for row in rows if row['events_per_call'] == factor]
            ax.plot([row['calls_per_request'] for row in cohort], [row[key] for row in cohort],
                color=color, linestyle=line, marker=marker, markersize=3.1,
                markerfacecolor=fill, markeredgewidth=.75, linewidth=1.0,
                label='{} events'.format(factor), zorder=4)
        ax.set_xscale('log', base=2)
        ax.set_yscale('log', base=10)
        ax.set_xlim(.8, 670)
        ax.set_ylim(1, 1800)
        ax.set_xticks(CALLS)
        ax.xaxis.set_major_formatter(ScalarFormatter())
        ax.xaxis.set_minor_locator(NullLocator())
        ax.set_yticks([1, 10, 100, 1000])
        ax.yaxis.set_major_formatter(ScalarFormatter())
        ax.yaxis.set_minor_locator(NullLocator())
        ax.set_ylabel(label + ' (ms)', labelpad=2)
        if key == 'construction_ms':
            detail_calls, xlim, ylim, yticks = [128, 512], (110, 550), (130, 820), [200, 800]
            detail_label, source_corner, inset_corner = '128\u2013512 calls', (550, 130), (1, 1)
        else:
            detail_calls, xlim, ylim, yticks = [1, 8], (.85, 8.7), (1.2, 15), [2, 14]
            detail_label, source_corner, inset_corner = '1\u20138 calls', (8.7, 1.2), (0, 0)
        # Insets only redraw the requested measured endpoints on linear axes.
        # Their limits exactly match the source rectangles in the log-scale main axes.
        # Lift the insets above the parent baseline; reserve right-side space
        # for their y tick labels within the parent frame.
        zoom = ax.inset_axes([.62, .20, .24, .33], zorder=8)
        lower_detail = [next(row[key] for row in rows if row['calls_per_request'] == calls
            and row['events_per_call'] == 4) for calls in detail_calls]
        upper_detail = [next(row[key] for row in rows if row['calls_per_request'] == calls
            and row['events_per_call'] == 8) for calls in detail_calls]
        zoom.fill_between(detail_calls, [min(a, b) for a, b in zip(lower_detail, upper_detail)],
            [max(a, b) for a, b in zip(lower_detail, upper_detail)], color='#B9D6E9', alpha=.48, linewidth=0)
        detail_values = []
        for factor, line, color, fill, marker in zip(FACTORS, ('-', '--'), protocol_colors, protocol_fills, ('o', 's')):
            cohort = [row for row in rows if row['events_per_call'] == factor
                      and row['calls_per_request'] in detail_calls]
            values = [row[key] for row in cohort]
            zoom.plot([row['calls_per_request'] for row in cohort], values,
                color=color, linestyle=line, marker=marker, markersize=2.2,
                markerfacecolor=fill, markeredgewidth=.6, linewidth=.75, zorder=3)
            detail_values.extend(values)
        zoom.set_xlim(xlim)
        zoom.set_ylim(ylim)
        zoom.set_xticks(detail_calls)
        zoom.set_yticks(yticks)
        # Right-side inset ticks keep the left labels clear of the main 32-call data.
        zoom.yaxis.tick_right()
        zoom.tick_params(labelsize=6.0, length=1.5, width=.5, pad=.8)
        zoom.grid(color='#E1E5E8', linewidth=.3)
        zoom.set_axisbelow(True)
        for spine in zoom.spines.values():
            spine.set_linewidth(.5)
            spine.set_color('#555555')
        ax.add_patch(Rectangle((xlim[0], ylim[0]), xlim[1] - xlim[0], ylim[1] - ylim[0],
            fill=False, edgecolor='#80868D', linewidth=.5, linestyle=(0, (3, 2)), zorder=5))
        ax.add_artist(ConnectionPatch(xyA=source_corner, coordsA=ax.transData,
            xyB=inset_corner, coordsB=zoom.transAxes, color='#8B9198', linewidth=.45, zorder=6))
        zoom_axes.append(zoom)
        inset_checks.append({'metric': key, 'calls': detail_calls, 'x_limits': list(xlim),
            'y_limits_ms': list(ylim), 'x_scale': 'linear', 'y_scale': 'linear',
            'raw_values_ms': detail_values, 'measured_points': 4})
    axes[1, 0].set_xlabel('Calls per request', labelpad=2)
    axes[0, 0].legend(loc='upper left', ncol=2, fontsize=6.3,
        handlelength=1.3, columnspacing=.5, handletextpad=.3, borderpad=.25,
        facecolor='white', edgecolor='#BBC0C7', framealpha=.98, fancybox=False)
    round_markers = ['o', 's', '^']
    round_handles = [Line2D([], [], marker=marker, linestyle='none', markersize=3,
        markerfacecolor=fill, markeredgecolor=edge, label='Round ' + str(rnd))
        for rnd, marker, fill, edge in zip((1, 2, 3), round_markers, round_fills, round_edges)]
    mean_handle = Line2D([], [], color='#2E3B97', linewidth=1.2, label='Mean')
    range_handle = Patch(facecolor='#ADAACF', alpha=.40, edgecolor='#666095', label='Range')
    model_ranges = []
    for ax, key, ylabel in zip(axes[:, 1],
        ('online_non_llm_ms', 'latency_change_percent'),
        ('Non-LLM (ms)', 'Latency \u0394 (%)')):
        for index, (model, _) in enumerate(MODELS):
            cohort = sorted([row for row in cross_model if row['model'] == model], key=lambda row: row['round'])
            values = [row[key] for row in cohort]
            lower, upper, mean = min(values), max(values), statistics.mean(values)
            ax.add_patch(Rectangle((index - .30, lower), .60, upper - lower,
                facecolor='#ADAACF', edgecolor='#666095', alpha=.40, linewidth=.5, zorder=2))
            ax.hlines([lower, upper], index - .30, index + .30,
                color='#666095', linewidth=.5, alpha=.8, zorder=2)
            for rnd, offset, marker, value, fill, edge in zip((1, 2, 3), (-.16, 0, .16), round_markers, values, round_fills, round_edges):
                ax.scatter(index + offset, value, s=12, marker=marker,
                    facecolor=fill, edgecolor=edge, linewidth=.65, zorder=5)
            ax.hlines(mean, index - .24, index + .24, color='#2E3B97', linewidth=1.2, zorder=4)
            model_ranges.append({'model': model, 'metric': key, 'round_values': values,
                                 'min': lower, 'max': upper, 'mean': mean})
        ax.set_xlim(-.58, 4.58)
        ax.set_xticks(range(5), compact_models)
        ax.tick_params(axis='x', labelsize=6.8)
        ax.set_ylabel(ylabel, labelpad=1.5)
    axes[0, 1].set_ylim(40, 100)
    axes[0, 1].set_yticks([40, 60, 80, 100])
    axes[1, 1].set_ylim(-30, 50)
    axes[1, 1].set_yticks([-20, 0, 20, 40])
    axes[1, 1].axhline(0, color='#69717B', linestyle='--', linewidth=.6, zorder=1)
    axes[0, 1].legend(handles=[round_handles[0], mean_handle, round_handles[1], range_handle, round_handles[2]],
        loc='upper left', ncol=3, fontsize=6.2, columnspacing=.3, handlelength=.85,
        handletextpad=.3, labelspacing=.22, borderpad=.25,
        borderaxespad=.2,
        facecolor='white', edgecolor='#BBC0C7', framealpha=.98, fancybox=False)
    for ax in axes.flat:
        ax.grid(which='major', axis='both' if ax in axes[:, 0] else 'y', color='#E1E5E8', linewidth=.35)
        ax.set_axisbelow(True)
        ax.tick_params(length=2.2, width=.6, pad=1.8)
        for spine in ax.spines.values():
            spine.set_linewidth(.6)
            spine.set_color('#3C464E')
        if ax.get_legend() is not None:
            ax.get_legend().get_frame().set_linewidth(.4)
    for ax in axes[0, :]:
        ax.tick_params(axis='x', labelbottom=False)
    group_labels = []
    for column, label in [(0, '(a) Protocol scaling'), (1, '(b) Cross-model variation')]:
        bounds = axes[1, column].get_position()
        group_labels.append(fig.text((bounds.x0 + bounds.x1) / 2, .045, label, fontsize=7.4,
                 ha='center', va='center'))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    outside = []
    adjacent_tick_overlaps = []
    for ax in [axes[1, 1]]:
        labels = ax.get_xticklabels()
        for left, right in zip(labels, labels[1:]):
            if left.get_window_extent(renderer).overlaps(right.get_window_extent(renderer)):
                adjacent_tick_overlaps.append([left.get_text(), right.get_text()])
    assert not adjacent_tick_overlaps, adjacent_tick_overlaps
    subtitle_clearances_pt = []
    for column, item in enumerate(group_labels):
        ax = axes[1, column]
        label_boxes = [text.get_window_extent(renderer)
            for text in [ax.xaxis.label, *ax.get_xticklabels()]
            if text.get_visible() and text.get_text()]
        clearance = min(box.y0 for box in label_boxes) - item.get_window_extent(renderer).y1
        assert clearance >= 1.5 * fig.dpi / 72, 'Group subtitle touches axis labels'
        subtitle_clearances_pt.append(clearance * 72 / fig.dpi)
    column_label_clearances_pt = []
    for left_ax, right_ax in axes:
        clearance = right_ax.yaxis.label.get_window_extent(renderer).x0 - left_ax.get_window_extent(renderer).x1
        assert clearance >= 1.5 * fig.dpi / 72, 'Right y label touches left panel'
        column_label_clearances_pt.append(clearance * 72 / fig.dpi)
    occluded_points = []
    for ax, key in zip(axes[:, 0], ('construction_ms', 'audit_ms')):
        legend = ax.get_legend()
        if legend is None:
            continue
        protocol_legend = legend.get_window_extent(renderer)
        for row in rows:
            point = ax.transData.transform((row['calls_per_request'], row[key]))
            if protocol_legend.contains(*point):
                occluded_points.append([row['calls_per_request'], row['events_per_call'], key])
    assert not occluded_points, occluded_points
    inset_occluded_points = []
    inset_tick_clearances_pt = []
    for ax, zoom, key in zip(axes[:, 0], zoom_axes, ('construction_ms', 'audit_ms')):
        inset_bounds = zoom.get_window_extent(renderer)
        parent_bounds = ax.get_window_extent(renderer)
        tick_boxes = [item.get_window_extent(renderer)
                      for item in [*zoom.get_xticklabels(), *zoom.get_yticklabels()]
                      if item.get_visible() and item.get_text()]
        clearance = min(box.y0 for box in tick_boxes) - parent_bounds.y0
        right_clearance = parent_bounds.x1 - max(box.x1 for box in tick_boxes)
        assert clearance > 2 * fig.dpi / 72, 'Inset tick labels touch parent x axis'
        assert right_clearance > 0, 'Inset tick labels cross parent right frame'
        inset_tick_clearances_pt.append({'metric': key,
            'bottom': clearance * 72 / fig.dpi, 'right': right_clearance * 72 / fig.dpi})
        for row in rows:
            if inset_bounds.contains(*ax.transData.transform((row['calls_per_request'], row[key]))):
                inset_occluded_points.append([row['calls_per_request'], row['events_per_call'], key])
    assert not inset_occluded_points, inset_occluded_points
    for entry in model_ranges:
        ax = axes[0, 1] if entry['metric'] == 'online_non_llm_ms' else axes[1, 1]
        assert ax.get_ylim()[0] <= entry['min'] <= entry['max'] <= ax.get_ylim()[1]
    model_legend = axes[0, 1].get_legend().get_window_extent(renderer)
    model_bounds = axes[0, 1].get_window_extent(renderer)
    assert model_bounds.x0 <= model_legend.x0 < model_legend.x1 <= model_bounds.x1, 'Round legend crosses parent frame'
    for entry in model_ranges:
        if entry['metric'] != 'online_non_llm_ms':
            continue
        index = next(i for i, (model, _) in enumerate(MODELS) if model == entry['model'])
        for offset, value in zip((-.16, 0, .16), entry['round_values']):
            assert not model_legend.contains(*axes[0, 1].transData.transform((index + offset, value)))
    for ax in [*axes.flat, *zoom_axes]:
        items = [ax.title, ax.xaxis.label, ax.yaxis.label,
                 *ax.get_xticklabels(), *ax.get_yticklabels(), *ax.texts]
        for item in items:
            if not item.get_visible() or not item.get_text():
                continue
            box = item.get_window_extent(renderer)
            if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
                outside.append(item.get_text())
    for item in fig.texts:
        box = item.get_window_extent(renderer)
        if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
            outside.append(item.get_text())
    assert not outside, outside
    for ext in ('png', 'pdf', 'svg'):
        target = OUT / ('b3_b4_scalability_variability.' + ext)
        fig.savefig(target, dpi=png_dpi if ext == 'png' else 300)
        (OUT / ('b3_protocol_scalability.' + ext)).write_bytes(target.read_bytes())
    converted = 0
    artists = fig.findobj() + [item.arrow_patch for ax in axes.flat for item in ax.texts
                              if getattr(item, 'arrow_patch', None) is not None]
    for artist in artists:
        for name in ('color', 'facecolor', 'edgecolor', 'markerfacecolor', 'markeredgecolor'):
            getter, setter = getattr(artist, 'get_' + name, None), getattr(artist, 'set_' + name, None)
            if not getter or not setter:
                continue
            try:
                colors = to_rgba_array(getter())
                if not len(colors):
                    continue
                gray = colors[:, 0] * .2126 + colors[:, 1] * .7152 + colors[:, 2] * .0722
                colors[:, :3] = gray[:, None]
                setter(colors[0] if len(colors) == 1 else colors)
                converted += 1
            except (ValueError, TypeError, AttributeError):
                pass
    for ext in ('png', 'pdf'):
        target = OUT / ('b3_b4_scalability_variability_grayscale.' + ext)
        fig.savefig(target, dpi=png_dpi if ext == 'png' else 300)
        (OUT / ('b3_protocol_scalability_grayscale.' + ext)).write_bytes(target.read_bytes())
    gray_image = plt.imread(OUT / 'b3_b4_scalability_variability_grayscale.png')
    gray_error = float(np.ptp(gray_image[:, :, :3], axis=2).max())
    assert gray_error == 0, gray_error
    plt.close(fig)
    return {'font': font, 'figure_size_inches': list(figure_size), 'labels_outside_figure': outside,
        'png_dpi': png_dpi,
        'native_output_width_inches': 3.5, 'frame_width_pt': .6,
        'grayscale_channel_error': gray_error, 'compact_model_labels': compact_models,
        'layout': '1x2 groups; each group has two vertically stacked flat axes',
        'shared_x_axes_per_column': True, 'top_x_tick_labels_hidden': True,
        'column_a_top_bottom': ['construction_ms', 'audit_ms'],
        'column_b_top_bottom': ['online_non_llm_ms', 'latency_change_percent'],
        'group_labels_position': 'centered below each column',
        'subplot_spacing': {'wspace': .27, 'hspace': .10},
        'round_legend_labels': ['Round 1', 'Round 2', 'Round 3'],
        'subtitle_clearances_pt': subtitle_clearances_pt,
        'column_label_clearances_pt': column_label_clearances_pt,
        'endpoint_value_callouts': False, 'insets': inset_checks,
        'main_protocol_markers_covered_by_insets': inset_occluded_points,
        'inset_tick_clearances_pt': inset_tick_clearances_pt,
        'inset_tick_font_pt': 6.0,
        'adjacent_model_tick_overlaps': adjacent_tick_overlaps,
        'protocol_points_covered_by_legend': occluded_points,
        'protocol_x_scale': 'log base 2', 'protocol_y_scale': 'log base 10', 'protocol_x_ticks': CALLS,
        'event_factors': FACTORS, 'all_measured_points_retained': True,
        'measurements_per_configuration': 1, 'error_bars': False,
        'grayscale_artist_property_conversions': converted,
        'protocol_bands': protocol_bands, 'cross_model_bands': model_ranges,
        'cross_model_band_meaning': 'min-max of three observed round means, not SD or CI',
        'cross_model_category_interpolation': False,
        'cross_model_y_limits': {'online_non_llm_ms': [40, 100], 'latency_change_percent': [-30, 50]}}


def main():
    OUT.mkdir(exist_ok=True)
    protocol = protocol_data()
    concurrency = concurrency_data()
    cross_model = cross_model_data()
    appendix(concurrency)
    extended_appendix(protocol)
    verification = plot(protocol, cross_model)
    verification.update({'protocol_configurations': len(protocol),
        'concurrency_scheme_configurations': len(concurrency),
        'concurrency_requests': sum(row['measured_requests'] for row in concurrency),
        'concurrency_model_calls': sum(row['model_calls'] for row in concurrency),
        'cross_model_rounds': len(cross_model), 'cross_model_formal_requests': 7680,
        'archived_concurrency_summary_loaded': True,
        'no_inference_or_proof_execution': True})
    (OUT / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n', encoding='utf-8')
    (OUT / 'README.md').write_text('''# Combined B3/B4 scalability and variability figure

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
''', encoding='utf-8')
    print(json.dumps({'protocol_configurations': len(protocol),
        'concurrency_rows': len(concurrency), 'output': str(OUT)}, ensure_ascii=True))


if __name__ == '__main__':
    main()
