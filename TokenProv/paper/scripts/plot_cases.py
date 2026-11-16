"""Render recorded qualitative cases; no serving or new experiment execution."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

from paper_paths import DATA, PAPER, output_dir, read_csv
HERE = DATA
ROOT = PAPER
OUT = output_dir('cases')


SPECS = [
    ('fact_qa', 'Fact QA',
     'What is the capital of France?\nReturn only the city name.', [0, 1]),
    ('translation', 'Translation',
     'Translate into English:\nEach model call generates a\nverifiable token billing receipt.', [5, 6, 11]),
    ('conversation_history', 'With conversation history',
     'History: three models, A/B/C.\nEach model: 3 rounds, 128 requests.\nReturn the calculation and result.', [1, 8, 12]),
]


def read_cases():
    # Display extracts preserve receipt/settlement fields and recorded audit status.
    # The full private source archive is not bundled; no cryptographic re-audit is claimed.
    cases = json.loads((DATA / 'cases/case_figure_data.json').read_text(encoding='utf-8'))
    assert len(cases)==3
    for case in cases:
        receipt,settlement=case['receipt'],case['settlement']
        assert case['recorded_offline_audit_accepted']
        assert len(case['prompt_token_ids'])==case['input_tokens']
        assert receipt['usage']['n_pf']==case['input_tokens']
        assert receipt['usage']['n_out']==case['output_tokens']
        assert receipt['charge_microunits']==case['input_tokens']+2*case['output_tokens']
        assert settlement['bill_microunits']==receipt['charge_microunits']
        assert all(0<=t['position']<case['output_tokens'] for t in case['selected_output_tokens'])
    return cases



def render(cases):
    import re
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import Rectangle, FancyArrowPatch

    fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('times.ttf', 'timesbd.ttf', 'timesi.ttf', 'msyh.ttc'):
        if (fonts / name).is_file():
            font_manager.fontManager.addfont(str(fonts / name))
    latin_font = font_manager.findfont('Times New Roman', fallback_to_default=False)
    cjk_font = font_manager.findfont('Microsoft YaHei', fallback_to_default=False)
    plt.rcParams.update({'font.family': ['Times New Roman', 'Microsoft YaHei'],
        'font.size': 6.6, 'text.color': '#22252D',
        'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none'})
    fig = plt.figure(figsize=(3.5, 5.5), dpi=300)
    renderer = fig.canvas.get_renderer()
    width, margin, gap = 252, 3, 3.5
    column_width = (width - 2 * margin - 2 * gap) / 3
    padding, header_h, layer_gap = 4, 18, 5
    usable = column_width - 2 * padding
    ellipsis, times, mu, arrow = chr(0x2026), chr(0xd7), chr(0x3bc), chr(0x2192)
    ink, secondary, accent = '#22252D', '#59616C', '#2E3B97'

    def text_width(text, size, bold=False):
        prop = FontProperties(family=['Times New Roman', 'Microsoft YaHei'],
            size=size, weight='bold' if bold else 'normal')
        return renderer.get_text_width_height_descent(text, prop, False)[0] * 72 / fig.dpi

    def wrap(text, max_width, size=6.6, bold=False):
        result = []
        for original in text.splitlines():
            line = ''
            # Preserve Latin words while allowing Chinese characters to wrap.
            units = re.findall(r"[A-Za-z0-9][A-Za-z0-9'_/-]*|\s+|.", original)
            for unit in units:
                trial = line + unit
                if text_width(trial, size, bold) > max_width and line.strip():
                    result.append(line.rstrip())
                    line = unit.lstrip()
                else:
                    line = trial
            if line.strip():
                result.append(line.rstrip())
        return result

    prepared = []
    for case in cases:
        if case['kind'] == 'translation':
            original = case['original_messages'][-1]['content']
            chinese_source = original.split(chr(0xff1a), 1)[1]
            prompt = wrap('Translate into English:', usable) + wrap(chinese_source, usable)
        else:
            prompt = wrap(' '.join(case['prompt_translated_excerpt'].splitlines()), usable)
        response = wrap(case['response'], usable - 6, 6.6)
        receipt = case['receipt']
        details = [
            ('Auth', receipt['authorization_digest'][:8]),
            ('H/B', receipt['hb_handoff_hash'][:8]),
            ('B/P', receipt['bp_handoff_hash'][:8]),
            ('Receipt', case['settlement']['receipt_hashes'][0][:8]),
            ('Input', receipt['input_hash'][:8]),
            ('Output', receipt['output_hash'][:8]),
        ]
        settlement = [
            ('{}+2{}{}={} {}u'.format(case['input_tokens'], times, case['output_tokens'],
                case['settlement']['bill_microunits'], mu), 7.5, True, accent),
            ('Evidence root: ' + case['settlement']['evidence_root'][:8] + ellipsis, 6.3, False, ink),
            ('Bill consistency: MATCH', 6.3, False, ink),
            ('Protocol audit: PASS', 6.3, True, accent),
        ]
        settlement_lines = [(wrap(text, usable, size, bold), size, bold, color)
            for text, size, bold, color in settlement]
        assert all(len(lines) == 1 for lines, _, _, _ in settlement_lines[-2:])
        prepared.append({'prompt': prompt, 'response': response, 'details': details,
            'response_h': len(response) * 7.7 + 4, 'settlement': settlement_lines})
    # Size each inset to its own response and reserve only the tallest combined
    # prompt/response stack, rather than separate maxima from different cases.
    request_h = max(header_h + 17 + (len(c['prompt']) - 1) * 7.7
        + 5 + c['response_h'] + 4 for c in prepared)
    table_h = 12 + max(len(c['selected_output_tokens']) for c in cases) * 9 + 4
    tokens_h = header_h + 36 + table_h + 4
    evidence_h = header_h + 10 + 5 + 6 * 8 + 10 + 4
    settlement_body_h = max(sum(len(lines) * 7.5 + 3 for lines, _, _, _ in c['settlement'])
        for c in prepared) + 6
    settlement_h = header_h + settlement_body_h
    heights = [request_h, tokens_h, evidence_h, settlement_h]
    height = sum(heights) + 3 * layer_gap + 2 * margin
    figsize = (3.5, height / 72)
    fig.set_size_inches(*figsize)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis('off')
    labels = []
    response_texts = []
    body_fills = ['#FFF1E5', '#EDF5FA', '#F1F0F8', '#FBECE9']
    header_fills = ['#FDD6AF', '#B9D6E9', '#D7D5E8', '#E2ABA2']

    def put(text, x, y, bounds, size=6.6, bold=False, color=ink, align='left', center=False):
        artist = ax.text(x, y, text, fontsize=size, ha=align,
            va='center' if center else 'baseline', fontweight='bold' if bold else 'normal',
            color=color, linespacing=1.03)
        labels.append((artist, bounds))
        return artist

    def card(x, y, h, title, row):
        bounds = (x, y, x + column_width, y + h)
        ax.add_patch(Rectangle((x, y), column_width, h,
            facecolor=body_fills[row], edgecolor='#515864', linewidth=.55))
        ax.add_patch(Rectangle((x, y + h - header_h), column_width, header_h,
            facecolor=header_fills[row], edgecolor='#515864', linewidth=.4))
        put(title, x + column_width / 2, y + h - header_h / 2, bounds,
            size=7.0, bold=True, align='center', center=True)
        return bounds

    for index, (case, content) in enumerate(zip(cases, prepared)):
        x = margin + index * (column_width + gap)
        top = height - margin
        rows = []
        for h in heights:
            rows.append((top - h, h))
            top -= h + layer_gap
        y, h = rows[0]
        title = 'With conversation\nhistory' if case['kind'] == 'conversation_history' else case['title']
        b = card(x, y, h, title, 0)
        put('Prompt:', x + padding, y + h - header_h - 8, b, size=6.4, bold=True)
        for row, line in enumerate(content['prompt']):
            put(line, x + padding, y + h - header_h - 17 - row * 7.7, b)
        response_h = content['response_h']
        rb = (x + padding, y + 4, x + column_width - padding, y + 4 + response_h)
        ax.add_patch(Rectangle((rb[0], rb[1]), usable, response_h, facecolor='white',
            edgecolor='#BBBFC9', linewidth=.35))
        ax.add_patch(Rectangle((rb[0], rb[1]), 1.1, response_h, facecolor='#666095', edgecolor='none'))
        # Preserve each observed response, centered vertically within its inset.
        first_baseline = rb[1] + response_h / 2 + (len(content['response']) - 1) * 7.7 / 2 - 2
        for row, line in enumerate(content['response']):
            response_texts.append(put(line, rb[0] + 3, first_baseline - row * 7.7,
                rb, size=6.6, color=accent))

        y, h = rows[1]
        b = card(x, y, h, 'Runtime token trace', 1)
        put('Input: {} tokens'.format(case['input_tokens']), x + padding,
            y + h - header_h - 10, b, size=6.9)
        ids = ','.join(str(t) for t in case['prompt_token_ids'][:3])
        put('IDs: ' + ids + ',' + ellipsis, x + padding,
            y + h - header_h - 20, b, size=6.0, color=secondary)
        put('Output: {} tokens'.format(case['output_tokens']), x + padding,
            y + h - header_h - 31, b, size=6.9)
        table_bottom = y + 4
        tb = (x + 3, table_bottom, x + column_width - 3, table_bottom + table_h)
        ax.add_patch(Rectangle((tb[0], tb[1]), tb[2] - tb[0], table_h,
            facecolor='white', edgecolor='#A9B5C1', linewidth=.35))
        header_y = tb[3] - 8
        for text, offset in [('Pos.', 5), ('Token ID', 18), ('Decoded', 44)]:
            put(text, x + offset, header_y, tb, size=5.9, bold=True)
        ax.hlines(header_y - 3, tb[0] + 2, tb[2] - 2, color='#C0C8D0', linewidth=.3)
        for row, token in enumerate(case['selected_output_tokens']):
            yy = header_y - 12 - row * 9
            piece = token['decoded_piece']
            piece_text = piece if piece == '<|im_end|>' else json.dumps(piece, ensure_ascii=False)
            put(str(token['position']), x + 5, yy, tb, size=6.2)
            put(str(token['token_id']), x + 18, yy, tb, size=6.2, color=accent)
            put(piece_text, x + 44, yy, tb, size=5.9 if piece == '<|im_end|>' else 6.2)

        y, h = rows[2]
        b = card(x, y, h, 'Signed evidence\nU ' + arrow + ' H ' + arrow + ' B ' + arrow + ' P', 2)
        put('RID ' + ellipsis + case['request_id'].rsplit('-', 1)[-1] + ' | ' + case['call_id'],
            x + padding, y + h - header_h - 10, b, size=5.8, color=secondary)
        yy = y + h - header_h - 22
        for label, digest in content['details']:
            put(label, x + padding, yy, b, size=6.2, bold=True, color='#424956')
            put(digest, x + 29, yy, b, size=6.2, color=secondary)
            yy -= 8
        usage = 'Signed usage: {} in / {} out'.format(case['input_tokens'], case['output_tokens'])
        put(usage, x + padding, y + 6, b, size=6.0)

        y, h = rows[3]
        b = card(x, y, h, 'Settlement and\noffline audit', 3)
        yy = y + h - header_h - 10
        for lines, size, bold, color in content['settlement']:
            for line in lines:
                put(line, x + padding, yy, b, size=size, bold=bold, color=color)
                yy -= 7.5
            yy -= 3
        for (upper_y, _), (lower_y, lower_h) in zip(rows, rows[1:]):
            cx = x + column_width / 2
            ax.add_patch(FancyArrowPatch((cx, upper_y - .25), (cx, lower_y + lower_h + .25),
                arrowstyle='-|>', mutation_scale=4.5, linewidth=.5, color='#515A65'))

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    outside, overlaps = [], []
    for artist, (x0, y0, x1, y1) in labels:
        box = artist.get_window_extent(renderer).transformed(ax.transData.inverted())
        if box.x0 < x0 + .4 or box.x1 > x1 - .4 or box.y0 < y0 + .4 or box.y1 > y1 - .4:
            outside.append(artist.get_text())
    for i, (first, _) in enumerate(labels):
        for second, _ in labels[i + 1:]:
            if first.get_window_extent(renderer).overlaps(second.get_window_extent(renderer)):
                overlaps.append([first.get_text(), second.get_text()])
    assert not outside, outside
    assert not overlaps, overlaps
    for ext in ('pdf', 'svg', 'png'):
        fig.savefig(OUT / ('token_provenance_case_boxes.' + ext), dpi=1200 if ext == 'png' else 300)
    plt.close(fig)
    pixels = struct.unpack('>II', (OUT / 'token_provenance_case_boxes.png').read_bytes()[16:24])
    return {'figure_size_inches': list(figsize), 'png_dpi': 1200, 'png_pixels': list(pixels),
        'latin_font': latin_font, 'cjk_glyph_font': cjk_font, 'labels_outside_boxes': outside,
        'layout': 'single-column width; three parallel requests and four boxed evidence layers',
        'row_heights_pt': heights, 'translation_source_is_original_chinese': True,
        'response_insets': True, 'response_inset_heights_pt': [c['response_h'] for c in prepared],
        'settlement_statuses_single_line': True,
        'token_tables_framed': True, 'text_overlaps': overlaps,
        'input_output_explanatory_suffixes': False, 'settlement_fields_preserved': True,
        'bundled_display_extract_used': True, 'original_archive_not_reaudited': True,
        'displayed_prompt_count_matches': True,
        'bills_equal_receipt_sum': True, 'all_recorded_offline_audits_accepted': True,
        'no_new_model_execution': True, 'no_individual_token_signature_claim': True}





def main():
    OUT.mkdir(exist_ok=True)
    cases = read_cases()
    (OUT / 'case_figure_data.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    result = render(cases)
    (OUT / 'verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    integration = r'''% Place in the appendix. The translation source sentence is original Chinese.
\begin{figure}[!t]
  \centering
  \includegraphics[width=\columnwidth]{token_provenance_case_boxes.pdf}
  \caption{Recorded token-billing provenance examples using Qwen3-14B.
  Each column follows a separate request through four layers: request/response,
  runtime token export, signed evidence, and settlement. The translation column
  reproduces the original Chinese source sentence; other prompts are English
  excerpts of the original messages. White insets show the responses verbatim.
  Token counts and IDs refer to the original recorded messages. Input counts
  include the chat template and, where applicable, conversation history; output
  counts include the end-of-message token. Output positions are zero-based and
  only the indicated positions are shown in the framed tables. Quotation marks
  delimit decoded text pieces, including leading spaces; \texttt{<|im\_end|>}
  is the end-of-message token.
  Auth, H/B, B/P, and Receipt identify the authorization, two bilateral handoffs,
  and provider receipt. RID suffixes and eight-hex-digit digest prefixes
  abbreviate full values available in the archived records. Experimental rates
  are one micro-unit per input token and two per output token. Receipts bind
  input/output digests and usage; token IDs are auxiliary runtime exports.
  The examples use host-generated role signatures and were collected separately
  from the performance measurements.}
  \label{fig:appendix-token-provenance-cases}
\end{figure}
'''
    (OUT / 'appendix_case_figure.tex').write_text(integration, encoding='utf-8')
    (OUT / 'README.md').write_text(
        '# Recorded provenance cases\n\n'
        'Three separate Qwen3-14B qualitative requests, drawn from the archived\n'
        'semantic cases. Three separately displayed requests, with no aggregation.\n'
        'Native width: 3.50 inches, for a single-column appendix figure.\n'
        'Layout: three parallel requests across four boxed evidence layers.\n'
        'Responses and token tables have small insets; signed evidence uses\n'
        'aligned labels/digests. Settlement values are retained with concise\n'
        'bill-consistency and protocol-audit labels on single lines.\n'
        'PNG: 1200 dpi. PDF and SVG are vector graphics. Latin text uses Times\n'
        'New Roman; the Chinese response/token glyph uses Microsoft YaHei.\n'
        'The translation source sentence is original Chinese; other prompt\n'
        'excerpts are English translations. Response strings and token IDs\n'
        'are copied from the original records. Stored audit results are reported.\n'
        'The generator checks bundled token counts, selected token positions, receipt\n'
        'usage, charge arithmetic, settlement sums, and text containment.\n'
        'No inference, TEE, or proof generation is executed by this script.\n', encoding='utf-8')
    print(json.dumps({'output': str(OUT), 'cases': len(cases), **result}, ensure_ascii=True))


if __name__ == '__main__':
    main()
