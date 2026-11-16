"""Derive B2 costs from preserved requests; do not invoke inference or proofs."""
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
OUT = output_dir('b2')


MODELS = [
    ('qwen3-1.7b', 'Qwen3-1.7B'),
    ('qwen3-4b', 'Qwen3-4B'),
    ('phi-3-mini-4k-instruct', 'Phi-3-mini (3.8B)'),
    ('deepseek-r1-distill-llama-8b', 'DeepSeek-R1-Distill-Llama (8B)'),
    ('qwen3-14b', 'Qwen3-14B'),
]
SYSTEMS = ['Vanilla', 'SignedLog', 'SignedLogPlus', 'TokenProv-Full']
LABELS = ['Vanilla', 'SignedLog', 'SignedLog+', 'TokenProv']
ONLINE_KEYS = ['T_auth', 'T_lifecycle', 'T_HB_handoff', 'T_BP_handoff',
               'T_provider_extra', 'T_receipt']
REMAINING_KEYS = ['T_close', 'T_merkle', 'T_settle_local', 'T_evidence_write',
                  'T_ledger', 'T_audit']
DERIVED_KEYS = ['online_other', 'online_non_llm', 'remaining_other',
                'remaining_processing', 'pipeline_non_llm']
ALL_KEYS = ONLINE_KEYS + REMAINING_KEYS + DERIVED_KEYS


def long_path(path):
    return Path('\\\\?\\' + str(path.resolve())) if sys.platform == 'win32' else path


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def find_run(*args):
    raise RuntimeError('Public reproduction uses bundled measurement extracts, not private run archives.')



def request_cost(row):
    timings = row['timings_ns']
    values = {key: timings.get(key, 0) / 1e6 for key in ONLINE_KEYS + REMAINING_KEYS}
    assert math.isclose(timings.get('T_settlement', 0) / 1e6,
                        sum(values[key] for key in REMAINING_KEYS[:3]),
                        rel_tol=1e-10, abs_tol=1e-8)
    values['online_non_llm'] = (timings['T_online'] - timings['T_llm']) / 1e6
    values['remaining_processing'] = (row['request_pipeline_ns'] - timings['T_online']) / 1e6
    values['pipeline_non_llm'] = (row['request_pipeline_ns'] - timings['T_llm']) / 1e6
    values['online_other'] = values['online_non_llm'] - sum(values[key] for key in ONLINE_KEYS)
    values['remaining_other'] = values['remaining_processing'] - sum(values[key] for key in REMAINING_KEYS)
    assert all(value >= -1e-6 for value in values.values()), values
    assert math.isclose(values['online_non_llm'] + values['remaining_processing'],
                        values['pipeline_non_llm'], abs_tol=1e-7)
    return values


def derive():
    with (HERE / 'five_model_three_round_metrics.csv').open(encoding='utf-8-sig', newline='') as stream:
        metrics = list(csv.DictReader(stream))
    assert len(metrics) == 60
    raw = [json.loads(line) for line in (DATA / 'b2/request_measurements.jsonl').read_text().splitlines()]
    per_round, identities = [], set()
    max_closure_error = 0
    for metric in metrics:
        selected = [r for r in raw if r['model'] == metric['model'] and r['system'] == metric['system']
                    and r['round'] == int(metric['round'])]
        assert len(selected) == 128 and all(r['success'] and r['actual_calls']==8 for r in selected)
        for row in selected:
            identity = row['model'],row['system'],row['round'],row['request_id']
            assert identity not in identities
            identities.add(identity)
        costs = [request_cost(row) for row in selected]
        means = {key:statistics.mean(cost[key] for cost in costs) for key in ALL_KEYS}
        assert math.isclose(means['online_non_llm'],float(metric['mean_online_non_llm_ms']),rel_tol=1e-10)
        max_closure_error = max(max_closure_error,abs(means['pipeline_non_llm']-means['online_non_llm']-means['remaining_processing']))
        per_round.append({'model':metric['model'],'system':metric['system'],'round':int(metric['round']),
            'requests':128,'source':metric['source'],'run_id':metric['run_id'],
            **{key+'_ms':value for key,value in means.items()}})
    assert len(identities) == 7680
    aggregate = []
    for model, _ in MODELS:
        for system in SYSTEMS:
            rounds = [row for row in per_round if row['model'] == model and row['system'] == system]
            assert sorted(row['round'] for row in rounds) == [1, 2, 3]
            summary = {'model': model, 'system': system, 'rounds': 3, 'measured_requests': 384}
            for key in ALL_KEYS:
                values = [row[key + '_ms'] for row in rounds]
                summary[key + '_mean_ms'] = statistics.mean(values)
                summary[key + '_sample_sd_ms'] = statistics.stdev(values)
            aggregate.append(summary)
    write_csv(OUT / 'b2_component_round_means.csv', per_round)
    write_csv(OUT / 'b2_component_summary.csv', aggregate)
    return per_round, aggregate, len(identities), max_closure_error


