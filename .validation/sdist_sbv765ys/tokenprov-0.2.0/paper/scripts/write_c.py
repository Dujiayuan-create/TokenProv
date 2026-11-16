"""Write the C-section manuscript from retained results; run no experiments."""
import csv
import hashlib
import json
from pathlib import Path
import re
import statistics

from paper_paths import DATA, PAPER, output_dir, read_csv
HERE = DATA
ROOT = PAPER
OUT = output_dir('c')
SOURCE = DATA / 'c'
SIGNING = DATA / 'c/signing'
VBS = SIGNING / 'summary.json'

def read_csv(path):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def summary(values):
    return statistics.fmean(values), statistics.stdev(values)


def pm(mean, sd, digits=2):
    return f'{mean:.{digits}f} $\\pm$ {sd:.{digits}f}'


def measured(rows, key, digits=2):
    return pm(*summary([float(row[key]) for row in rows]), digits=digits)


def tabular(caption, label, spec, header, body, wide=False):
    env = 'table*' if wide else 'table'
    width = r'\textwidth' if wide else r'\columnwidth'
    return '\n'.join([
        r'\begin{' + env + r'}[!t]', r'\centering', r'\caption{' + caption + '}',
        r'\label{' + label + '}', r'\footnotesize', r'\renewcommand{\arraystretch}{1.08}',
        r'\begin{tabular*}{' + width + r'}{@{\hspace{3pt}}' + spec + r'@{\hspace{3pt}}}',
        r'\toprule', header + r' \\', r'\midrule', *body,
        r'\bottomrule', r'\end{tabular*}', r'\end{' + env + '}', ''])


