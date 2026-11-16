"""Plot existing D-section observations without executing new experiments."""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys

from paper_paths import DATA, PAPER, output_dir, read_csv
HERE = DATA
ROOT = PAPER
OUT = output_dir('d')


MODELS = [
    ('qwen3-1.7b', 'Qwen3\n1.7B'),
    ('qwen3-4b', 'Qwen3\n4B'),
    ('phi-3-mini-4k-instruct', 'Phi-3\n3.8B'),
    ('deepseek-r1-distill-llama-8b', 'DeepSeek\n8B'),
    ('qwen3-14b', 'Qwen3\n14B'),
]
CALLS = [1, 2, 4, 8, 16, 32, 64]
STAGES = ['T_close_ms', 'T_merkle_ms', 'T_settle_local_ms', 'T_ledger_ms']
METRICS = ['repository_bytes', 'ledger_bytes', 'T_evidence_write_ms',
           'T_audit_ms', *STAGES[:3], 'T_settlement_ms', 'T_ledger_ms']


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def derive():
    legacy_path = DATA / 'd/E1_storage_vs_calls.csv'
    current_path = DATA / 'd/current_round_data.csv'
    storage_raw = read_csv(legacy_path)
    assert len(storage_raw) == 105
    storage = []
    for scheme in ('TokenProv-Full', 'FullTrace'):
        for calls in CALLS:
            rows = [r for r in storage_raw if r['system'] == scheme
                    and int(r['calls_per_request']) == calls]
            assert sorted(int(r['run']) for r in rows) == [1, 2, 3, 4, 5]
            assert all(r['measurement_type'] == 'PROTOCOL_MICROBENCHMARK' for r in rows)
            assert all(math.isclose(float(r['repository_bytes']) + float(r['ledger_bytes']),
                                    float(r['total_bytes'])) for r in rows)
            values = [float(r['total_bytes']) / 1024 for r in rows]
            storage.append({'system': scheme, 'calls_per_request': calls, 'rounds': 5,
                'mean_KiB': statistics.mean(values), 'sample_sd_KiB': statistics.stdev(values),
                'min_KiB': min(values), 'max_KiB': max(values),
                'cohort': 'historical_protocol_microbenchmark'})

    raw = read_csv(current_path)
    assert len(raw) == 60
    official = read_csv(DATA / 'd/current_model_summary.csv')
    current = []
    for model, _ in MODELS:
        rows = sorted([r for r in raw if r['model'] == model
                       and r['system'] == 'TokenProv-Full'], key=lambda r: int(r['round']))
        assert [int(r['round']) for r in rows] == [1, 2, 3]
        assert all(int(r['requests']) == 128 for r in rows)
        summary = next(r for r in official if r['model'] == model
                       and r['system'] == 'TokenProv-Full')
        item = {'model': model, 'rounds': 3, 'requests_per_round': 128, 'requests': 384}
        for key in METRICS:
            values = [float(r[key]) for r in rows]
            item[key + '_mean'] = statistics.mean(values)
            item[key + '_sample_sd'] = statistics.stdev(values)
            for suffix in ('mean', 'sample_sd'):
                assert math.isclose(item[key + '_' + suffix],
                    float(summary[key + '_' + suffix]), rel_tol=1e-10, abs_tol=1e-9)
        for row in rows:
            assert math.isclose(float(row['T_settlement_ms']),
                sum(float(row[key]) for key in STAGES[:3]), abs_tol=1e-8)
        totals = [sum(float(r[key]) for key in STAGES) for r in rows]
        item['settlement_and_ledger_ms_mean'] = statistics.mean(totals)
        item['settlement_and_ledger_ms_sample_sd'] = statistics.stdev(totals)
        for key in ['T_evidence_write_ms', 'T_audit_ms']:
            for row in rows:
                item[key + '_round_' + row['round']] = float(row[key])
            item[key + '_min'] = min(float(r[key]) for r in rows)
            item[key + '_max'] = max(float(r[key]) for r in rows)
        item['settlement_and_ledger_ms_min'] = min(totals)
        item['settlement_and_ledger_ms_max'] = max(totals)
        item['ledger_fraction_of_retained_bytes_percent'] = 100 * item['ledger_bytes_mean'] / (
            item['repository_bytes_mean'] + item['ledger_bytes_mean'])
        item['ledger_fraction_of_settlement_time_percent'] = 100 * item['T_ledger_ms_mean'] / statistics.mean(totals)
        for row, total in zip(rows, totals):
            item['settlement_and_ledger_round_' + row['round'] + '_ms'] = total
        current.append(item)
    write_csv(OUT / 'd_storage_plot_values.csv', storage)
    write_csv(OUT / 'd_current_plot_values.csv', current)
    hashes = {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in [legacy_path, current_path, DATA / 'd/current_model_summary.csv']}
    return storage, current, hashes