def make_appendix(aggregate):
    columns = [next(row for row in aggregate if row['model'] == model
                    and row['system'] == 'TokenProv-Full') for model, _ in MODELS]
    def cells(key):
        return ' & '.join('{:.2f} $\\pm$ {:.2f}'.format(
            row[key + '_mean_ms'], row[key + '_sample_sd_ms']) for row in columns)
    def line(label, key, bold=False):
        if bold:
            label = '\\textbf{' + label + '}'
        return label + ' & ' + cells(key) + r' \\'
    text = [
        '% Requires booktabs and xcolor with the table option (or colortbl).',
        r'\begin{table*}[!t]',
        r'\centering',
        r'\caption{TokenProv cost breakdown across models (ms/request; mean $\pm$ sample standard deviation across three rounds).}',
        r'\label{tab:b2-component-breakdown}',
        r'\footnotesize',
        r'\renewcommand{\arraystretch}{1.10}',
        r'\begin{tabular*}{\textwidth}{@{\hspace{4pt}}l@{\extracolsep{\fill}}ccccc@{\hspace{4pt}}}',
        r'\toprule',
        r'\textbf{Component} & \shortstack{\textbf{Qwen3}\\\textbf{1.7B}} & \shortstack{\textbf{Qwen3}\\\textbf{4B}} & \shortstack{\textbf{Phi-3-mini}\\\textbf{3.8B}} & \shortstack{\textbf{DeepSeek-R1-Distill}\\\textbf{Llama-8B}} & \shortstack{\textbf{Qwen3}\\\textbf{14B}} \\',
        r'\midrule',
        r'\rowcolor{gray!12}',
        r'\multicolumn{6}{l}{\textbf{Online non-LLM work}} \\',
    ]
    for label, key in [
        ('Authorization', 'T_auth'), ('Lifecycle records', 'T_lifecycle'),
        (r'$H\!\rightarrow\!B$ handoffs', 'T_HB_handoff'),
        (r'$B\!\rightarrow\!P$ handoffs', 'T_BP_handoff'),
        ('Provider commitments', 'T_provider_extra'), ('Receipts', 'T_receipt'),
        ('Other online work', 'online_other')]:
        text.append(line(label, key))
    text += [line('Online non-LLM total', 'online_non_llm', True), r'\midrule',
        r'\rowcolor{gray!12}',
        r'\multicolumn{6}{l}{\textbf{Remaining request processing}} \\']
    for label, key in [
        ('Request closure', 'T_close'), ('Merkle construction', 'T_merkle'),
        ('Local settlement creation', 'T_settle_local'),
        ('Evidence persistence', 'T_evidence_write'), ('Local ledger', 'T_ledger'),
        ('Protocol audit', 'T_audit'), ('Other pipeline work', 'remaining_other')]:
        text.append(line(label, key))
    text += [line('Remaining processing total', 'remaining_processing', True), r'\midrule',
        line('Complete non-LLM total', 'pipeline_non_llm', True),
        r'\bottomrule', r'\end{tabular*}', r'\end{table*}', '']
    (OUT / 'b2_appendix_component_table.tex').write_text('\n'.join(text), encoding='utf-8')
    # A complete compilable preview is separate from the drop-in table fragment.
    preview = r'''\documentclass[conference]{IEEEtran}
\usepackage{booktabs}
\usepackage[table]{xcolor}
\usepackage{mathptmx}
\begin{document}
\onecolumn
\appendices
\section{Additional Cost Breakdown}
Table~\ref{tab:b2-component-breakdown} reports the complete component breakdown for
\textsc{TokenProv}, using 128 measured requests per model per round, with eight
sequential model calls per request and concurrency eight.
The online non-LLM total is $T_{\mathrm{online}}-T_{\mathrm{llm}}$; the complete
non-LLM total is $T_{\mathrm{pipeline}}-T_{\mathrm{llm}}$.
Here $T_{\mathrm{llm}}$ is the cumulative model-service HTTP wall time.
Remaining processing is $T_{\mathrm{pipeline}}-T_{\mathrm{online}}$, including
closure, evidence persistence, the local ledger, audit, and surrounding pipeline work.
All these operations are included in the complete request latency reported in B1.
Standard deviations describe the three round means; they are not per-request
standard deviations or confidence intervals. Totals are calculated per round
before aggregation. These components are internal portions of the measured path,
rather than incremental latency relative to a baseline.
\input{b2_appendix_component_table.tex}
\end{document}
'''
    (OUT / 'b2_appendix_preview.tex').write_text(preview, encoding='utf-8')
    integration = r'''% Add to the preamble if not already loaded:
% \usepackage{booktabs}
% \usepackage[table]{xcolor}

% Main text, B2:
\begin{figure}[!t]
  \centering
  \includegraphics[width=\columnwidth]{b2_reference_cost_breakdown.pdf}
  \caption{Cost breakdown for Qwen3-1.7B at concurrency eight with eight sequential
  calls per request. Stacks show three-round mean component costs; error bars
  show the sample standard deviation of each total across the three round means.}
  \label{fig:b2-cost-breakdown}
\end{figure}

% In the existing appendix, after \appendices:
\section{Additional Cost Breakdown}
\label{app:b2-cost}
Table~\ref{tab:b2-component-breakdown} gives the full breakdown across five models.
Online non-LLM work is $T_{\mathrm{online}}-T_{\mathrm{llm}}$, and remaining
processing is $T_{\mathrm{pipeline}}-T_{\mathrm{online}}$.
The latter includes evidence persistence, audit, settlement, and surrounding
pipeline work, all included in the complete request latency.
\input{b2_appendix_component_table.tex}
'''
    (OUT / 'b2_paper_integration.tex').write_text(integration, encoding='utf-8')


