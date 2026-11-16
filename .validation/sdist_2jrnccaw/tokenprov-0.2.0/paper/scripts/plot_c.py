"""Publication figures from completed C1--C3/TEE signing results; no experiments run."""
import csv
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys

from paper_paths import DATA, PAPER, output_dir, read_csv
HERE = DATA
ROOT = PAPER
OUT = output_dir('c')
SOURCE = DATA / 'c'
SIGNING = DATA / 'c/signing'
VBS = SIGNING / 'summary.json'



def stats(values):
    return statistics.fmean(values),statistics.stdev(values)


def csv_out(path,rows):
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def derive():
    verification=json.loads((SOURCE/'verification.json').read_text())
    assert verification['source_files_unchanged'] and verification['all_configurations_expected_rounds']
    assert verification['failed_proofs']==0
    with (SOURCE/'round_metrics.csv').open(newline='',encoding='utf-8') as f:
        raw=list(csv.DictReader(f))
    rows=[{k:float(v) if k not in ('experiment','mode') and v else v for k,v in r.items()} for r in raw]
    c2=[]
    for calls in (1,2,4,8):
        for mode in ('Immediate','Deferred'):
            selected=[r for r in rows if r['experiment']=='C2' and r['calls']==calls and r['mode']==mode]
            assert len(selected)==3
            result={'calls':calls,'mode':mode}
            for name in ('foreground_ms','audit_wall_ms'):
                result[name],result[name+'_sd']=stats([r[name] for r in selected])
                for r in selected:result[name+'_round'+str(int(r['round']))]=r[name]
            # Correlated stage SDs must not be added; sum within each round first.
            result['total_ms'],result['total_ms_sd']=stats([r['foreground_ms']+r['audit_wall_ms'] for r in selected])
            c2.append(result)
    c3=[]
    for rate in (1,5,10,25,50,100):
        selected=[r for r in rows if r['experiment']=='C3' and r['audit_rate_percent']==rate]
        assert len(selected)==3 and all(r['proof_count']==rate*8 for r in selected)
        result={'audit_rate_percent':rate,'proofs_per_batch':rate*8}
        for name in ('amortized_prove_verify_ms_per_request','amortized_full_prove_ms_per_request','audit_makespan_ms'):
            result[name],result[name+'_sd']=stats([r[name] for r in selected])
            for r in selected:result[name+'_round'+str(int(r['round']))]=r[name]
        result['verification_ms_per_request'],result['verification_ms_per_request_sd']=stats(
            [r['sum_verify_ms']/r['requests'] for r in selected])
        assert abs(result['amortized_prove_verify_ms_per_request']-
            result['amortized_full_prove_ms_per_request']-result['verification_ms_per_request'])<1e-8
        c3.append(result)
    warm=[r for r in rows if r['experiment']=='C1' and r['mode']=='persistent-warm']
    table=[]
    for label,unit,key in [('Circuit witness + proving','ms/call','full_prove_ms'),
            ('Proof verification','ms/call','verify_ms'),('Serialized proof (JSON)','B/call','proof_bytes')]:
        mean,sd=stats([r[key] for r in warm])
        table.append({'group':'Scoped Groth16 (B300 server CPU)','component':label,'unit':unit,
            'mean':mean,'sample_sd':sd,'digits':2})
    vbs=json.loads(VBS.read_text())
    assert vbs['completed_real_vbs_key_signing']
    sessions=vbs['round_statistics']
    session_values={mode:{r['round']:r['sign_api_ns_ms']['mean'] for r in sessions if r['mode']==mode}
        for mode in ('software_ksp','require_vbs')}
    for label,mode in [('Software-key signing','software_ksp'),('TEE-based signing','require_vbs')]:
        assert len(session_values[mode])==3
        mean,sd=stats(list(session_values[mode].values()))
        table.append({'group':'TEE-based receipt signing (local)','component':label,'unit':'ms/receipt',
            'mean':mean,'sample_sd':sd,'digits':3})
    mean,sd=stats([session_values['require_vbs'][r]-session_values['software_ksp'][r] for r in (1,2,3)])
    table.append({'group':'TEE-based receipt signing (local)','component':'Paired signing increment','unit':'ms/receipt',
        'mean':mean,'sample_sd':sd,'digits':3})
    reduction=[]
    for r in (1,2,3):
        pair={v['mode']:v for v in rows if v['experiment']=='C2' and v['calls']==8 and v['round']==r}
        reduction.append(100*(1-pair['Deferred']['foreground_ms']/pair['Immediate']['foreground_ms']))
    csv_out(OUT/'c2_phase_plot_values.csv',c2)
    csv_out(OUT/'c3_audit_rate_plot_values.csv',c3)
    csv_out(OUT/'c_assurance_components.csv',table)
    return c2,c3,table,stats(reduction)


