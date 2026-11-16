"""Plot historical cumulative validation measurements; launch no experiments."""
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
OUT = output_dir('f')
VARIANTS = ['Vanilla', 'Vanilla + Lifecycle', '+ Bilateral Handoff',
            '+ Provider Binding', 'TokenProv-Full']



def derive():
    source = DATA / 'f/G2_performance_ablation.csv'
    with source.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 25
    stages = []
    for index, variant in enumerate(VARIANTS):
        selected = sorted([r for r in rows if r['variant'] == variant], key=lambda r: int(r['run']))
        assert [int(r['run']) for r in selected] == [1, 2, 3, 4, 5]
        assert all(int(r['sample_count']) == 1000 for r in selected)
        assert all(r['measurement_type'] == 'PROTOCOL_MICROBENCHMARK' for r in selected)
        time = [float(r['mean_protocol_overhead_ms']) for r in selected]
        assert all(math.isclose(float(r['mean_online_latency_ms']), t) for r, t in zip(selected, time))
        size = [float(r['bytes_per_request']) / 1024 for r in selected]
        item = {'variant': variant, 'time_ms_rounds': time, 'selected_payload_KiB_rounds': size}
        for key, values in [('time_ms', time), ('selected_payload_KiB', size)]:
            previous = [0.] * 5 if index == 0 else stages[-1][key + '_rounds']
            changes = [value - before for value, before in zip(values, previous)]
            assert all(x >= 0 for x in changes)
            item[key + '_mean'] = statistics.mean(values)
            item[key + '_sd'] = statistics.stdev(values)
            item[key + '_increment_rounds'] = changes
            item[key + '_increment_mean'] = statistics.mean(changes)
            item[key + '_increment_sd'] = statistics.stdev(changes)
        stages.append(item)
    for key in ['time_ms', 'selected_payload_KiB']:
        assert math.isclose(sum(r[key + '_increment_mean'] for r in stages), stages[-1][key + '_mean'], abs_tol=1e-10)
        total = stages[-1][key + '_mean']
        for stage in stages:
            stage[key + '_increment_share_percent'] = 100 * stage[key + '_increment_mean'] / total
    for stage in stages:
        mean = stage['time_ms_mean']
        stage['time_ms_cv_percent'] = 100 * stage['time_ms_sd'] / mean
        stage['time_ms_absolute_deviation_percent_rounds'] = [
            100 * abs(value - mean) / mean for value in stage['time_ms_rounds']]
        stage['time_ms_signed_deviation_percent_rounds'] = [
            100 * (value / mean - 1) for value in stage['time_ms_rounds']]
    data = {'stages': stages, 'calls_per_fixture': 8, 'lifecycle_events_per_fixture': 32,
        'runs': 5, 'samples_per_variant_per_run': 1000, 'warmups_per_variant_per_run': 100,
        'formal_validation_count': 25000, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'time_scope': 'cumulative validation on prebuilt fixtures; serialization measured for size after timer',
        'size_scope': 'selected serialized payload; not the full retained evidence bundle'}
    (OUT / 'f_waterfall_values.json').write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return data