def plot(per_round, aggregate):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.colors import to_rgba_array
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import MultipleLocator

    fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for filename in ('times.ttf', 'timesbd.ttf', 'timesi.ttf', 'timesbi.ttf'):
        if (fonts / filename).is_file():
            font_manager.fontManager.addfont(str(fonts / filename))
    font = font_manager.findfont('Times New Roman', fallback_to_default=False)
    # Specify the actual single-column paper width, so the declared point sizes
    # are the point sizes readers see rather than scaled-down screen labels.
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.serif': ['Times New Roman'],
        'font.size': 7.4, 'axes.titlesize': 7.8, 'axes.labelsize': 7.5,
        'xtick.labelsize': 7.1, 'ytick.labelsize': 7.4, 'axes.linewidth': .65,
        'axes.edgecolor': '#36383D', 'text.color': '#24262B',
        'axes.labelcolor': '#24262B', 'xtick.color': '#35383D', 'ytick.color': '#35383D',
        'hatch.linewidth': .58, 'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none'})
    figure_size = (3.5, 1.78)
    png_dpi = 1200
    fig, axes = plt.subplots(1, 2, figsize=figure_size, sharey=True)
    axes[0].set_position([.19, .278, .382, .702])
    axes[1].set_position([.607, .278, .378, .702])
    # Muted fills are paired with darker opaque hatch strokes; the patterns
    # remain informative after grayscale printing at the final paper width.
    palettes = [
        ['#C8C8CD', '#D8978E', '#EDBE87', '#96BDD4', '#E3E4E8'],
        ['#9895BF', '#D8978E', '#96BDD4', '#9299C5', '#E3E4E8'],
    ]
    outline_palettes = [
        ['#5D6068', '#8F4F44', '#92602E', '#3F6C85', '#7C8089'],
        ['#57517F', '#8F4F44', '#3F6C85', '#424F93', '#7C8089'],
    ]
    hatches = ['...', '///', '\\\\', 'xxx', '++']
    groups = [
        [('Authorization', ['T_auth']), ('Lifecycle/logs', ['T_lifecycle']),
         ('Handoffs', ['T_HB_handoff', 'T_BP_handoff']),
         ('Receipts/evidence', ['T_receipt', 'T_provider_extra']), ('Other', ['online_other'])],
        [('Settlement', ['T_close', 'T_merkle', 'T_settle_local']),
         ('Evidence write', ['T_evidence_write']), ('Ledger', ['T_ledger']),
         ('Audit', ['T_audit']), ('Other', ['remaining_other'])],
    ]
    total_keys = ['online_non_llm', 'remaining_processing']
    # Full component names fit beside the two short baseline stacks without
    # increasing the canvas height or reducing the legend font size.
    legend_labels = [[label for label, _ in group] for group in groups]
    ref = {row['system']: row for row in aggregate if row['model'] == 'qwen3-1.7b'}
    plot_rows, bar_artists, bar_labels, legends = [], [], [], []
    for pi, ax in enumerate(axes):
        y = [3, 2, 1, 0]
        left = [0.] * 4
        for (label, keys), color, edge, hatch in zip(
                groups[pi], palettes[pi], outline_palettes[pi], hatches):
            values = [sum(ref[system][key + '_mean_ms'] for key in keys) for system in SYSTEMS]
            bars = ax.barh(y, values, left=left, height=.52, color=color, edgecolor=edge,
                           linewidth=.35, hatch=hatch, label=label, zorder=3)
            bar_artists.extend(bars)
            for index in range(4):
                plot_rows.append({'panel': pi + 1, 'system': SYSTEMS[index],
                    'component': label, 'mean_ms': values[index]})
            left = [a + b for a, b in zip(left, values)]
        totals = [ref[system][total_keys[pi] + '_mean_ms'] for system in SYSTEMS]
        sd = [ref[system][total_keys[pi] + '_sample_sd_ms'] for system in SYSTEMS]
        assert all(math.isclose(a, b, abs_tol=1e-7) for a, b in zip(left, totals))
        ax.errorbar(totals, y, xerr=sd, fmt='none', ecolor='#191919',
                    capsize=1.8, elinewidth=.7, capthick=.7, zorder=5)
        for index, (total, deviation) in enumerate(zip(totals, sd)):
            ax.add_patch(Rectangle((0, y[index] - .26), total, .52,
                fill=False, edgecolor='#36383D', linewidth=.65 if index == 3 else .5, zorder=4))
            bar_labels.append(ax.text(total + deviation + 1.3, y[index], '{:.1f}'.format(total),
                    va='center', ha='left', fontsize=7.4,
                    fontweight='bold' if index == 3 else 'normal',
                    color='#2E3B97' if index == 3 else '#24262B', zorder=6))
        ax.set_xlim(0, 132)
        ax.set_ylim(-.53, 4.25)
        ax.xaxis.set_major_locator(MultipleLocator(50))
        ax.grid(axis='x', color='#DBDDE2', linewidth=.4, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(length=2.6, width=.65, pad=2.5)
        handles, _ = ax.get_legend_handles_labels()
        legend = ax.legend(handles, legend_labels[pi], loc='upper right', ncol=1, fontsize=6.4,
            columnspacing=.55, handlelength=.95, handletextpad=.3,
            labelspacing=.22, borderpad=.3, borderaxespad=.38,
            facecolor='white', edgecolor='#B6BBC3', framealpha=1., fancybox=False)
        legend.get_frame().set_linewidth(.4)
        legends.append(legend)
    for ax in axes:
        ax.set_yticks([3, 2, 1, 0], LABELS)
        if ax.get_yticklabels():
            ax.get_yticklabels()[-1].set_color('#2E3B97')
            ax.get_yticklabels()[-1].set_fontweight('bold')
    axes[1].tick_params(axis='y', labelleft=False, length=0)
    for ax in axes:
        ax.set_xlabel('Time (ms/request)', labelpad=2)
    subtitles = []
    for ax, label, ypos in zip(axes,
            ['(a) Online non-LLM work', '(b) Remaining processing'], [.065, .065]):
        bounds = ax.get_position()
        subtitles.append(fig.text((bounds.x0 + bounds.x1) / 2, ypos, label,
            fontsize=7.7, ha='center', va='center'))
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    outside = []
    for ax, legend in zip(axes, legends):
        xmin, xmax = ax.get_xlim()
        texts = [ax.title, ax.xaxis.label, *ax.get_yticklabels(), *legend.get_texts(),
            *(label for value, label in zip(ax.get_xticks(), ax.get_xticklabels()) if xmin <= value <= xmax)]
        for item in texts:
            if item.get_visible() and item.get_text():
                box = item.get_window_extent(renderer)
                if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
                    outside.append(item.get_text())
    for item in bar_labels:
        box = item.get_window_extent(renderer)
        if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
            outside.append(item.get_text())
    subtitle_clearances_pt = []
    for item, ax in zip(subtitles, axes):
        box = item.get_window_extent(renderer)
        if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
            outside.append(item.get_text())
        if ax.xaxis.label.get_text():
            clearance = ax.xaxis.label.get_window_extent(renderer).y0 - box.y1
            assert clearance >= 1.5 * fig.dpi / 72, 'Subtitle too close to x label'
            subtitle_clearances_pt.append(clearance * 72 / fig.dpi)
    assert not outside, outside
    # Internal legends sit in the vacant top-right region rather than obscuring
    # a stack or its uncertainty/mean label.
    for ax, legend in zip(axes, legends):
        legend_box = legend.get_window_extent(renderer)
        assert all(not legend_box.overlaps(bar.get_window_extent(renderer))
                   for bar in bar_artists if bar.axes is ax and bar.get_width() > 0), 'Legend obscures stack'
        assert all(not legend_box.overlaps(label.get_window_extent(renderer))
                   for label in bar_labels if label.axes is ax), 'Legend obscures total label'
    for ext in ('png', 'pdf', 'svg'):
        fig.savefig(OUT / ('b2_reference_cost_breakdown.' + ext),
                    dpi=png_dpi if ext == 'png' else 300)
    converted = 0
    for artist in fig.findobj():
        for name in ('color', 'facecolor', 'edgecolor', 'markeredgecolor', 'markerfacecolor'):
            get, set_ = getattr(artist, 'get_' + name, None), getattr(artist, 'set_' + name, None)
            if not get or not set_:
                continue
            try:
                rgba = to_rgba_array(get())
                if not len(rgba):
                    continue
                gray = rgba[:, 0] * .2126 + rgba[:, 1] * .7152 + rgba[:, 2] * .0722
                rgba[:, :3] = gray[:, None]
                set_(rgba[0] if len(rgba) == 1 else rgba)
                converted += 1
            except (TypeError, ValueError, AttributeError):
                pass
    for ext in ('png', 'pdf'):
        fig.savefig(OUT / ('b2_reference_cost_breakdown_grayscale.' + ext),
                    dpi=png_dpi if ext == 'png' else 300)
    gray_image = plt.imread(OUT / 'b2_reference_cost_breakdown_grayscale.png')
    gray_channel_error = float(abs(gray_image[:, :, 0] - gray_image[:, :, 1]).max())
    gray_channel_error = max(gray_channel_error, float(abs(gray_image[:, :, 1] - gray_image[:, :, 2]).max()))
    assert gray_channel_error == 0, gray_channel_error
    plt.close(fig)
    write_csv(OUT / 'b2_main_plot_values.csv', plot_rows)
    return {'font': font, 'figure_size_inches': list(figure_size),
        'png_dpi': png_dpi,
        'native_output_width_inches': figure_size[0], 'frame_width_pt': .65,
        'font_sizes_pt': {'axis_labels': 7.5, 'ticks_x': 7.1, 'scheme_labels': 7.4,
                          'subtitles': 7.7, 'legends': 6.4, 'total_values': 7.4},
        'layout': 'single-column width; two side-by-side panels with shared scheme rows',
        'legend_columns': 1, 'legend_display_labels': legend_labels,
        'panel_gap_pt': (axes[1].get_position().x0 - axes[0].get_position().x1) * figure_size[0] * 72,
        'subtitle_clearances_pt': subtitle_clearances_pt,
        'scheme_label_rotation_degrees': 0,
        'palette_fills': palettes, 'palette_outlines': outline_palettes,
        'legend_obscures_data': False, 'grayscale_channel_error': gray_channel_error,
        'subplot_titles_position': 'below each subplot',
        'labels_outside_figure': outside, 'grayscale_artist_property_conversions': converted,
        'axes_start_at_zero': True, 'distinct_component_hatches': hatches,
        'reference_mean_ms': {system: {key: ref[system][key + '_mean_ms']
            for key in total_keys + ['pipeline_non_llm']} for system in SYSTEMS}}


def main():
    OUT.mkdir(exist_ok=True)
    rounds, aggregate, request_count, closure_error = derive()
    make_appendix(aggregate)
    verification = plot(rounds, aggregate)
    verification.update({'formal_request_count': request_count, 'round_mean_rows': len(rounds),
        'model_scheme_groups': len(aggregate), 'max_closure_error_ms': closure_error,
        'statistical_unit': 'three round means, each using 128 measured requests',
        'no_inference_or_proof_execution': True})
    (OUT / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n', encoding='utf-8')
    readme = '''# B2 cost breakdown

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
'''
    (OUT / 'README.md').write_text(readme, encoding='utf-8')
    print(json.dumps({'formal_requests': request_count, 'output': str(OUT),
                      'reference': verification['reference_mean_ms']['TokenProv-Full']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