def setup():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    for name in ('times.ttf','timesbd.ttf','timesi.ttf','timesbi.ttf'):
        if (fonts/name).exists():font_manager.fontManager.addfont(str(fonts/name))
    font=font_manager.findfont('Times New Roman',fallback_to_default=False)
    plt.rcParams.update({'font.family':'Times New Roman','font.serif':['Times New Roman'],
        'font.size':7.3,'axes.labelsize':7.4,'xtick.labelsize':7.0,'ytick.labelsize':7.0,
        'axes.linewidth':.65,'axes.edgecolor':'#36383D','axes.labelcolor':'#24262B',
        'text.color':'#24262B','xtick.color':'#35383D','ytick.color':'#35383D',
        'hatch.linewidth':.6,'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none'})
    return plt,font


def save_graphic(fig,stem):
    for ext in ('pdf','svg','png'):
        fig.savefig(OUT/(stem+'.'+ext),dpi=1200 if ext=='png' else 300)


def plot(c2,c3,reduction,plt):
    from matplotlib.patches import Rectangle,Patch
    from matplotlib.lines import Line2D
    from matplotlib.colors import to_rgba_array
    from matplotlib.transforms import ScaledTranslation
    from matplotlib.ticker import FuncFormatter
    fig=plt.figure(figsize=(3.5,2.38))
    online=fig.add_axes([.090,.570,.400,.375])
    audit=fig.add_axes([.090,.180,.400,.274])
    b=fig.add_axes([.590,.570,.402,.375])
    drain=fig.add_axes([.590,.180,.402,.274])
    coral,coral_edge='#D8978E','#8F4F44'
    blue,blue_edge='#96BDD4','#3F6C85'
    indigo='#2E3B97'
    annotations=[];scale_labels=[]
    styles=[('Immediate',coral_edge,coral,'o',''),('Deferred',blue_edge,blue,'s','///')]
    x=list(range(4))
    for mode,edge,fill,marker,hatch in styles:
        values=[v for v in c2 if v['mode']==mode]
        offset=-.16 if mode=='Immediate' else .16
        positions=[v+offset for v in x]
        mean=[v['foreground_ms'] for v in values];sd=[v['foreground_ms_sd'] for v in values]
        online.bar(positions,mean,width=.28,color=fill,edgecolor=edge,linewidth=.45,hatch=hatch,zorder=3)
        online.errorbar(positions,mean,yerr=sd,fmt='none',ecolor=edge,elinewidth=.65,
            capthick=.65,capsize=1.4,zorder=4)
    online.set_ylim(0,1450);online.set_yticks([0,600,1200])
    online.yaxis.set_major_formatter(FuncFormatter(lambda value,position:f'{value/1000:g}'))
    scale_labels.append(online.text(0,1.018,'\u00d710\u00b3',transform=online.transAxes,
        ha='left',va='bottom',fontsize=6.5))
    online.set_ylabel('Online (ms)',labelpad=2.4)
    online.set_xlim(-.5,3.5);online.set_xticks(x,['1','2','4','8'])
    online.set_xlabel('Calls per request',labelpad=1.8)
    # Deferred values are enlarged on a separately labelled linear scale.
    zoom=online.inset_axes([.14,.555,.43,.265])
    deferred=[v for v in c2 if v['mode']=='Deferred']
    dm=[v['foreground_ms'] for v in deferred];ds=[v['foreground_ms_sd'] for v in deferred]
    zoom.bar(x,dm,width=.56,color=blue,edgecolor=blue_edge,linewidth=.4,hatch='///',zorder=3)
    zoom.errorbar(x,dm,yerr=ds,fmt='none',ecolor=blue_edge,capsize=1.0,elinewidth=.5,capthick=.5,zorder=4)
    zoom.set_xlim(-.5,3.5);zoom.set_ylim(0,13)
    zoom.set_xticks(x,['1','2','4','8']);zoom.set_yticks([0,10])
    zoom.tick_params(labelsize=5.9,length=1.6,width=.45,pad=1)
    zoom.set_title('Deferred (ms)',fontsize=6.0,pad=1.4)
    for spine in zoom.spines.values():spine.set_linewidth(.5)
    zoom.grid(axis='y',color='#E0E3E8',linewidth=.3);zoom.set_axisbelow(True)
    ann=online.annotate(f'{reduction[0]:.2f}% lower',xy=(3,dm[-1]),xycoords=zoom.transData,
        xytext=(.787,.94),textcoords=online.transAxes,ha='center',va='center',fontsize=6.1,color=indigo,
        bbox=dict(boxstyle='square,pad=.18',facecolor='white',edgecolor='#AEB4C2',linewidth=.4),
        arrowprops=dict(arrowstyle='->',color=indigo,linewidth=.6,shrinkA=2,shrinkB=3,
            connectionstyle='arc3,rad=-.10'),zorder=8)
    annotations.append(ann)
    # Audit costs use dumbbells: the connector spans the two mode means.
    immediate=[v for v in c2 if v['mode']=='Immediate']
    for i,(im,de) in enumerate(zip(immediate,deferred)):
        audit.hlines(i,im['audit_wall_ms'],de['audit_wall_ms'],color='#BCC2CC',linewidth=1.2,zorder=2)
    for mode,edge,fill,marker,hatch in styles:
        values=immediate if mode=='Immediate' else deferred
        audit.errorbar([v['audit_wall_ms'] for v in values],x,
            xerr=[v['audit_wall_ms_sd'] for v in values],fmt=marker,linestyle='none',
            color=edge,markerfacecolor=fill,markersize=3.0,markeredgewidth=.7,
            elinewidth=.65,capsize=1.3,capthick=.65,zorder=4)
    audit.set_ylim(-.55,3.55);audit.set_yticks(x,['1','2','4','8'])
    audit.set_xlim(-50,1450);audit.set_xticks([0,700,1400])
    audit.xaxis.set_major_formatter(FuncFormatter(lambda value,position:f'{value/1000:g}'))
    scale_labels.append(audit.text(1,-.345,'\u00d710\u00b3',transform=audit.transAxes,
        ha='right',va='center',fontsize=6.5))
    audit.set_ylabel('Calls/request',labelpad=2.2)
    audit.set_xlabel('Audit time (ms)',labelpad=1.8)
    mode_handles=[Patch(facecolor=coral,edgecolor=coral_edge,linewidth=.45,label='Immediate'),
        Patch(facecolor=blue,edgecolor=blue_edge,linewidth=.45,hatch='///',label='Deferred')]
    legend_a=audit.legend(handles=mode_handles,loc='lower right',
        fontsize=6.1,handlelength=.95,handletextpad=.3,borderpad=.3,labelspacing=.25,
        borderaxespad=.35,framealpha=1,facecolor='white',edgecolor='#B6BBC3',fancybox=False)
    legend_a.get_frame().set_linewidth(.4)
    # Separate proving/verification work, retaining actual exact audit-rate settings.
    rates=[v['audit_rate_percent'] for v in c3];positions=list(range(6))
    proving=[v['amortized_full_prove_ms_per_request'] for v in c3]
    verification=[v['verification_ms_per_request'] for v in c3]
    means=[v['amortized_prove_verify_ms_per_request'] for v in c3]
    sd=[v['amortized_prove_verify_ms_per_request_sd'] for v in c3]
    b.bar(positions,proving,width=.58,color='#9895BF',edgecolor='#57517F',linewidth=.4,label='Proving',zorder=3)
    b.bar(positions,verification,bottom=proving,width=.58,color='#EDBE87',edgecolor='#92602E',
        linewidth=.4,hatch='///',label='Verification',zorder=3)
    b.errorbar(positions,means,yerr=sd,fmt='none',ecolor='#24262B',capsize=1.4,
        elinewidth=.65,capthick=.65,zorder=4)
    b.set_xlim(-.55,5.55);b.set_xticks(positions,[str(v) for v in rates])
    b.set_ylim(0,1450);b.set_yticks([0,600,1200])
    b.yaxis.set_major_formatter(FuncFormatter(lambda value,position:f'{value/1000:g}'))
    scale_labels.append(b.text(0,1.018,'\u00d710\u00b3',transform=b.transAxes,
        ha='left',va='bottom',fontsize=6.5))
    b.set_xlabel('Audit rate (%)',labelpad=1.8);b.set_ylabel('Audit cost (ms/request)',labelpad=2.4)
    default=c3[2];value=default['amortized_prove_verify_ms_per_request']
    b.add_patch(Rectangle((1.56,0),.88,180,fill=False,edgecolor=coral_edge,linestyle=(0,(2,1.5)),linewidth=.6,zorder=5))
    ann=b.annotate(f'10%: {value:.2f} ms/req',xy=(2,value),xytext=(.05,.365),
        textcoords=b.transAxes,ha='left',va='center',fontsize=6.1,color=indigo,
        bbox=dict(boxstyle='square,pad=.18',facecolor='white',edgecolor='#AEB4C2',linewidth=.4),
        arrowprops=dict(arrowstyle='->',color=coral_edge,linewidth=.6,shrinkA=2,shrinkB=3,
            connectionstyle='arc3,rad=.10'),zorder=8)
    annotations.append(ann)
    legend_b=b.legend(loc='upper left',fontsize=6.1,handlelength=.95,
        handletextpad=.4,borderpad=.35,labelspacing=.3,borderaxespad=.4,
        framealpha=1,facecolor='white',edgecolor='#B6BBC3',fancybox=False)
    legend_b.get_frame().set_linewidth(.4)
    # Actual two-worker elapsed drain times are a separate measurement dimension.
    proof_counts=[v['proofs_per_batch'] for v in c3]
    wall=[v['audit_makespan_ms']/1000 for v in c3]
    wall_sd=[v['audit_makespan_ms_sd']/1000 for v in c3]
    round_offsets_pt=(-2.0,0.0,2.0)
    for ri,offset_pt in zip((1,2,3),round_offsets_pt):
        drain.scatter(proof_counts,[v['audit_makespan_ms_round'+str(ri)]/1000 for v in c3],
            s=3.3,color='#747C8B',edgecolors='white',linewidths=.2,zorder=6,
            transform=drain.transData+ScaledTranslation(offset_pt/72,0,fig.dpi_scale_trans))
    drain.errorbar(proof_counts,wall,yerr=wall_sd,fmt='D',linestyle='none',color=indigo,
        markerfacecolor='none',markersize=3.3,markeredgewidth=.65,capsize=1.4,
        elinewidth=.65,capthick=.65,zorder=5)
    drain.set_xlim(-40,850);drain.set_xticks([0,400,800])
    drain.set_ylim(-3,70);drain.set_yticks([0,30,60])
    drain.set_xlabel('Proofs per batch',labelpad=1.8);drain.set_ylabel('Audit time (s)',labelpad=2.2)
    legend_drain=drain.legend(handles=[Line2D([],[],color='#747C8B',marker='o',linestyle='none',
            markersize=2.0,label='Rounds'),Line2D([],[],color=indigo,marker='D',markerfacecolor='none',
            linestyle='none',markersize=3.3,label='Mean '+chr(0xb1)+' SD')],
        loc='upper left',fontsize=6.0,handlelength=.8,handletextpad=.35,borderpad=.3,labelspacing=.25,
        borderaxespad=.3,framealpha=1,facecolor='white',edgecolor='#B6BBC3',fancybox=False)
    legend_drain.get_frame().set_linewidth(.4)
    default_wall=default['audit_makespan_ms']/1000
    ann=drain.annotate(f'80 proofs: {default_wall:.2f} s',xy=(80,default_wall),xytext=(.37,.18),
        textcoords=drain.transAxes,ha='left',va='center',fontsize=6.0,color=indigo,
        bbox=dict(boxstyle='square,pad=.18',facecolor='white',edgecolor='#AEB4C2',linewidth=.4),
        arrowprops=dict(arrowstyle='->',color=coral_edge,linewidth=.6,shrinkA=2,shrinkB=3,
            connectionstyle='arc3,rad=-.10'),zorder=8)
    annotations.append(ann)
    for ax in [online,audit,b,drain]:
        ax.grid(axis='y',color='#DBDDE2',linewidth=.4)
        ax.set_axisbelow(True);ax.tick_params(length=2.3,width=.65,pad=1.0)
        ax.xaxis.labelpad=.7
    # Position each title next to its own ticks; fixed offsets leave excessive
    # whitespace when one axis has fewer tick-label digits than its paired axis.
    for ax in [online,audit,b,drain]:
        ax.tick_params(axis='y',pad=1.0)
        ax.yaxis.labelpad=.6
    subtitles=[]
    for ax,text in zip([audit,drain],['(a) Proof scheduling','(b) Audit-rate scaling']):
        bounds=ax.get_position()
        subtitles.append(fig.text((bounds.x0+bounds.x1)/2,.040,text,ha='center',va='center',fontsize=7.6))
    fig.canvas.draw();renderer=fig.canvas.get_renderer();outside=[]
    visible_text=[]
    for ax in [online,audit,b,drain,zoom]:
        visible_text.extend(t for t in [ax.title,ax.xaxis.label,ax.yaxis.label,*ax.get_xticklabels(),*ax.get_yticklabels()]
            if t.get_visible() and t.get_text())
    visible_text.extend([*subtitles,*annotations,*scale_labels,*legend_a.get_texts(),*legend_b.get_texts(),*legend_drain.get_texts()])
    for item in visible_text:
        box=item.get_window_extent(renderer)
        if box.x0<0 or box.y0<0 or box.x1>fig.bbox.width or box.y1>fig.bbox.height:outside.append(item.get_text())
    assert not outside,outside
    ylabel_gaps=[]
    for ax in [online,audit,b,drain]:
        tick_left=min(t.get_window_extent(renderer).x0 for t in ax.get_yticklabels()
            if t.get_visible() and t.get_text())
        gap=(tick_left-ax.yaxis.label.get_window_extent(renderer).x1)*72/fig.dpi
        assert .2 <= gap <= 1.5,(ax.get_ylabel(),gap)
        ylabel_gaps.append(gap)
    subtitle_gaps=[]
    for subtitle,ax in zip(subtitles,[audit,drain]):
        gap=(ax.xaxis.label.get_window_extent(renderer).y0-subtitle.get_window_extent(renderer).y1)*72/fig.dpi
        assert gap>1,gap
        subtitle_gaps.append(gap)
    aligned_pairs=[]
    for upper,lower in [(online,audit),(b,drain)]:
        up,lo=upper.get_position(),lower.get_position()
        assert abs(up.x0-lo.x0)<1e-10 and abs(up.x1-lo.x1)<1e-10
        aligned_pairs.append([up.x0,up.x1])
    row_text_gaps=[]
    for upper,lower in [(online,audit),(b,drain)]:
        gap=(upper.xaxis.label.get_window_extent(renderer).y0-lower.bbox.y1)*72/fig.dpi
        assert gap>=1,(upper.get_xlabel(),gap)
        row_text_gaps.append(gap)
    column_text_gap=(b.yaxis.label.get_window_extent(renderer).x0-online.bbox.x1)*72/fig.dpi
    assert column_text_gap>=1,column_text_gap
    save_graphic(fig,'c_deferred_proving_audit_rate')
    # Export a real grayscale rendering while preserving all hatch/marker cues.
    gray_artists=list(fig.findobj())+[item.arrow_patch for item in annotations
        if getattr(item,'arrow_patch',None) is not None]
    gray_artists.extend(item.get_bbox_patch() for item in annotations
        if hasattr(item,'get_bbox_patch') and item.get_bbox_patch() is not None)
    for artist in gray_artists:
        for name in ('color','facecolor','edgecolor','markeredgecolor','markerfacecolor'):
            getter,setter=getattr(artist,'get_'+name,None),getattr(artist,'set_'+name,None)
            if not getter or not setter:continue
            try:
                rgba=to_rgba_array(getter())
                if not len(rgba):continue
                gray=rgba[:,0]*.2126+rgba[:,1]*.7152+rgba[:,2]*.0722;rgba[:,:3]=gray[:,None]
                setter(rgba[0] if len(rgba)==1 else rgba)
            except (ValueError,TypeError,AttributeError):pass
    for ext in ('png','pdf'):
        fig.savefig(OUT/('c_deferred_proving_audit_rate_grayscale.'+ext),dpi=1200 if ext=='png' else 300)
    from PIL import Image,ImageChops
    with Image.open(OUT/'c_deferred_proving_audit_rate_grayscale.png') as gray_image:
        red,green,blue_channel=gray_image.convert('RGB').split()
        assert ImageChops.difference(red,green).getbbox() is None
        assert ImageChops.difference(green,blue_channel).getbbox() is None
    plt.close(fig)
    return {'figure_size_inches':[3.5,2.38],'png_dpi':1200,'labels_outside_figure':outside,
        'frame_width_pt':.65,'panel_gap_pt':.100*3.5*72,'uncertainty':'sample SD of three round means',
        'row_xlabel_to_lower_frame_gap_pt':row_text_gaps,'column_ylabel_to_left_frame_gap_pt':column_text_gap,
        'subtitle_to_xlabel_gap_pt':subtitle_gaps,'upper_lower_frame_alignment':aligned_pairs,
        'ylabel_to_tick_gap_pt':ylabel_gaps,'ytick_padding_pt':1.0,
        'scientific_tick_scale':{'a_upper_y':1000,'a_lower_x':1000,'b_upper_y':1000},
        'b_yaxis_labels':[b.get_ylabel(),drain.get_ylabel()],
        'layout':'online paired bars/inset; audit dumbbells; proof/verify stacks; actual drain-time scatter',
        'online_axis_logarithmic':False,'deferred_inset_linear_ylim':[0,13],'c2_times_per_request':True,
        'c3_actual_round_points':18,'c3_proof_counts':[8,40,80,200,400,800],
        'c3_round_display_offsets_pt':list(round_offsets_pt),
        'c2_total_sd':'sum phases per round before aggregation','c3_includes_verification':True,
        'grayscale_channels_equal':True,
        'foreground_reduction_percent_mean_sd':list(reduction)}