def make_latex(storage, current):
    def cells(key, divisor=1):
        return ' & '.join('{:.2f} $\\pm$ {:.2f}'.format(
            row[key + '_mean'] / divisor, row[key + '_sample_sd'] / divisor) for row in current)

    table = [r'\begin{table*}[!t]', r'\centering',
        r'\caption{Current TokenProv evidence and local settlement costs (mean $\pm$ sample SD of three round means; 128 measured requests per round).}',
        r'\label{tab:d-current-evidence-settlement}', r'\footnotesize',
        r'\renewcommand{\arraystretch}{1.10}',
        r'\begin{tabular*}{\textwidth}{@{\hspace{4pt}}l@{\extracolsep{\fill}}ccccc@{\hspace{4pt}}}',
        r'\toprule',
        r'\textbf{Metric} & \shortstack{\textbf{Qwen3}\\\textbf{1.7B}} & \shortstack{\textbf{Qwen3}\\\textbf{4B}} & \shortstack{\textbf{Phi-3-mini}\\\textbf{3.8B}} & \shortstack{\textbf{DeepSeek-R1-Distill}\\\textbf{Llama-8B}} & \shortstack{\textbf{Qwen3}\\\textbf{14B}} \\',
        r'\midrule', r'\rowcolor{gray!12}',
        r'\multicolumn{6}{l}{\textbf{Serialized storage (KiB/request)}} \\',
        'Evidence bundle & ' + cells('repository_bytes', 1024) + r' \\',
        'Local ledger & ' + cells('ledger_bytes', 1024) + r' \\',
        r'\midrule', r'\rowcolor{gray!12}',
        r'\multicolumn{6}{l}{\textbf{Processing time (ms/request)}} \\']
    for label, key in [('Evidence persistence', 'T_evidence_write_ms'),
            ('Protocol audit', 'T_audit_ms'), ('Request closure', 'T_close_ms'),
            ('Merkle construction', 'T_merkle_ms'), ('Local settlement', 'T_settle_local_ms'),
            ('Settlement construction subtotal', 'T_settlement_ms'),
            ('Ledger append', 'T_ledger_ms'),
            ('Settlement construction + ledger', 'settlement_and_ledger_ms')]:
        table.append(label + ' & ' + cells(key) + r' \\')
    table += [r'\bottomrule', r'\end{tabular*}', r'\end{table*}', '']
    (OUT / 'd_appendix_current_table.tex').write_text('\n'.join(table), encoding='utf-8')

    table = [r'\begin{table}[!t]', r'\centering',
        r'\caption{Historical protocol storage microbenchmark (KiB/request; five measurements per configuration). Sizes are identical across repetitions.}',
        r'\label{tab:d-fulltrace-storage}', r'\footnotesize',
        r'\renewcommand{\arraystretch}{1.10}', r'\begin{tabular}{cccc}',
        r'\toprule', r'\textbf{Calls} & \textbf{TokenProv} & \textbf{FullTrace} & \textbf{Reduction} \\', r'\midrule']
    assert all(row['sample_sd_KiB'] == 0 for row in storage)
    for calls in CALLS:
        tp = next(r['mean_KiB'] for r in storage if r['system'] == 'TokenProv-Full'
                  and r['calls_per_request'] == calls)
        ft = next(r['mean_KiB'] for r in storage if r['system'] == 'FullTrace'
                  and r['calls_per_request'] == calls)
        table.append(f'{calls} & {tp:.2f} & {ft:.2f} & {100 * (1 - tp / ft):.2f}\\% ' + r'\\')
    table += [r'\bottomrule', r'\end{tabular}', r'\end{table}', '']
    (OUT / 'd_appendix_storage_table.tex').write_text('\n'.join(table), encoding='utf-8')

    integration = r'''% Copy the figure into the paper's graphics directory and adapt this path.
\begin{figure}[!t]
  \centering
  \includegraphics[width=\columnwidth]{d_evidence_settlement.pdf}
  \caption{Evidence storage and processing costs.
  (a) Historical protocol microbenchmark comparing retained storage with FullTrace
  under the same earlier serialization; five measurements per configuration have
  identical sizes; the shaded gap indicates storage saved and the lower strip
  gives relative savings.
  (b)--(c) Current TokenProv measurements with eight sequential calls per request
  and concurrency eight. Circles, squares and triangles identify the three
  round means (128 measured requests per round), black ticks show their means,
  and shaded intervals span their minimum and maximum. The storage matrix in
  (b) gives three-round mean serialized sizes. Stage cells in (c) give mean
  ms/request; total costs are summed within each round before aggregation.
  The round-marker legend in (c) also applies to (b). In (b), Q1.7/Q4/Q14 denote
  Qwen3-1.7B/4B/14B, Phi denotes Phi-3-mini, and DS8 denotes the DeepSeek
  Llama-8B distillation.}
  \label{fig:d-evidence-settlement}
\end{figure}
'''
    (OUT / 'd_paper_integration.tex').write_text(integration, encoding='utf-8')
    appendix = r'''\section{Additional Evidence and Settlement Measurements}
\label{app:d-evidence-settlement}
Table~\ref{tab:d-current-evidence-settlement} reports current measurements for
\textsc{TokenProv}: three rounds of 128 measured requests per model, with eight
sequential calls per request and concurrency eight. Warmups are excluded.
Each entry is the mean and sample standard deviation of three round means.
Serialized sizes exclude filesystem block allocation and shared deployment setup.
Evidence persistence includes serialization, hashing, writing, flushing and syncing.
Protocol audit checks an in-memory transcript; it does not measure cold repository
retrieval. Settlement construction is the sum of request closure, Merkle
construction and local settlement. The final total additionally includes ledger
append, and is calculated within each round before aggregation.
The local ledger appends three signed records in one batch; these measurements
do not include distributed consensus.
\input{d_appendix_current_table.tex}

Table~\ref{tab:d-fulltrace-storage} gives the historical FullTrace comparison
under an earlier serialization. Both schemes use the same authenticated protocol;
FullTrace additionally retains the available raw messages and responses.
These are protocol microbenchmarks, rather than retained GPU inference states.
Their absolute sizes are not pooled with current evidence-bundle measurements.
\input{d_appendix_storage_table.tex}
'''
    (OUT / 'd_appendix.tex').write_text(appendix, encoding='utf-8')
    preview = r'''\documentclass[conference]{IEEEtran}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage[table]{xcolor}
\usepackage{mathptmx}
\begin{document}
\input{d_paper_integration.tex}
\clearpage
\onecolumn
\appendices
\input{d_appendix.tex}
\end{document}
'''
    (OUT / 'd_preview.tex').write_text(preview, encoding='utf-8')


