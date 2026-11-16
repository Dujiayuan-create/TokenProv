"""Retained paper inputs must remain complete and internally consistent."""
import csv
import importlib.util
import json
from pathlib import Path
import statistics

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('paper_reproduction',ROOT/'paper/reproduce.py')
paper=importlib.util.module_from_spec(spec)
spec.loader.exec_module(paper)


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        return list(csv.DictReader(stream))


def test_retained_paper_files_match_manifest():
    assert paper.verify_inputs()>100


def test_every_serving_round_retained_and_timings_match():
    metrics=read_csv(ROOT/'paper/data/five_model_three_round_metrics.csv')
    traces=read_csv(ROOT/'paper/data/b1/request_latency_data.csv')
    timing=[json.loads(line) for line in (ROOT/'paper/data/b2/request_measurements.jsonl').read_text().splitlines()]
    assert len(metrics)==60 and len(traces)==len(timing)==7680
    assert len({r['model'] for r in metrics})==5
    for row in metrics:
        selected=[r for r in timing if r['model']==row['model'] and r['system']==row['system'] and r['round']==int(row['round'])]
        assert len(selected)==128 and all(r['success'] and r['actual_calls']==8 for r in selected)
        mean=statistics.mean(r['request_pipeline_ns']/1e6 for r in selected)
        assert mean==pytest.approx(float(row['mean_pipeline_ms']),rel=1e-10)
    deepseek=[r for r in metrics if r['model']=='deepseek-r1-distill-llama-8b' and r['system']=='TokenProv-Full']
    assert len(deepseek)==3 and max(float(r['pipeline_delta_vs_vanilla_percent']) for r in deepseek)>40


def test_reproduction_rejects_output_over_retained_inputs():
    with pytest.raises(ValueError,match='separate'):
        paper.main(['--section','b1','--output',str(ROOT/'paper/data')])