def table_assets(rows,plt):
    from matplotlib.patches import Rectangle
    text=[r'\begin{table}[!t]',r'\centering',
        r'\caption{Assurance component costs (mean $\pm$ sample SD across three proof rounds or signing sessions).}',
        r'\label{tab:c-assurance-components}',r'\footnotesize',r'\renewcommand{\arraystretch}{1.08}',
        r'\begin{tabular*}{\columnwidth}{@{\hspace{3pt}}l@{\extracolsep{\fill}}cc@{\hspace{3pt}}}',
        r'\toprule',r'\textbf{Component} & \textbf{Unit} & \textbf{Mean $\pm$ SD} \\',r'\midrule']
    previous=None
    for row in rows:
        if row['group']!=previous:
            if previous is not None:text.append(r'\midrule')
            text.extend([r'\rowcolor{gray!12}',r'\multicolumn{3}{@{\hspace{3pt}}l@{\hspace{3pt}}}{\textbf{'+row['group']+r'}} \\'])
            previous=row['group']
        label=row['component']
        if label=='Paired signing increment':label=r'\textbf{'+label+'}'
        digits=row['digits'];value=f"{row['mean']:.{digits}f} $\\pm$ {row['sample_sd']:.{digits}f}"
        if row['component']=='Paired signing increment':
            value=r'\textcolor[HTML]{2E3B97}{\textbf{'+value+'}}'
        text.append(label+' & '+row['unit']+' & '+value+r' \\')
    text.extend([r'\bottomrule',r'\end{tabular*}',r'\end{table}',''])
    (OUT/'c_assurance_components_table.tex').write_text('\n'.join(text),encoding='utf-8')
    fig=plt.figure(figsize=(3.5,1.85));ax=fig.add_axes([0,0,1,1]);ax.set_xlim(0,252);ax.set_ylim(0,133.2);ax.axis('off')
    labels=[]
    def put(value,x,y,**kwargs):
        labels.append(ax.text(x,y,value,va='center',fontsize=7.6,**kwargs))
    ax.hlines([131,118],3,249,colors='#36383D',linewidths=[.8,.45])
    for value,x,ha in [('Component',6,'left'),('Unit',146,'center'),('Mean '+chr(0xb1)+' SD',207,'center')]:
        put(value,x,124.2,ha=ha,fontweight='bold')
    for group,y in [(rows[0]['group'],107),(rows[3]['group'],51)]:
        ax.add_patch(Rectangle((3,y-6),246,12,facecolor='#E8E8E8',edgecolor='none'))
        put(group,6,y,ha='left',fontweight='bold')
    ax.hlines([61,4],3,249,colors='#36383D',linewidths=[.45,.8])
    for row,y in zip(rows,[94,81,68,38,25,12]):
        bold=row['component']=='Paired signing increment'
        put(row['component'],6,y,ha='left',fontweight='bold' if bold else 'normal')
        put(row['unit'],146,y,ha='center')
        n=row['digits'];value=f"{row['mean']:.{n}f} "+chr(0xb1)+f" {row['sample_sd']:.{n}f}"
        put(value,207,y,ha='center',color='#2E3B97' if bold else '#24262B',fontweight='bold' if bold else 'normal')
    fig.canvas.draw();renderer=fig.canvas.get_renderer()
    outside=[];overlaps=[]
    for item in labels:
        b=item.get_window_extent(renderer)
        if b.x0<0 or b.y0<0 or b.x1>fig.bbox.width or b.y1>fig.bbox.height:outside.append(item.get_text())
    for i,first in enumerate(labels):
        for second in labels[i+1:]:
            if first.get_window_extent(renderer).overlaps(second.get_window_extent(renderer)):
                overlaps.append([first.get_text(),second.get_text()])
    assert not outside,outside
    assert not overlaps,overlaps
    save_graphic(fig,'c_assurance_components_table_preview');plt.close(fig)
    return {'table_labels_outside':outside,'table_text_overlaps':overlaps,'table_width_inches':3.5}