def plot(storage, current):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.colors import LinearSegmentedColormap, ListedColormap, to_rgba_array
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Rectangle
    from matplotlib.ticker import MultipleLocator, NullLocator, ScalarFormatter
    import numpy as np

    font_dir = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('times.ttf', 'timesbd.ttf', 'timesi.ttf', 'timesbi.ttf'):
        if (font_dir / name).is_file():
            font_manager.fontManager.addfont(str(font_dir / name))
    font = font_manager.findfont('Times New Roman', fallback_to_default=False)
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 13,
        'axes.labelsize': 14, 'xtick.labelsize': 12.5, 'ytick.labelsize': 13,
        'axes.linewidth': .8, 'hatch.linewidth': .65, 'pdf.fonttype': 42,
        'ps.fonttype': 42, 'svg.fonttype': 'none'})
    fig = plt.figure(figsize=(3.5, 3.95))
    grid = fig.add_gridspec(2, 1, left=.14, right=.99, bottom=.115, top=.99,
                            hspace=.34)
    upper = grid[0, 0].subgridspec(1, 2, width_ratios=[1.10, 1.0], wspace=.30)
    axes = []
    for group in range(3):
        parent = upper[0, group] if group < 2 else grid[1, 0]
        inner = parent.subgridspec(2, 1,
            height_ratios=[3.3, 1.0] if group < 2 else [1.4, 1.0], hspace=.08)
        top = fig.add_subplot(inner[0, 0])
        lower = fig.add_subplot(inner[1, 0], sharex=top)
        axes.extend([top, lower])
        top.tick_params(axis='x', labelbottom=False, bottom=False)
    a_top, a_lower, b_top, b_lower, c_top, c_lower = axes
    # The stage row names need a slightly wider left margin than numeric ticks.
    for ax in [c_top, c_lower]:
        position = ax.get_position()
        ax.set_position([position.x0 + .03, position.y0, position.width - .03, position.height])
    blue, orange, gray = '#155C91', '#A34D00', '#777777'
    for ax in axes:
        ax.set_axisbelow(True)
        ax.grid(axis='y', color='#D8D8D8', linewidth=.55)
        ax.tick_params(length=3, width=.7, pad=3)
    legend_kw = dict(framealpha=.97, facecolor='white', edgecolor='#C0C0C0',
        fontsize=11.4, handlelength=1.1, handletextpad=.4, borderpad=.32,
        labelspacing=.25, borderaxespad=.35)

    # (a) Curves connect the seven measured configurations; the shaded gap is
    # savings between schemes, not repeated-measurement uncertainty.
    xpos = np.arange(len(CALLS))
    tp = np.array([next(r['mean_KiB'] for r in storage if r['system'] == 'TokenProv-Full'
                       and r['calls_per_request'] == calls) for calls in CALLS])
    ft = np.array([next(r['mean_KiB'] for r in storage if r['system'] == 'FullTrace'
                       and r['calls_per_request'] == calls) for calls in CALLS])
    savings = 100 * (1 - tp / ft)
    a_top.fill_between(xpos, tp, ft, color=blue, alpha=.20, linewidth=0, zorder=2)
    a_top.plot(xpos, ft, '--s', color=gray, lw=1.4, ms=4.6, mfc='white', mec=gray, mew=1.1,
               label='FullTrace', zorder=4)
    a_top.plot(xpos, tp, '-o', color=blue, lw=1.5, ms=4.6, mfc='white', mec=blue, mew=1.1,
               label='TokenProv', zorder=5)
    a_top.set_ylim(0, 510)
    a_top.set_yticks([0, 200, 400])
    a_top.yaxis.set_major_formatter(ScalarFormatter())
    a_top.yaxis.set_minor_locator(NullLocator())
    a_top.set_ylabel('KiB/request', labelpad=2)
    a_top.set_xlim(-.5, 6.6)
    a_top.legend(loc='upper left', ncol=2, columnspacing=.7, **legend_kw)
    annotation = a_top.annotate('29.0% less\n118.7 KiB', xy=(5.76, (tp[-1] + ft[-1]) / 2),
        xytext=(.1, 290), fontsize=10.7, color=blue, va='center',
        bbox=dict(boxstyle='round,pad=.20', fc='white', ec=blue, lw=.7),
        arrowprops=dict(arrowstyle='->', lw=.9, color=blue), zorder=6)

    savings_cmap = LinearSegmentedColormap.from_list('savings', ['#F3F6F8', '#9BBAD3'])
    a_lower.grid(False)
    a_lower.imshow([savings], extent=(-.5, 6.5, 0, 1), aspect='auto',
        cmap=savings_cmap, vmin=0, vmax=35, interpolation='nearest')
    a_lower.set_ylim(0, 1)
    a_lower.set_yticks([.5], ['Saved\n(%)'])
    a_lower.tick_params(axis='y', length=0, labelsize=10.5)
    a_lower.set_xticks(xpos, CALLS)
    a_lower.set_xlabel('Calls/request', labelpad=2)
    for pos, value in zip(xpos, savings):
        a_lower.text(pos, .5, f'{value:.1f}', ha='center', va='center', fontsize=10.3)
        a_lower.axvline(pos + .5, color='white', lw=1)

    # (b) Two colored intervals per model show exactly the three observed rounds.
    x = np.arange(len(MODELS))
    markers = ['o', 's', '^']
    for offset, key, color in [(-.20, 'T_evidence_write_ms', blue),
                               (.20, 'T_audit_ms', orange)]:
        for index, row in enumerate(current):
            center = index + offset
            values = [row[key + '_round_' + str(rnd)] for rnd in (1, 2, 3)]
            b_top.add_patch(Rectangle((center - .105, min(values)), .21,
                max(values) - min(values), facecolor=color, alpha=.18,
                edgecolor=color, linewidth=.9, hatch='///' if offset > 0 else None,
                zorder=2))
            b_top.vlines(center, min(values), max(values), color=color, lw=.9, zorder=3)
            for jitter, marker, value in zip([-.045, 0, .045], markers, values):
                b_top.plot(center + jitter, value, marker=marker, ms=5,
                    mec=color, mfc='white', mew=1.0, linestyle='none', zorder=4)
            b_top.hlines(row[key + '_mean'], center - .12, center + .12,
                         color='#111111', lw=1.7, zorder=5)
    handles = [Patch(facecolor=blue, alpha=.3, edgecolor=blue, label='Persist'),
               Patch(facecolor=orange, alpha=.3, edgecolor=orange, hatch='///', label='Audit'),
               Line2D([], [], color='#111111', lw=1.7, label='Mean')]
    handles += [Line2D([], [], marker=m, mfc='white', mec='#333333', linestyle='none',
                      label='R' + str(rnd), ms=5) for rnd, m in enumerate(markers, 1)]
    b_top.legend(handles=handles, loc='upper left', ncol=3, columnspacing=.9, **legend_kw)
    b_top.set_ylim(10, 69)
    b_top.yaxis.set_major_locator(MultipleLocator(15))
    b_top.set_ylabel('Time (ms)', labelpad=3)

    # Nearly invariant sizes use equal-light cells, avoiding a min--max color
    # scale that would exaggerate the 1% cross-model difference.
    b_lower.grid(False)
    b_lower.set_ylim(0, 2)
    for index, row in enumerate(current):
        b_lower.add_patch(Rectangle((index - .5, 1), 1, 1,
            fc='#E7EEF4', ec='white', lw=1))
        b_lower.text(index, 1.5, f"{row['repository_bytes_mean'] / 1024:.1f}",
            ha='center', va='center', fontsize=10.3)
    b_lower.add_patch(Rectangle((-.5, 0), 5, 1, fc='#F4F4F4', ec='white', lw=1))
    b_lower.text(2, .5, 'Ledger: 1.318 KiB/request', ha='center', va='center', fontsize=10.0)
    b_lower.set_yticks([1.5], ['Bundle\n(KiB)'])
    b_lower.tick_params(axis='y', length=0, labelsize=10.5)

    # (c) An annotated, common-scale matrix makes all twenty stage means visible.
    matrix = np.array([[r[key + '_mean'] for r in current] for key in STAGES])
    stages_cmap = LinearSegmentedColormap.from_list('stages', ['#F1F5F8', '#155C91'])
    c_top.grid(False)
    c_top.imshow(matrix, origin='upper', aspect='auto', extent=(-.5, 4.5, 3.5, -.5),
        cmap=stages_cmap, vmin=0, vmax=9, interpolation='nearest')
    c_top.set_yticks(range(4), ['Closure', 'Merkle', 'Settle', 'Ledger'])
    c_top.tick_params(axis='y', length=0, pad=4, labelsize=12.5)
    for index in range(5):
        c_top.axvline(index + .5, color='white', lw=1)
        for stage in range(4):
            c_top.text(index, stage, f'{matrix[stage, index]:.2f}', ha='center', va='center',
                fontsize=13, color='white' if matrix[stage, index] > 4.8 else '#111111')
    for stage in range(3):
        c_top.axhline(stage + .5, color='white', lw=1)
    c_top.set_ylabel('Stage (ms)', labelpad=3)

    for index, row in enumerate(current):
        values = [row['settlement_and_ledger_round_' + str(rnd) + '_ms'] for rnd in (1, 2, 3)]
        c_lower.add_patch(Rectangle((index - .19, min(values)), .38, max(values) - min(values),
            fc=blue, alpha=.18, ec=blue, linewidth=.9, zorder=2))
        for jitter, marker, value in zip([-.12, 0, .12], markers, values):
            c_lower.plot(index + jitter, value, marker=marker, mfc='white', mec='#333333',
                         mew=.95, ms=5, linestyle='none', zorder=4)
        c_lower.hlines(row['settlement_and_ledger_ms_mean'], index - .22, index + .22,
                       color='#111111', linewidth=1.7, zorder=5)
        c_lower.text(index, max(values) + .48, f"{row['settlement_and_ledger_ms_mean']:.2f}",
            fontsize=12, ha='center', va='bottom', color=blue)
    c_lower.set_ylim(10.5, 19.8)
    c_lower.set_yticks([12, 14, 16])
    c_lower.set_ylabel('Total (ms)', labelpad=4)
    for ax in [b_top, b_lower, c_top, c_lower]:
        ax.set_xlim(-.5, 4.5)
    b_lower.set_xticks(x, ['Q1.7', 'Q4', 'Phi', 'DS8', 'Q14'])
    b_lower.tick_params(axis='x', length=0, pad=3, labelsize=10.5)
    c_lower.set_xticks(x, [label for _, label in MODELS])
    c_lower.tick_params(axis='x', length=0, pad=4, labelsize=12.5)
    a_lower.tick_params(axis='x', labelsize=10.7)

    # All point-sized elements are explicitly sized for a real 3.5-inch column,
    # rather than shrinking a multi-column graphic at LaTeX insertion time.
    from matplotlib.text import Text
    for artist in fig.findobj():
        if isinstance(artist, Text):
            artist.set_fontsize(artist.get_fontsize() * .615)
        if isinstance(artist, Line2D):
            artist.set_markersize(artist.get_markersize() * .66)
            artist.set_markeredgewidth(artist.get_markeredgewidth() * .7)
            artist.set_linewidth(artist.get_linewidth() * .75)
    # Rebuild legends at their final size so handle lengths and row spacing,
    # as well as text, fit the narrow axes without covering measurements.
    compact_legend = dict(legend_kw, fontsize=7.0, borderpad=.27,
                          handlelength=1.0, handletextpad=.35, labelspacing=.2)
    a_top.legend(loc='upper left', ncol=2, columnspacing=.35,
                 **dict(compact_legend, fontsize=6.2, handlelength=.9))
    b_top.legend(handles=handles[:2], loc='upper left', ncol=2, columnspacing=.5,
                 **dict(compact_legend, fontsize=6.3, handlelength=.8))
    c_lower.legend(handles=[*handles[3:], handles[2]], loc='upper center',
        ncol=4, columnspacing=.8, **dict(compact_legend, fontsize=6.5))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    # Keep the savings strip's upper edge fixed and shorten it until the
    # Calls/request row aligns with the adjacent model-name row.
    calls_box = a_lower.xaxis.label.get_window_extent(renderer)
    model_boxes = [label.get_window_extent(renderer) for label in b_lower.get_xticklabels()]
    row_delta_px = statistics.mean((box.y0 + box.y1) / 2 for box in model_boxes) - (calls_box.y0 + calls_box.y1) / 2
    row_delta = row_delta_px / fig.bbox.height
    strip_position = a_lower.get_position()
    original_strip_height_pt = strip_position.height * fig.get_figheight() * 72
    assert row_delta > 0
    assert (strip_position.height - row_delta) * fig.get_figheight() * 72 >= 9
    a_lower.set_position([strip_position.x0, strip_position.y0 + row_delta,
        strip_position.width, strip_position.height - row_delta])
    # Raise the full-width group with the upper captions, preserving their gap.
    # Tight export then removes the space released beneath the last caption.
    for ax in [c_top, c_lower]:
        position = ax.get_position()
        ax.set_position([position.x0, position.y0 + row_delta, position.width, position.height])
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    subtitles = []
    lowest_decorations = []
    for ax in [a_lower, b_lower, c_lower]:
        decorations = [label for label in [ax.xaxis.label, *ax.get_xticklabels()]
                       if label.get_visible() and label.get_text()]
        lowest_decorations.append(min(label.get_window_extent(renderer).y0 for label in decorations))
    lowest_decorations[:2] = [min(lowest_decorations[:2])] * 2
    for ax, title, lowest in zip([a_lower, b_lower, c_lower],
            ['(a) Storage efficiency', '(b) Evidence profile', '(c) Settlement profile'], lowest_decorations):
        box = ax.get_position()
        subtitle_y = (lowest - (2 + 8.0 / 2) * fig.dpi / 72) / fig.bbox.height
        title_x = .5 if ax is c_lower else (box.x0 + box.x1) / 2
        subtitles.append(fig.text(title_x, subtitle_y, title,
            ha='center', va='center', fontsize=8.0))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    # Center (c) on the exported figure, including its left-side row labels.
    export_bounds = fig.get_tightbbox(renderer)
    subtitles[-1].set_x((export_bounds.x0 + export_bounds.x1) / (2 * fig.get_figwidth()))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    artists = [*subtitles, annotation]
    for ax in axes:
        artists += [ax.xaxis.label, ax.yaxis.label, *ax.texts,
                    *ax.get_xticklabels(), *ax.get_yticklabels()]
        if ax.get_legend() is not None:
            artists.append(ax.get_legend())
    outside = []
    for artist in artists:
        if not artist.get_visible():
            continue
        # Matplotlib creates tick labels outside explicit view limits; do not
        # treat those invisible, clipped ticks as actual figure content.
        if artist in [label for ax in axes for label in ax.get_yticklabels()]:
            y = artist.get_position()[1]
            owner = next(ax for ax in axes if artist in ax.get_yticklabels())
            if not min(owner.get_ylim()) <= y <= max(owner.get_ylim()):
                continue
        box = artist.get_window_extent(renderer)
        if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
            outside.append(str(getattr(artist, 'get_text', lambda: type(artist).__name__)()))
    assert not outside, outside
    upper_caption_gap = min(title.get_window_extent(renderer).y0 for title in subtitles[:2]) - c_top.get_window_extent(renderer).y1
    assert upper_caption_gap > 2 * fig.dpi / 72, 'Upper captions collide with lower group'
    assert b_top.yaxis.label.get_window_extent(renderer).x0 > a_top.get_window_extent(renderer).x1, 'Upper axes label collides with adjacent panel'
    legend_box = b_top.get_legend().get_window_extent(renderer)
    for index, row in enumerate(current):
        for offset, key in [(-.20, 'T_evidence_write_ms'), (.20, 'T_audit_ms')]:
            for rnd, jitter in zip((1, 2, 3), [-.045, 0, .045]):
                assert not legend_box.contains(*b_top.transData.transform(
                    (index + offset + jitter, row[key + '_round_' + str(rnd)]))), 'Legend hides a measured point'
    for title, ax in zip(subtitles, [a_lower, b_lower, c_lower]):
        if ax.xaxis.label.get_text():
            assert not title.get_window_extent(renderer).overlaps(ax.xaxis.label.get_window_extent(renderer))
        assert all(not title.get_window_extent(renderer).overlaps(label.get_window_extent(renderer))
                   for label in ax.get_xticklabels())
    for ax in [b_lower, c_lower]:
        tick_boxes = [label.get_window_extent(renderer) for label in ax.get_xticklabels()]
        assert all(a.x1 + 2 < b.x0 for a, b in zip(tick_boxes, tick_boxes[1:])), 'Model labels collide'
    calls_box = a_lower.xaxis.label.get_window_extent(renderer)
    model_boxes = [label.get_window_extent(renderer) for label in b_lower.get_xticklabels()]
    row_alignment_error_pt = abs((calls_box.y0 + calls_box.y1) / 2 - statistics.mean(
        (box.y0 + box.y1) / 2 for box in model_boxes)) * 72 / fig.dpi
    assert row_alignment_error_pt < .1
    assert abs(subtitles[0].get_position()[1] - subtitles[1].get_position()[1]) < 1e-10
    export_bounds = fig.get_tightbbox(renderer)
    c_caption_center_error_pt = abs(subtitles[-1].get_position()[0] * fig.get_figwidth()
        - (export_bounds.x0 + export_bounds.x1) / 2) * 72
    assert c_caption_center_error_pt < .1
    for ext in ('pdf', 'png', 'svg'):
        fig.savefig(OUT / ('d_evidence_settlement.' + ext), dpi=1200 if ext == 'png' else 300,
                    bbox_inches='tight', pad_inches=.035)
    for ax in axes:
        for image in ax.images:
            colors = image.get_cmap()(np.linspace(0, 1, 256))
            colors[:, :3] = (colors[:, :3] @ np.array([.2126, .7152, .0722]))[:, None]
            image.set_cmap(ListedColormap(colors))
    for artist in [*fig.findobj(), annotation.arrow_patch, annotation.get_bbox_patch()]:
        for name in ('color', 'facecolor', 'edgecolor', 'markerfacecolor', 'markeredgecolor'):
            getter, setter = getattr(artist, 'get_' + name, None), getattr(artist, 'set_' + name, None)
            if getter is None or setter is None:
                continue
            try:
                rgba = to_rgba_array(getter())
                if len(rgba):
                    luminance = rgba[:, :3] @ np.array([.2126, .7152, .0722])
                    rgba[:, :3] = luminance[:, None]
                    setter(rgba[0] if len(rgba) == 1 else rgba)
            except (TypeError, ValueError, AttributeError):
                pass
    for ext in ('pdf', 'png'):
        fig.savefig(OUT / ('d_evidence_settlement_grayscale.' + ext), dpi=1200 if ext == 'png' else 300,
                    bbox_inches='tight', pad_inches=.035)
    from PIL import Image, ImageChops
    with Image.open(OUT / 'd_evidence_settlement.png') as image:
        png_size = list(image.size)
        assert all(abs(value - 1200) < .1 for value in image.info['dpi'])
    with Image.open(OUT / 'd_evidence_settlement_grayscale.png') as image:
        red, green, blue_channel = image.convert('RGB').split()
        assert ImageChops.difference(red, green).getbbox() is None
        assert ImageChops.difference(green, blue_channel).getbbox() is None
    plt.close(fig)
    return {'font': font, 'figure_size_inches': [3.5, 3.95],
        'png_dpi': 1200, 'png_size_pixels': png_size,
        'saved_strip_height_pt_before': original_strip_height_pt,
        'saved_strip_height_pt_after': a_lower.get_position().height * fig.get_figheight() * 72,
        'calls_and_model_row_alignment_error_pt': row_alignment_error_pt,
        'upper_subtitle_y_aligned': True, 'c_subtitle_export_center_error_pt': c_caption_center_error_pt,
        'grayscale_channels_equal': True,
        'upper_caption_to_lower_group_gap_pt': upper_caption_gap / fig.dpi * 72,
        'layout': 'single column: a and b side by side above full-width c; each group has two tiers',
        'nominal_data_font_pt': 13 * .615,
        'compact_upper_matrix_font_pt': 10.3 * .615,
        'labels_outside_figure': outside, 'fulltrace_reduction_at_64_calls_percent': float(savings[-1]),
        'storage_repetition_sd_KiB_max': max(r['sample_sd_KiB'] for r in storage),
        'current_intervals': 'min to max of three observed round means; markers identify rounds',
        'settlement_intervals': 'range of per-round sums of four non-overlapping stages',
        'heatmap_stage_means_ms': matrix.tolist(),
        'relative_storage_savings_percent': savings.tolist(),
        'ledger_fraction_of_retained_bytes_percent': [r['ledger_fraction_of_retained_bytes_percent'] for r in current],
        'current_TokenProv_requests': sum(r['requests'] for r in current),
        'historical_and_current_sizes_pooled': False, 'new_experiments_executed': False}


def main():
    OUT.mkdir(exist_ok=True)
    storage, current, hashes = derive()
    make_latex(storage, current)
    result = plot(storage, current)
    result['input_sha256'] = hashes
    (OUT / 'd_figure_verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    (OUT / 'FIGURE_README.md').write_text('''# D figure and appendix

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
''', encoding='utf-8')
    print(json.dumps({'output': str(OUT), **result}, ensure_ascii=True))


if __name__ == '__main__':
    main()
