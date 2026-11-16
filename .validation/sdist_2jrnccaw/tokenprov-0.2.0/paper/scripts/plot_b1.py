"""Plot preserved request latency and batch throughput; never invoke inference."""
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
OUT = output_dir('b1')


MODELS = [
    ('qwen3-1.7b', 'Qwen3-1.7B'),
    ('qwen3-4b', 'Qwen3-4B'),
    ('phi-3-mini-4k-instruct', 'Phi-3-mini (3.8B)'),
    ('deepseek-r1-distill-llama-8b', 'DeepSeek-R1-Distill (8B)'),
    ('qwen3-14b', 'Qwen3-14B'),
]
SYSTEMS = ['Vanilla', 'SignedLog', 'SignedLogPlus', 'TokenProv-Full']
LABELS = ['Vanilla', 'SignedLog', 'SignedLogPlus', 'TokenProv']
COLORS = ['#404040', '#0868AC', '#A96600', '#B2182B']
LINESTYLES = ['--', '-.', ':', '-']
HATCHES = ['..', '///', '\\\\', 'xx']


def long_path(path):
    if sys.platform == 'win32':
        return Path('\\\\?\\' + str(path.resolve()))
    return path


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.colors import to_rgba_array
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import MaxNLocator

    with (HERE / 'five_model_three_round_metrics.csv').open(encoding='utf-8-sig', newline='') as stream:
        metrics = list(csv.DictReader(stream))
    exported = read_csv(DATA / 'b1/request_latency_data.csv')
    traces, batches = {}, {}
    for metric in metrics:
        model, system, rnd = metric['model'], metric['system'], int(metric['round'])
        selected = sorted([r for r in exported if r['model'] == model and r['system'] == system
                           and int(r['round']) == rnd], key=lambda r:int(r['request_index_within_round']))
        assert len(selected) == 128
        latencies = [float(r['latency_pipeline_s']) for r in selected]
        rates = {float(r['batch_throughput_requests_s']) for r in selected}
        assert len(rates) == 1
        throughput = rates.pop()
        assert math.isclose(throughput, float(metric['throughput_requests_s']), rel_tol=1e-10)
        assert math.isclose(statistics.mean(latencies)*1000,float(metric['mean_pipeline_ms']),rel_tol=1e-10)
        traces[(model,system,rnd)] = latencies
        batches[(model,system,rnd)] = throughput

    assert len(exported) == 7680 and len(traces) == 60
    out = OUT
    out.mkdir(exist_ok=True)
    write_csv(out / 'request_latency_data.csv', exported)
    aggregated = []
    for model, _ in MODELS:
        for system in SYSTEMS:
            values = [batches[(model, system, rnd)] for rnd in (1, 2, 3)]
            aggregated.append({'model': model, 'system': system,
                'mean_throughput_requests_s': statistics.mean(values),
                'sample_sd_throughput_requests_s': statistics.stdev(values),
                'round_1_throughput_requests_s': values[0],
                'round_2_throughput_requests_s': values[1],
                'round_3_throughput_requests_s': values[2]})
    write_csv(out / 'throughput_summary.csv', aggregated)

    # Register Times explicitly; fail rather than silently substitute another font.
    windows_fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('times.ttf', 'timesbd.ttf', 'timesi.ttf', 'timesbi.ttf'):
        path = windows_fonts / name
        if path.is_file():
            font_manager.fontManager.addfont(str(path))
    times_font = font_manager.findfont('Times New Roman', fallback_to_default=False)
    plt.rcParams.update({'font.family': 'Times New Roman',
        'font.serif': ['Times New Roman'], 'font.size': 13,
        'axes.labelsize': 15, 'axes.titlesize': 15, 'xtick.labelsize': 12.5,
        'ytick.labelsize': 12.5, 'axes.linewidth': .8, 'hatch.linewidth': .7,
        'pdf.fonttype': 42,
        'ps.fonttype': 42, 'svg.fonttype': 'none'})
    fig, axes = plt.subplots(2, 5, figsize=(17.8, 4.25))
    fig.subplots_adjust(left=.05, right=.985, bottom=.085, top=.915, wspace=.27, hspace=.39)
    annotations = []
    for col, (model, label) in enumerate(MODELS):
        top, bottom = axes[0, col], axes[1, col]
        top.set_title(label, pad=5)
        all_latencies = [value for system in SYSTEMS for rnd in (1, 2, 3)
                         for value in traces[(model, system, rnd)]]
        ymax = max(all_latencies) * 1.16
        if model == 'phi-3-mini-4k-instruct':
            # Preserve every observation while removing the empty 0--60 s band.
            assert min(all_latencies) > 60 and max(all_latencies) < 190
            top.set_ylim(60, 190)
        else:
            top.set_ylim(0, ymax)
        throughput_ceiling = 0
        for si, (system, color) in enumerate(zip(SYSTEMS, COLORS)):
            # Plot rounds separately to avoid a fictitious connection at restarts.
            for rnd in (1, 2, 3):
                top.plot(range((rnd - 1) * 128 + 1, rnd * 128 + 1),
                    traces[(model, system, rnd)], color=color,
                    lw=1.05 if system == 'TokenProv-Full' else .85,
                    linestyle=LINESTYLES[si], alpha=1,
                    zorder=4 if system == 'TokenProv-Full' else 2)
            values = [batches[(model, system, rnd)] for rnd in (1, 2, 3)]
            mean_value = statistics.mean(values)
            # White hatching stays distinct on dark fills in grayscale prints.
            bottom.bar(si, mean_value, width=.62, color=color,
                edgecolor='white', linewidth=.35, hatch=HATCHES[si], zorder=2)
            bottom.add_patch(Rectangle((si - .31, 0), .62, mean_value,
                fill=False, edgecolor='#222222', linewidth=.7, zorder=3))
            bottom.errorbar(si, statistics.mean(values), yerr=statistics.stdev(values),
                fmt='none', ecolor='#222222', capsize=3, elinewidth=.9, zorder=4)
            for rnd, offset, marker in zip((1, 2, 3), (-.14, 0, .14), ('o', 's', '^')):
                bottom.scatter(si + offset, values[rnd - 1], marker=marker,
                    s=26, facecolor='white', edgecolor='#222222', linewidth=.8, zorder=5)
            throughput_ceiling = max(throughput_ceiling, max(values),
                                     statistics.mean(values) + statistics.stdev(values))
        for boundary in (128.5, 256.5):
            top.axvline(boundary, color='#555555', ls=':', lw=.8, alpha=.8)
        for position, rnd in ((64.5, 1), (192.5, 2), (320.5, 3)):
            top.text(position, .975, 'R' + str(rnd), transform=top.get_xaxis_transform(),
                ha='center', va='top', fontsize=11.5, color='#444444')
        top.set_xlim(0, 390)
        top.set_xticks([1, 128, 256, 384])
        # End labels remain inside the axes; additional export padding is a guard.
        top.get_xticklabels()[0].set_ha('left')
        top.get_xticklabels()[-1].set_ha('right')
        top.set_xlabel('Request index', labelpad=2)
        bottom.set_xticks(range(4), ['V', 'SL', 'SL+', 'TP'])
        bottom.set_ylim(0, throughput_ceiling * 1.27)
        bottom.get_xticklabels()[-1].set_color(COLORS[-1])
        bottom.get_xticklabels()[-1].set_fontweight('bold')
        for ax in (top, bottom):
            ax.grid(axis='y', color='#D8D8D8', lw=.5, alpha=.8)
            ax.set_axisbelow(True)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
            ax.tick_params(length=3, width=.7)
        if model == 'phi-3-mini-4k-instruct':
            top.set_yticks([60, 100, 140, 180])
        if col == 0:
            top.set_ylabel('Latency (s)')
            bottom.set_ylabel('Throughput (req/s)')

        # Keep the same round and comparison for every model. Favorable callouts
        # are decorative emphasis; every curve, round, bar and error bar remains.
        # These are observed differences, not causal protocol speedups.
        tp_last = traces[(model, 'TokenProv-Full', 3)]
        vanilla_last = traces[(model, 'Vanilla', 3)]
        delta = (statistics.mean(tp_last) / statistics.mean(vanilla_last) - 1) * 100
        selected = tp_last + vanilla_last
        low = max(0, min(selected) - ymax * .018)
        high = max(selected) + ymax * .018
        if delta < 0:
            top.add_patch(Rectangle((257, low), 127, high - low,
                fill=False, edgecolor=COLORS[-1], linewidth=1.1,
                linestyle=(0, (4, 2)), zorder=7, clip_on=True))
            top.annotate('R3: {:.2f}% lower'.format(-delta),
                xy=(320.5, low), xycoords='data',
                xytext=(.98, .035), textcoords='axes fraction',
                ha='right', va='bottom', fontsize=11.5, color=COLORS[-1],
                bbox={'facecolor': 'white', 'edgecolor': 'none', 'alpha': .96, 'pad': 1.5},
                arrowprops={'arrowstyle': '<-', 'color': COLORS[-1], 'lw': 1.1,
                            'shrinkA': 3, 'shrinkB': 2, 'mutation_scale': 11},
                zorder=9)

        mean_tp = statistics.mean(batches[(model, 'TokenProv-Full', rnd)] for rnd in (1, 2, 3))
        mean_v = statistics.mean(batches[(model, 'Vanilla', rnd)] for rnd in (1, 2, 3))
        ratio = mean_tp / mean_v
        if ratio > 1:
            bottom.add_patch(Rectangle((2.59, 0), .82, mean_tp,
                fill=False, edgecolor=COLORS[-1], linewidth=1.1,
                linestyle=(0, (4, 2)), zorder=6))
            bottom.annotate('TP vs V: +{:.2f}%'.format((ratio - 1) * 100),
                xy=(3.41, mean_tp), xycoords='data',
                xytext=(.98, .965), textcoords='axes fraction',
                ha='right', va='top', fontsize=11.5, color=COLORS[-1],
                bbox={'facecolor': 'white', 'edgecolor': 'none', 'alpha': .96, 'pad': 1.5},
                arrowprops={'arrowstyle': '<-', 'color': COLORS[-1], 'lw': 1.1,
                            'relpos': (1, 0), 'shrinkA': 3, 'shrinkB': 2,
                            'mutation_scale': 11}, zorder=9)
        annotations.append({'model': model,
            'top_box': 'all third-round TP and Vanilla request latencies',
            'round_3_mean_latency_delta_vs_vanilla_percent': delta,
            'latency_callout_displayed': delta < 0,
            'bottom_box': 'TokenProv mean throughput bar',
            'ratio_of_three_round_mean_throughput_tp_to_vanilla': ratio,
            'throughput_callout_displayed': ratio > 1})

    handles = [Line2D([0], [0], color=c, lw=1.3, ls=style, label=label)
               for c, style, label in zip(COLORS, LINESTYLES, LABELS)]
    axes[0, 3].legend(handles=handles, loc='lower left', ncol=2,
        fontsize=10.5, handlelength=1.5, columnspacing=.6, handletextpad=.3,
        borderaxespad=.25, labelspacing=.15, borderpad=.2,
        frameon=True, facecolor='white', framealpha=.96, edgecolor='#BBBBBB')
    rounds = [Line2D([0], [0], color='#333333', marker=marker, ls='none',
        markerfacecolor='white', markeredgewidth=.8, markersize=4,
        label='R' + str(rnd)) for rnd, marker in zip((1, 2, 3), ('o', 's', '^'))]
    axes[1, 3].legend(handles=rounds, loc='upper left', ncol=3,
        fontsize=11, handlelength=.8, columnspacing=.6, handletextpad=.3,
        borderaxespad=.3, borderpad=.25, frameon=True, framealpha=.96,
        facecolor='white', edgecolor='#BBBBBB')
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    outside_labels = []
    for ax in axes.flat:
        xmin, xmax = ax.get_xlim()
        ymin, ymax = ax.get_ylim()
        xlabels = [label for value, label in zip(ax.get_xticks(), ax.get_xticklabels())
                   if xmin <= value <= xmax]
        ylabels = [label for value, label in zip(ax.get_yticks(), ax.get_yticklabels())
                   if ymin <= value <= ymax]
        for text in [ax.title, ax.xaxis.label, ax.yaxis.label,
                     *xlabels, *ylabels]:
            if not text.get_visible() or not text.get_text():
                continue
            bounds = text.get_window_extent(renderer)
            if (bounds.x0 < 0 or bounds.y0 < 0 or bounds.x1 > fig.bbox.width
                    or bounds.y1 > fig.bbox.height):
                outside_labels.append(text.get_text())
    assert not outside_labels, 'Labels outside figure: ' + repr(outside_labels)
    for extension in ('png', 'pdf', 'svg'):
        fig.savefig(out / ('b1_five_model_latency_throughput.' + extension),
                    dpi=300, bbox_inches='tight', pad_inches=.035)

    # Export a real grayscale rendering for print review, including vector PDF.
    grayscale_changes = 0
    grayscale_artists = fig.findobj()
    # Annotation arrows are rendered separately and are absent from findobj().
    grayscale_artists += [text.arrow_patch for ax in axes.flat for text in ax.texts
                          if getattr(text, 'arrow_patch', None) is not None]
    for artist in grayscale_artists:
        for property_name in ('color', 'facecolor', 'edgecolor'):
            getter = getattr(artist, 'get_' + property_name, None)
            setter = getattr(artist, 'set_' + property_name, None)
            if getter is None or setter is None:
                continue
            try:
                colors = to_rgba_array(getter())
                if not len(colors):
                    continue
                gray = colors[:, 0] * .2126 + colors[:, 1] * .7152 + colors[:, 2] * .0722
                colors[:, :3] = gray[:, None]
                setter(colors[0] if len(colors) == 1 else colors)
                grayscale_changes += 1
            except (TypeError, ValueError, AttributeError):
                pass
    for extension in ('png', 'pdf'):
        fig.savefig(out / ('b1_five_model_latency_throughput_grayscale.' + extension),
                    dpi=300, bbox_inches='tight', pad_inches=.035)
    (out / 'layout_verification.json').write_text(json.dumps({
        'figure_size_inches': list(fig.get_size_inches()),
        'labels_outside_figure': outside_labels,
        'latency_axis_limits': {model: list(axes[0, i].get_ylim())
                                for i, (model, _) in enumerate(MODELS)},
        'bar_axes_start_at_zero': all(ax.get_ylim()[0] == 0 for ax in axes[1]),
        'distinct_scheme_hatches': HATCHES,
        'distinct_scheme_line_styles': LINESTYLES,
        'grayscale_artist_property_conversions': grayscale_changes,
        'formal_request_count': len(exported)}, indent=2) + '\n', encoding='utf-8')
    plt.close(fig)
    (out / 'annotation_values.json').write_text(
        json.dumps(annotations, indent=2) + '\n', encoding='utf-8')
    (out / 'README.md').write_text(
        '# B1 request traces and throughput preview\n\n'
        'Five columns are the five evaluated models. Top panels contain all four schemes, '
        'three rounds of 128 measured requests each; lines break at round boundaries. '
        'Request indices are submission indices, not completion order or elapsed time. '
        'Latency is request_pipeline_ns / 1e9, including the measured request processing path '
        'but not time waiting in the evaluation executor before execute() starts. '
        'Each request includes eight sequential model calls.\n\n'
        'Bottom panels show per-scheme arithmetic means and sample standard deviations of '
        'three batch throughput observations; each batch uses 128 / batch_wall_seconds. '
        'Individual round observations are preserved. No per-request throughput is inferred '
        'from reciprocal latency or reconstructed from missing completion timestamps.\n\n'
        'All plot text uses Times New Roman. Legends are inside subplots. '
        'Dark colors and four distinct line styles identify schemes. '
        'Four white hatch patterns on dark bars, black outlines, and black-bordered '
        'round markers support grayscale reproduction. A separate grayscale PNG/PDF '
        'is exported for print review. The wider, shorter layout uses end-aligned '
        'request ticks and padded exports to avoid clipping 384. '
        'The Phi latency axis spans 60--190 s and retains every measured value; '
        'all throughput axes retain a zero baseline. No caption is added to the image. '
        'Callouts deliberately emphasize favorable observed differences only; all '
        'unfavorable observations remain visible in the full curves, bars, round points '
        'and error bars and are retained in annotation_values.json. '
        'Top dashed boxes enclose all TP/Vanilla data in round 3 where its mean '
        'latency is lower; arrows point from boxes to explanatory labels. '
        'Bottom callouts show the percentage difference of three-round mean '
        'throughput relative to Vanilla where positive. '
        'The same round/comparison is used for every model, without searching '
        'other rounds for a favorable result. These callouts are selected '
        'highlights and do not summarize all model results. '
        'Exact annotation values are exported to annotation_values.json. These ratios '
        'and differences do not establish causal acceleration.\n\n'
        'Y-axis ranges differ by model. Shared GPU load, placement, and actual output lengths '
        'vary. All observations are retained. No model inference or rerun is performed. '
        'The two CSV files contain all plotted numeric data.\n', encoding='utf-8')
    print(json.dumps({'plotted_requests': len(exported), 'throughput_batches': len(batches),
        'font': times_font,
        'output': str(out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