def integration():
    (OUT/'c_paper_integration.tex').write_text(r'''% Requires graphicx, booktabs, and xcolor with the table option (or colortbl).
\begin{figure}[!t]
  \centering
  \includegraphics[width=\columnwidth]{c_deferred_proving_audit_rate.pdf}
  \caption{Scoped proving on retained real-serving witnesses using persistent
  CPU workers. (a) Online protocol cost (upper, paired bars) and subsequent audit
  cost (lower, dumbbells), in ms/request; the linear-scale inset enlarges
  deferred online cost. The mode legend applies to both plots.
  (b) Audit cost sums the scoped witness, proving, and verification operation
  wall times and divides by all 100 batch requests (upper); audit time is
  actual elapsed completion time versus proof count with two workers
  (lower). Each request has eight calls. Error bars show the sample SD across
  three rounds; gray dots show individual round-level audit times, horizontally
  offset for visibility without changing their measured times.
  Summed proving work and elapsed batch time are reported separately.}
  \label{fig:c-deferred-audit}
\end{figure}

\input{c_assurance_components_table.tex}
''',encoding='utf-8')
    (OUT/'c_preview.tex').write_text(r'''\documentclass[conference]{IEEEtran}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage[table]{xcolor}
\usepackage{mathptmx}
\pagestyle{empty}
\begin{document}
\input{c_paper_integration.tex}
\clearpage
\end{document}
''',encoding='utf-8')


def main():
    OUT.mkdir(exist_ok=True)
    c2,c3,table,reduction=derive();plt,font=setup()
    result=plot(c2,c3,reduction,plt);result.update(table_assets(table,plt));integration()
    result.update({'font':font,'source_sha256':{
        'round_metrics.csv':hashlib.sha256((SOURCE/'round_metrics.csv').read_bytes()).hexdigest(),
        'tee_signing_summary.json':hashlib.sha256(VBS.read_bytes()).hexdigest()},'no_new_experiment_execution':True})
    (OUT/'c_figure_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    (OUT/'C_FIGURE_README.md').write_text('''# C-section main figure and component table

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
''',encoding='utf-8')
    print(json.dumps({'output':str(OUT),**result},ensure_ascii=True))


if __name__=='__main__':main()
