"""Populate the final single-column E table from regenerated measurements."""
import re
from paper_paths import PAPER, output_dir, read_csv


def main():
    out=output_dir('e')
    text=(PAPER/'published/e/e_combined_results_table.tex').read_text(encoding='utf-8')
    labels={'SignedLog':'SignedLog','SignedLogPlus':'SignedLog+',
            'TokenProv-Full':r'\textbf{\textsc{TokenProv}}'}
    for row in read_csv(out/'e_current_security_summary.csv'):
        label=labels[row['system']]
        line=(f"{label} & {row['successful_episodes']}/{row['episodes']} & "
              f"{int(row['attack_queries']):,} & {row['benign_false_rejections']}/{row['benign_trials']} "+r'\\')
        text,count=re.subn(r'^'+re.escape(label)+r' & .*$',lambda m:line,text,count=1,flags=re.M)
        assert count==1
    scenarios={'S1':'Same model and deployment','S2':'Same model, different deployment',
               'S3':'Different model and deployment'}
    splicing=read_csv(out/'e_historical_splicing_summary.csv')
    for scenario,label in scenarios.items():
        a=next(r for r in splicing if r['scenario']==scenario and r['variant']=='IndependentLocalChecks')
        b=next(r for r in splicing if r['scenario']==scenario and r['variant']=='TokenProv-Full')
        line=f"{label} & {a['accepted']}/{a['trials']} & {b['accepted']}/{b['trials']} "+r'\\'
        text,count=re.subn(r'^'+re.escape(label)+r' & .*$',lambda m:line,text,count=1,flags=re.M)
        assert count==1
    for row in read_csv(out/'e_historical_localization_summary.csv'):
        relation='$R_{\\mathrm{'+row['first_failed_relation'][2:]+'}}$'
        interval=f"${row['responsibility_interval_start']}$--${row['responsibility_interval_end']}$ ({float(row['mean_interval_width']):.0f})"
        line=(f"{row['stage_name']} & {relation} & {row['first_relation_matches']}/{row['trials']} & "
              f"{interval} & {float(row['mean_audit_latency_ms']):.3f} "+r'\\')
        text,count=re.subn(r'^'+re.escape(row['stage_name'])+r' & .*$',lambda m:line,text,count=1,flags=re.M)
        assert count==1
    (out/'e_combined_results_table.tex').write_text(text,encoding='utf-8')
    body=(PAPER/'published/e/e_section_body.tex').read_text(encoding='utf-8')
    (out/'e_section_full.tex').write_text(body.replace(r'\input{e_combined_results_table.tex}',text),encoding='utf-8')
    appendix=(PAPER/'published/e/e_appendix_for_paper_body.tex').read_text(encoding='utf-8')
    expanded=re.sub(r'\\input\{([^}]+)\}',lambda m:(out/m[1]).read_text(encoding='utf-8'),appendix)
    (out/'e_appendix_full.tex').write_text(expanded,encoding='utf-8')
    (out/'e_appendix_for_paper_full.tex').write_text(expanded,encoding='utf-8')


if __name__=='__main__':main()