def plot(data):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.colors import to_rgba_array
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import MultipleLocator
    import numpy as np

    font_dir = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('times.ttf', 'timesbd.ttf', 'timesi.ttf', 'timesbi.ttf'):
        if (font_dir / name).is_file():
            font_manager.fontManager.addfont(str(font_dir / name))
    font = font_manager.findfont('Times New Roman', fallback_to_default=False)
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': 8,
        'axes.labelsize': 8, 'xtick.labelsize': 7.5, 'ytick.labelsize': 7.6,
        'axes.linewidth': .6, 'hatch.linewidth': .70, 'pdf.fonttype': 42,
        'ps.fonttype': 42, 'svg.fonttype': 'none'})
    fig = plt.figure(figsize=(3.5, 3.95))
    time_ax = fig.add_axes([.15, .755, .83, .22])
    payload_ax = fig.add_axes([.15, .51, .83, .20], sharex=time_ax)
    spread_ax = fig.add_axes([.28, .12, .56, .235])
    time_ax.tick_params(axis='x', bottom=False, labelbottom=False)
    stage_names = ['Lifecycle', 'Handoff', 'Provider', 'Settlement']
    colors = ['#E2ABA2', '#FDD6AF', '#B9D6E9', '#ADAACF', '#2E3B97']
    edge_colors = ['#B76252', '#A66B32', '#527F98', '#666095', '#26317C']
    waterfall_colors = ['#D8978E', '#EDBE87', '#96BDD4', '#9895BF', '#2E3B97']
    waterfall_edges = ['#8F4F44', '#92602E', '#3F6C85', '#57517F', '#26317C']
    label_colors = ['#282A30'] * 4 + ['#2E3B97']
    hatches = ['///', '\\\\\\', '...', 'xxx', '']
    stages = data['stages'][1:]
    total = data['stages'][-1]
    texts, errorbar_ranges = [], []
    for ax, key, ylabel, ymax, tickstep in [
            (time_ax, 'time_ms', 'Time (ms)', 24, 10),
            (payload_ax, 'selected_payload_KiB', 'Payload (KiB)', 46, 20)]:
        cumulative = [s[key + '_mean'] for s in data['stages']]
        heights = [s[key + '_increment_mean'] for s in stages] + [total[key + '_mean']]
        bottoms = cumulative[:-1] + [0]
        tops = cumulative[1:] + [cumulative[-1]]
        errors = [s[key + '_sd'] for s in stages] + [total[key + '_sd']]
        for index in range(4):
            ax.plot([index + .32, index + .68], [tops[index]] * 2,
                    color='#8B959D', linewidth=.65, linestyle=(0, (3, 2)), zorder=2)
        for index, (height, bottom, color, hatch) in enumerate(zip(heights, bottoms, waterfall_colors, hatches)):
            ax.bar(index, height, bottom=bottom, width=.64, color=color, hatch=hatch,
                   edgecolor=waterfall_edges[index], linewidth=0, zorder=3)
            ax.bar(index, height, bottom=bottom, width=.64, color='none',
                   edgecolor=waterfall_edges[index], linewidth=.70, zorder=4)
            if key == 'time_ms':
                ax.errorbar(index, tops[index], yerr=errors[index], fmt='none', color='#111111',
                            capsize=2, elinewidth=.7, capthick=.7, zorder=5)
                errorbar_ranges.append({'stage': (stage_names + ['Total'])[index],
                    'cumulative_mean_ms': tops[index], 'cumulative_sample_sd_ms': errors[index]})
            if index < 4:
                share = stages[index][key + '_increment_share_percent']
                label = f'+{height:.2f}\n{share:.1f}%'
                size, weight = 7.4, 'normal'
            else:
                label = f'{height:.2f}'
                if key == 'time_ms':
                    label += f'\n\u00b1{errors[index]:.2f}'
                size, weight = 8.1, 'bold'
            texts.append(ax.annotate(label, (index, tops[index] + errors[index]),
                xytext=(0, 1.8), textcoords='offset points', ha='center', va='bottom',
                fontsize=size, fontweight=weight, color=label_colors[index], linespacing=1.0))
        ax.set_xlim(-.55, 4.55)
        ax.set_ylim(0, ymax)
        ax.yaxis.set_major_locator(MultipleLocator(tickstep))
        ax.set_ylabel(ylabel, labelpad=3)
        ax.set_axisbelow(True)
        ax.grid(axis='y', color='#DCE1E4', linewidth=.45)
        ax.tick_params(axis='y', length=2.5, width=.6, pad=2)
        ax.spines[['top', 'right']].set_visible(False)
    payload_ax.set_xticks(np.arange(5), stage_names + ['Total'])
    payload_ax.tick_params(axis='x', length=0, pad=3)
    for index, tick in enumerate(payload_ax.get_xticklabels()):
        tick.set_color(label_colors[index])
        if index == 4:
            tick.set_fontweight('bold')

    deviations = np.array([s['time_ms_signed_deviation_percent_rounds'] for s in stages])
    assert np.abs(deviations).max() < 1.2
    spread_ax.axvline(0, color='#4B5661', linewidth=.85, linestyle=(0, (3, 2)), zorder=3)
    for index, stage in enumerate(stages):
        cv = stage['time_ms_cv_percent']
        assert math.isclose(float(np.std(deviations[index], ddof=1)), cv, rel_tol=1e-12)
        spread_ax.hlines(index, -1.2, 1.2, color='#E1E5E8', linewidth=.45, zorder=0)
        spread_ax.plot([-cv, cv], [index, index], color=colors[index], linewidth=7,
                       alpha=.80, solid_capstyle='butt', zorder=2)
        spread_ax.vlines([-cv, cv], index - .075, index + .075,
                         color=edge_colors[index], linewidth=.75, zorder=3)
        spread_ax.scatter(deviations[index], index + np.linspace(-.09, .09, 5), s=14,
                          facecolor=colors[index], edgecolor=edge_colors[index], linewidth=.9, zorder=4)
        texts.append(spread_ax.text(1.50, index, f'{cv:.2f}%', ha='center', va='center',
            fontsize=8, color=colors[-1] if index == 3 else label_colors[index],
            fontweight='bold' if index == 3 else 'normal', clip_on=False))
    texts.append(spread_ax.text(1.50, -.80, 'CV', ha='center', va='center', fontsize=8, clip_on=False))
    spread_ax.set_xlim(-1.2, 1.2)
    spread_ax.set_ylim(3.5, -.5)
    spread_ax.set_yticks(np.arange(4), ['Lifecycle', '+ Handoff', '+ Provider', '+ Settlement'])
    spread_ax.set_xticks([-1, 0, 1])
    spread_ax.set_xlabel('Deviation from mean (%)', labelpad=3)
    spread_ax.tick_params(axis='x', length=2.5, width=.65, pad=2, labelsize=7.8)
    spread_ax.tick_params(axis='y', length=0, pad=4, labelsize=8)
    for spine in spread_ax.spines.values():
        spine.set_visible(True)
        spine.set_color('#3C464E')
        spine.set_linewidth(.65)
    # Attach the descriptive CV column to the data frame without extending its x scale.
    for y in [-.5, 3.5]:
        spread_ax.plot([1.2, 1.8], [y, y], color='#3C464E', linewidth=.65,
                       clip_on=False, zorder=5)
    spread_ax.plot([1.8, 1.8], [-.5, 3.5], color='#3C464E', linewidth=.65,
                   clip_on=False, zorder=5)
    for y in [.5, 1.5, 2.5]:
        spread_ax.plot([1.2, 1.8], [y, y], color='#E1E5E8', linewidth=.45,
                       clip_on=False, zorder=0)
    legend = fig.legend(handles=[
        Line2D([], [], marker='o', linestyle='none', markersize=3.7, markerfacecolor='#DADCE2',
               markeredgecolor='#303030', markeredgewidth=.9, label='Run mean'),
        Patch(facecolor='#DADCE2', alpha=.80, edgecolor='none', label='\u00b11 SD')],
        loc='upper center', bbox_to_anchor=(.55, .405), ncol=2, frameon=False,
        fontsize=7.4, handlelength=1.05, handletextpad=.45, columnspacing=1.5, borderpad=0)
    subtitles = [fig.text(.55, .450, '(a) Incremental costs and contributions', ha='center',
                         va='center', fontsize=8.4),
                 fig.text(.55, .022, '(b) Repeatability of cumulative validation', ha='center',
                         va='center', fontsize=8.4)]
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    outside = []
    candidates = [*texts, *subtitles, *legend.get_texts()]
    for ax in [time_ax, payload_ax, spread_ax]:
        candidates.extend([ax.xaxis.label, ax.yaxis.label, *ax.get_xticklabels(), *ax.get_yticklabels()])
    for text in candidates:
        if not text.get_visible() or not text.get_text():
            continue
        if text in time_ax.get_yticklabels() and not 0 <= text.get_position()[1] <= 24:
            continue
        if text in payload_ax.get_yticklabels() and not 0 <= text.get_position()[1] <= 46:
            continue
        box = text.get_window_extent(renderer)
        if box.x0 < 0 or box.y0 < 0 or box.x1 > fig.bbox.width or box.y1 > fig.bbox.height:
            outside.append(text.get_text())
    assert not outside, outside
    assert all(not subtitles[0].get_window_extent(renderer).overlaps(t.get_window_extent(renderer))
               for t in payload_ax.get_xticklabels())
    assert not subtitles[1].get_window_extent(renderer).overlaps(spread_ax.xaxis.label.get_window_extent(renderer))
    for ext in ['pdf', 'png', 'svg']:
        fig.savefig(OUT / ('f_component_waterfalls.' + ext), dpi=300, bbox_inches='tight', pad_inches=.035)
    for artist in fig.findobj():
        for name in ['color', 'facecolor', 'edgecolor', 'markeredgecolor', 'markerfacecolor']:
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
    for ext in ['pdf', 'png']:
        fig.savefig(OUT / ('f_component_waterfalls_grayscale.' + ext), dpi=300,
                    bbox_inches='tight', pad_inches=.035)
    gray_image = plt.imread(OUT / 'f_component_waterfalls_grayscale.png')
    gray_channel_error = float(np.ptp(gray_image[:, :, :3], axis=2).max())
    assert gray_channel_error == 0, gray_channel_error
    plt.close(fig)
    return {'font': font, 'figure_size_inches': [3.5, 3.95], 'labels_outside_figure': outside,
        'palette_fill': dict(zip(stage_names + ['Total'], colors)),
        'palette_outline': dict(zip(stage_names + ['Total'], edge_colors)),
        'waterfall_palette_fill': dict(zip(stage_names + ['Total'], waterfall_colors)),
        'waterfall_palette_outline': dict(zip(stage_names + ['Total'], waterfall_edges)),
        'waterfall_hatches': dict(zip(stage_names + ['Total'], hatches)),
        'waterfall_hatch_linewidth_pt': .70,
        'waterfall_axes_start_at_zero': True, 'waterfall_whiskers': errorbar_ranges,
        'waterfall_errorbar_definition': 'sample SD of cumulative configuration run means',
        'repeatability_points': deviations.tolist(),
        'repeatability_interval_definition': 'plus/minus cumulative sample SD expressed as percentage of configuration mean',
        'maximum_absolute_deviation_percent': float(np.abs(deviations).max()),
        'grayscale_max_rgb_channel_difference': gray_channel_error,
        'cumulative_cv_percent': [s['time_ms_cv_percent'] for s in stages],
        'final_cumulative_time_range_ms': [min(total['time_ms_rounds']), max(total['time_ms_rounds'])],
        'historical_rounds': 5, 'formal_validations': 25000, 'new_experiments_executed': False}