def build():
    rows = read_csv(SOURCE / 'round_metrics.csv')
    phases = read_csv(OUT / 'c2_phase_plot_values.csv')
    rates = read_csv(OUT / 'c3_audit_rate_plot_values.csv')
    source_check = json.loads((SOURCE / 'verification.json').read_text(encoding='utf-8'))
    signing_check = json.loads((SIGNING / 'verification.json').read_text(encoding='utf-8'))
    assert source_check['source_files_unchanged'] and source_check['failed_proofs'] == 0
    assert source_check['successful_proofs'] == 5064 + 18 + 3
    assert source_check['negative_controls_rejected'] == 12
    assert signing_check['passed'] and signing_check['measured_signatures'] == 6144
    warm = [row for row in rows if row['experiment'] == 'C1' and row['mode'] == 'persistent-warm']
    legacy = [row for row in rows if row['experiment'] == 'C1' and row['mode'] == 'legacy-command']
    assert len(warm) == len(legacy) == 3

    c1body = [r'\rowcolor{gray!12}', r'\multicolumn{2}{l}{\textbf{Persistent workers}} \\']
    c1items = [('Host witness/link checks', 'host_check_ms'),
        ('Circuit input preparation', 'circuit_input_ms'),
        ('Circuit witness calculation', 'circuit_witness_ms'),
        ('Groth16 proving', 'groth16_prove_ms'),
        ('Witness calculation + proving', 'full_prove_ms'),
        ('Proof verification', 'verify_ms'), ('Complete warm RPC', 'rpc_wall_ms')]
    c1body.extend(label + ' & ' + measured(warm, key, 3) + r' \\' for label, key in c1items)
    c1body.extend([r'\midrule', r'\rowcolor{gray!12}',
        r'\multicolumn{2}{l}{\textbf{Original process-per-call adapter}} \\',
        'Complete prover command & ' + measured(legacy, 'legacy_prover_command_ms', 3) + r' \\',
        'Complete verifier command & ' + measured(legacy, 'legacy_verifier_command_ms', 3) + r' \\'])
    c1 = tabular('Scoped proof costs on the B300 server CPU '
        '(ms/call; mean $\\pm$ sample SD across three rounds).', 'tab:c1-assurance-cost',
        r'l@{\extracolsep{\fill}}c', r'\textbf{Component} & \textbf{Cost}', c1body)

    c2body = []
    for phase in phases:
        selected = [row for row in rows if row['experiment'] == 'C2'
            and row['mode'] == phase['mode'] and float(row['calls']) == float(phase['calls'])]
        assert len(selected) == 3
        if phase['calls'] == '8':
            c2body.append(r'\rowcolor{gray!12}')
        values = [phase['calls'], phase['mode'],
            pm(float(phase['foreground_ms']), float(phase['foreground_ms_sd'])),
            measured(selected, 'online_full_prove_ms'), measured(selected, 'audit_full_prove_ms'),
            pm(float(phase['audit_wall_ms']), float(phase['audit_wall_ms_sd'])),
            pm(float(phase['total_ms']), float(phase['total_ms_sd']))]
        c2body.append(' & '.join(values) + r' \\')
    c2 = tabular('Immediate and deferred proving on retained request witnesses '
        '(ms/request; mean $\\pm$ sample SD across three rounds). '
        'Online and audit proving are components of their respective enclosing phases.',
        'tab:c2-deferred-cost', r'c@{\extracolsep{\fill}}lccccc',
        r'\shortstack{\textbf{Calls/}\\\textbf{request}} & \textbf{Mode} & '
        r'\textbf{Foreground} & \shortstack{\textbf{Online}\\\textbf{proving}} & '
        r'\shortstack{\textbf{Audit}\\\textbf{proving}} & '
        r'\shortstack{\textbf{Audit}\\\textbf{time}} & \textbf{Total}', c2body, wide=True)

    c3body = []
    for rate in rates:
        if rate['audit_rate_percent'] == '10':
            c3body.append(r'\rowcolor{gray!12}')
        values = [rate['audit_rate_percent'], rate['proofs_per_batch'],
            pm(float(rate['amortized_full_prove_ms_per_request']),
                float(rate['amortized_full_prove_ms_per_request_sd'])),
            pm(float(rate['verification_ms_per_request']), float(rate['verification_ms_per_request_sd'])),
            pm(float(rate['amortized_prove_verify_ms_per_request']),
                float(rate['amortized_prove_verify_ms_per_request_sd'])),
            pm(float(rate['audit_makespan_ms']) / 1000, float(rate['audit_makespan_ms_sd']) / 1000)]
        c3body.append(' & '.join(values) + r' \\')
    c3 = tabular('Audit-rate measurements with 100 requests per batch, eight calls per request, '
        'and two proof workers (mean $\\pm$ sample SD across three rounds). '
        'The 10\\% default is shaded.', 'tab:c3-audit-rate', r'c@{\extracolsep{\fill}}ccccc',
        r'\shortstack{\textbf{Audit rate}\\\textbf{(\%)}} & '
        r'\shortstack{\textbf{Proofs/}\\\textbf{batch}} & '
        r'\shortstack{\textbf{Proving cost}\\\textbf{(ms/request)}} & '
        r'\shortstack{\textbf{Verification cost}\\\textbf{(ms/request)}} & '
        r'\shortstack{\textbf{Combined cost}\\\textbf{(ms/request)}} & '
        r'\shortstack{\textbf{Audit time}\\\textbf{(s/batch)}}', c3body, wide=True)

    c4body = []
    for metric in signing_check['metrics']:
        label = {'Signing API': 'Signing API', 'Receipt authentication': 'Hashing + signing',
            'External verification': 'External verification', 'Session key setup': 'Session key setup'}[metric['metric']]
        values = [label, metric['unit']]
        for key in ('software', 'vbs', 'paired_increment'):
            values.append(pm(metric[key]['mean'], metric[key]['sample_sd'], 3))
        c4body.append(' & '.join(values) + r' \\')
    c4 = tabular('Local receipt-signing comparison using the same ECDSA-P256 API '
        '(mean $\\pm$ sample SD across three paired sessions). '
        'Paired changes are TEE-based minus software-key costs within each session.',
        'tab:c4-tee-signing', r'l@{\extracolsep{\fill}}cccc',
        r'\textbf{Operation} & \textbf{Unit} & \textbf{Software key} & '
        r'\textbf{TEE-based key} & \textbf{Paired change}', c4body, wide=True)

    main = r'''% Required packages: graphicx, amsmath, booktabs, and xcolor with the table option.
\subsection{Provider Assurance Overhead}
\label{sec:evaluation-assurance}

We evaluate scoped proof-based assurance and the isolated signing component
of the TEE-based path. Proof measurements replay retained witnesses from
128 Qwen3-1.7B requests comprising 1,024 calls on the B300 server CPU;
model inference is excluded. The circuit enforces the scoped accounting
commitment, integer token charges, and scoped request-hash equality, while
receipt-bound witness validation and shared-field linkage checks execute
on the host. Values are means and sample standard deviations across three
rounds or paired signing sessions. Appendix~\ref{app:c-assurance} provides
the measurement boundaries and complete results.

\noindent\textbf{Scoped proof costs.}
Table~\ref{tab:c-assurance-components} summarizes the component costs.
With persistent CPU workers, circuit witness calculation and proving take
148.45 $\pm$ 7.64\,ms/call, and verification takes
15.10 $\pm$ 1.55\,ms/call. The serialized JSON proof occupies
722.98 $\pm$ 0.47\,B/call. These measurements quantify the scoped
assurance operation used by the prototype. The original process-per-call
adapter and the persistent implementation are reported separately in
Table~\ref{tab:c1-assurance-cost}.

@MAIN_TABLE@

\noindent\textbf{Immediate versus deferred proving.}
Figure~\ref{fig:c-deferred-audit}(a) varies the number of calls per request
from one to eight. Immediate generates proofs in the foreground and verifies
them during the subsequent audit; Deferred performs both operations during
audit. Both modes revalidate and prepare witnesses and persist a fresh copy
before the foreground phase ends. At eight calls, Deferred reduces foreground
protocol time from 1,200.07 $\pm$ 60.99 to
8.65 $\pm$ 2.48\,ms/request, a within-round reduction of
99.28\% $\pm$ 0.19 percentage points.
The subsequent Deferred audit takes 1,332.92 $\pm$ 39.68\,ms/request.
Total replay processing is @IMMEDIATE_TOTAL@\,ms/request for Immediate
and @DEFERRED_TOTAL@\,ms/request for Deferred: deferral moves proof work
off the foreground path while retaining its eventual processing cost.
These phase timings measure protocol replay, excluding LLM inference.

\begin{figure}[!t]
  \centering
  \includegraphics[width=\columnwidth]{c_deferred_proving_audit_rate.pdf}
  \caption{Proof scheduling and audit-rate scaling on retained serving
  witnesses. (a) Foreground protocol cost (upper) and subsequent audit time
  (lower); the inset enlarges Deferred foreground cost. The shared mode
  legend applies to both plots. (b) Proving and verification costs amortized
  over all 100 batch requests (upper), and elapsed audit time with two
  workers (lower), using eight calls per request. Error bars show sample SD
  across three rounds. Gray dots show round-level audit times with small
  horizontal offsets for visibility.}
  \label{fig:c-deferred-audit}
\end{figure}

\noindent\textbf{Audit-rate scaling.}
Figure~\ref{fig:c-deferred-audit}(b) varies the audited fraction from
1\% to 100\%. Each batch contains 100 requests, and auditing a request
checks all eight calls. Combined proving and verification cost increases
from 12.57 $\pm$ 0.47 to 1,248.94 $\pm$ 41.00\,ms/request
as the number of proofs grows from eight to 800.
At the default 10\% audit rate, 80 proofs incur an amortized cost of
123.53 $\pm$ 1.42\,ms/request, and two CPU workers complete the audit
batch in 6.27 $\pm$ 0.08\,s. The upper plot sums individual operation
wall times and divides by all batch requests; the lower plot measures actual
elapsed completion time. Proof generation remains in the audit phase at
every rate. Table~\ref{tab:c3-audit-rate} reports the complete scan.

\noindent\textbf{TEE-based receipt signing.}
We separately compare local TEE-based isolated-key signing with a
software-key control using the same ECDSA-P256 API. Each mode signs
1,024 retained receipts in each of three paired sessions.
TEE-based signing takes 0.763 $\pm$ 0.085\,ms/receipt, versus
0.534 $\pm$ 0.028\,ms/receipt for the software-key control.
The paired increment is 0.229 $\pm$ 0.108\,ms/receipt.
This experiment measures protected private-key operations;
receipt preparation and accounting remain on the host.
The 6,144 measured signatures verify successfully.
Table~\ref{tab:c4-tee-signing} includes hashing, verification, and session
key setup. Full inference attestation and remote attestation are outside
this component measurement.
'''
    eight = {row['mode']: row for row in phases if row['calls'] == '8'}
    main = main.replace('@MAIN_TABLE@', (OUT / 'c_assurance_components_table.tex').read_text(encoding='utf-8').strip())
    for mode, tag in [('Immediate', '@IMMEDIATE_TOTAL@'), ('Deferred', '@DEFERRED_TOTAL@')]:
        main = main.replace(tag, f"{float(eight[mode]['total_ms']):,.2f} $\\pm$ "
            f"{float(eight[mode]['total_ms_sd']):.2f}")

    appendix = r'''% Insert after the paper's existing \appendices command.
\section{Additional Provider-Assurance Measurements}
\label{app:c-assurance}

\noindent\textbf{Inputs, platform, and statistical method.}
Proof experiments use retained witnesses from the original 128 Qwen3-1.7B
requests and 1,024 calls. Before replay, we validate all 128 source-bundle
hashes and 1,024 per-call witness commitments and check that source files
remain unchanged. Measurements run on the B300 server's Intel Xeon 6747P
CPU using Node.js 22.14.0 and snarkjs 0.7.5. No GPU is allocated.
We use two persistent proof processes, with at most two curve worker
threads per process, and three rounds with seeds 42--44.
Each statistic is first computed within a round; tables then report the
arithmetic mean and sample standard deviation of three round-level values.
Warmups and preflight measurements are excluded, and all formal observations
are retained. These are replay measurements with no new model inference.

\noindent\textbf{Proof statement and execution boundaries.}
The Groth16 circuit checks a Poseidon accounting commitment, integer token
charges, and a scoped request-hash equality. Receipt-bound SHA-256 witness
validation and shared-field linkage checks execute on the host.
The measured circuit does not prove full LLM inference or the complete
host-side validation procedure.
Table~\ref{tab:c1-assurance-cost} separates circuit input preparation,
witness calculation, proving, verification, and complete warm RPC time.
Each round measures 32 warm calls and eight original process-per-call
commands. C2 and C3 use the persistent benchmark adapter; the original
deployed adapter is measured independently in C1. The persistent adapter
also verifies proofs produced by the original adapter. Original command
timings include process startup and command-level overhead, so the rows
describe implementation costs at their stated boundaries.

@C1_TABLE@

\noindent\textbf{Proof scheduling.}
For each call count and mode, each round replays four source-request groups,
using the first one, two, four, or eight calls of each request.
Both modes revalidate and prepare inputs, then serialize and synchronize
a fresh witness copy before ending the foreground timer.
Immediate generates proofs in the foreground and verifies them during audit;
Deferred generates and verifies proofs during audit.
Table~\ref{tab:c2-deferred-cost} reports foreground time, its proving
component, audit proving, elapsed audit time, and total processing.
For each round, total time is the sum of foreground and subsequent audit
time; the total's SD is calculated from these round-level sums.
The 99.28\% foreground reduction is calculated within each paired round
before aggregation. These measurements do not include LLM service time
or characterize interference with concurrent inference.

@C2_TABLE@

\noindent\textbf{Audit-rate scan.}
Each round uses 100 retained requests and eight calls per request. Audit
rates of 1\%, 5\%, 10\%, 25\%, 50\%, and 100\% select exactly
one, five, ten, 25, 50, and 100 requests, producing eight, 40, 80, 200,
400, and 800 proofs. Rate order is randomized, and nested challenged sets
are reproducible. Each rate executes its own proofs.
For rate $\alpha$, let $\mathcal{J}_{r,\alpha}$ contain the challenged
calls in round $r$. The amortized combined cost is
\[
 C_r(\alpha)=\frac{1}{100}
 \sum_{j\in\mathcal{J}_{r,\alpha}}
 \bigl(t^{\mathrm{witness+prove}}_{r,j}
       +t^{\mathrm{verify}}_{r,j}\bigr).
\]
This sum uses individual operation wall durations; it is not measured CPU
time. Elapsed audit time is the actual time to drain the two-worker batch,
including result I/O. Verification-component SDs are computed directly
from their three round-level observations.
The separate preparation baseline writes one retained 100-request file
and performs one synchronization per round; its persistence timing is a
cohort-level observation. Deferred proof generation occurs after the
foreground phase, while preparation and persistence remain nonzero.
Table~\ref{tab:c3-audit-rate} reports all six configurations.

@C3_TABLE@

\noindent\textbf{TEE-based signing control.}
Table~\ref{tab:c4-tee-signing} compares software-key and TEE-based isolated-key
ECDSA-P256 signing through the same API. Each mode processes 1,024 receipts
per session in three paired sessions, giving 3,072 measurements per mode.
The private-key isolation property is checked in each session; private
key operations are isolated in the TEE-based mode. Accounting and receipt
hashing execute on the host, and public-key verification is external.
The hashing-plus-signing row includes signing and is not added to the
signing-only row. Key setup is measured per session and excluded from
per-receipt signing time. Paired-change SDs use the three within-session
differences, rather than differences of marginal SDs.

@C4_TABLE@

\noindent\textbf{Validation and scope controls.}
All 5,064 formal proofs verify. Eighteen warmup proofs and three additional
honest-control proofs are recorded separately, and all 12 negative proof
controls are rejected. For receipt signing, all 6,144 measured signatures
verify. Across both signing modes, 48 receipt/signature tampering cases
are rejected and six private-key export attempts are denied. Six additional
scope controls modify a charge on the host and request a fresh signature;
the resulting signatures remain verifiable, as expected. These controls
confirm that isolated signing authenticates the signed bytes while
accounting enforcement remains a host-side responsibility in this component
experiment. The measurements cover scoped proofs and isolated receipt
signing; full inference attestation and remote attestation are not measured.
'''
    for tag, table in [('@C1_TABLE@', c1), ('@C2_TABLE@', c2), ('@C3_TABLE@', c3), ('@C4_TABLE@', c4)]:
        appendix = appendix.replace(tag, table.strip())
    combined = main + '\n\n' + appendix
    assert not re.findall(r'@[A-Z_]+@', combined)
    labels = re.findall(r'\\label\{([^}]+)\}', combined)
    refs = re.findall(r'\\(?:eq)?ref\{([^}]+)\}', combined)
    assert len(labels) == len(set(labels))
    assert not set(refs) - set(labels), sorted(set(refs) - set(labels))
    stack = []
    for action, env in re.findall(r'\\(begin|end)\{([^}]+)\}', combined):
        if action == 'begin':
            stack.append(env)
        else:
            assert stack and stack.pop() == env
    assert not stack
    assert len(re.findall(r'(?<!\\)\$', combined)) % 2 == 0
    assert (OUT / 'c_deferred_proving_audit_rate.pdf').is_file()
    assert 'VBS' not in combined and 'vbs' not in combined
    for path, text in [('c_maintext.tex', main), ('c_appendix.tex', appendix),
            ('c_complete.tex', combined), ('c1_appendix_cost_table.tex', c1),
            ('c2_appendix_phase_table.tex', c2), ('c3_appendix_audit_rate_table.tex', c3),
            ('c4_appendix_signing_table.tex', c4)]:
        (OUT / path).write_text(text.strip() + '\n', encoding='utf-8')
    validation = {'labels': labels, 'unresolved_references': [], 'balanced_environments': True,
        'latex_compiled': False, 'three_round_statistics': True,
        'main_table_reused': 'c_assurance_components_table.tex',
        'figure_reused': 'c_deferred_proving_audit_rate.pdf',
        'formal_proofs': 5064, 'warmup_proofs': 18, 'honest_controls': 3,
        'negative_controls_rejected': 12, 'measured_signatures': 6144,
        'all_observations_retained': True, 'new_experiments_run': False,
        'source_sha256': {(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else 'generated/c/' + path.name): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [SOURCE / 'round_metrics.csv', SOURCE / 'verification.json',
                SIGNING / 'verification.json', OUT / 'c_assurance_components_table.tex',
                OUT / 'c_deferred_proving_audit_rate.pdf']}}
    (OUT / 'c_manuscript_validation.json').write_text(json.dumps(validation, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(OUT), 'labels': labels, 'main_words': len(main.split()),
        'appendix_words': len(appendix.split()), 'validation': 'passed'}, ensure_ascii=True))


if __name__ == '__main__':
    build()