def integration():
    (OUT / 'f_paper_integration.tex').write_text(r'''\begin{figure}[!t]
\centering
\includegraphics[width=\columnwidth]{f_component_waterfalls.pdf}
\caption{Costs and repeatability on prebuilt eight-call, 32-event fixtures.
(a) Waterfalls show mean paired increments in validation time and selected
payload; labels give increments and shares of the final total. Time whiskers
show sample SDs of cumulative configuration means. Totals include the
0.47\,$\mu$s/22\,B baseline. (b) Points show the signed percentage deviation
of each run mean from its cumulative configuration mean; shaded intervals
span $\pm1$ sample SD, with CV reported at right. Five historical runs each use
1,000 measurements after 100 warmups per configuration. Serialization is
outside the validation timer.}
\label{fig:f-component-costs}
\end{figure}
''', encoding='utf-8')
    (OUT / 'f_preview.tex').write_text(r'''\documentclass[conference]{IEEEtran}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage[table]{xcolor}
\usepackage{amsmath}
\usepackage{mathptmx}
\begin{document}
\input{f_main_rule_table.tex}
\input{f_paper_integration.tex}
\clearpage
\appendices
\input{f_appendix.tex}
\end{document}
''', encoding='utf-8')
    (OUT / 'f_main_rule_table_preview.tex').write_text(r'''\documentclass[conference]{IEEEtran}
\usepackage{booktabs}
\usepackage[table]{xcolor}
\usepackage{amsmath}
\usepackage{mathptmx}
\usepackage[active,tightpage]{preview}
\makeatletter
\renewenvironment{table}[1][]{\begin{minipage}{\columnwidth}\def\@captype{table}}{\end{minipage}}
\makeatother
\begin{document}
\begin{preview}
\input{f_main_rule_table.tex}
\end{preview}
\end{document}
''', encoding='utf-8')


def main():
    OUT.mkdir(exist_ok=True)
    data = derive()
    result = plot(data)
    (OUT / 'f_figure_verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    integration()
    print(json.dumps({'output': str(OUT), 'validation_ms': data['stages'][-1]['time_ms_mean'],
        'selected_payload_KiB': data['stages'][-1]['selected_payload_KiB_mean'],
        'labels_outside': result['labels_outside_figure']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
