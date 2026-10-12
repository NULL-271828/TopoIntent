#!/usr/bin/env python3
"""Metric/table verification from released original experimental outputs."""
import argparse,csv,json,sys,time
from pathlib import Path
A=Path(__file__).resolve().parents[1];P=A/'artifact/src';R=P/'evaluation/evaluation_runs';M=A/'artifact/precomputed/metrics'
sys.path.insert(0,str(P/'evaluation'))
import metric_stage4 as s4,metric_stage5 as s5,metric_ablation_table as ab
from retrieval_core import compute_metrics
from run_retrieval import inputs
INITIAL='test_all_single_vector_structfix_20260526_091223';FEEDBACK='test_all_single_vector_structfix_feedback'
ABL='rag_ablation_structfix_20260526_130809';NOREPAIR='rag_ablation_test_20260526_010652'
def load(p):return json.loads(p.read_text())
def report_validate(p):
 import jsonschema
 d=load(p);jsonschema.validate(d,load(A/'artifact/schemas/stage5-report.schema.json'))
 for phase in ['pre_acl','post_acl']:
  rows=d[phase+'_policy_tests'];su=d[phase+'_policy_summary']
  actual={'total':len(rows),'skipped':sum(x['actual']=='skip' for x in rows),'executed':sum(x['actual']!='skip' for x in rows),'passed':sum(x['passed'] is True for x in rows),'failed':sum(x['actual']!='skip' and not x['passed'] for x in rows)}
  assert all(su[k]==v for k,v in actual.items()),f'{p.name}: inconsistent {phase} counters'
 node=d['node_link_tests'];su=d['node_link_summary'];lat=[x['latency_ms'] for x in node if x['latency_ms'] is not None]
 assert su['total']==len(node) and su['reachable']==sum(x['reachable'] for x in node)
 assert su['unreachable']==len(node)-su['reachable']
 assert su['avg_latency_ms']==(round(sum(lat)/len(lat),3) if lat else None)
 return d

def stage1():
 refs,meta,queries=inputs();data=load(P/'dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_train.json')['items'];zs=[];ns=[];ss=[]
 for x,q in zip(data,queries):
  gt=x['ground_truth']['topology_template'];ss.append(q['scenario'].lower()==gt['scenario'].lower())
  for arr,key,col in [('zones','zone_type',zs),('nodes','normalized_type',ns)]:
   g={v.get(key,'') for v in gt[arr]};v={v.get(key,'') for v in q[arr]};col.append(len(g&v)/len(g) if g else 1.)
 return dict(zip(['scenario_accuracy','zone_type_recall','node_type_recall'],[round(sum(v)/44,4) for v in [ss,zs,ns]]))

def stage2():
 result={}
 for v in ['single_vector','multi_field_uniform','multi_field_weighted']:
  rows=load(M/f'metric_stage2_{v}_retrieval.json');assert len(rows)==44 and len({x['sample_id'] for x in rows})==44
  d=compute_metrics(rows);result[v]={k:d[k] for k in ['top_k','mrr','avg_hit_rank','miss_count']}
 return result

def stage4():
 files=s4.collect_stage4_outputs(R/INITIAL);assert len(files)==28
 rows=[s4.compute_one(p) for p in files];assert len({(x['sample_id'],x['ig_level']) for x in rows})==28
 assert all(x['controls_total']>0 for x in rows)
 return s4.summarize(rows)

def stage5():
 rows=[]
 for run,sub,setting in [(INITIAL,'reports_from_wsl','Initial'),(FEEDBACK,'reports_from_wsl/all_reports','After Feedback')]:
  files=s5.collect_reports(R/run/sub);assert len(files)==28
  assert len({s5.parse_report_identity(p) for p in files})==28
  for p in files:report_validate(p);rows.append(s5.compute_one(p,setting))
 return s5.summarize(rows)

def end_to_end():
 dataset=P/'dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_test_old.json'
 order,gt=ab.load_ground_truth(dataset);assert len(order)==14
 groups=[('Direct',ab.collect_direct_rows(R/'direct_baseline_test_20260525_234326',order,gt),None),('RAG w/o Fusion',ab.collect_rag_only_rows(R/'rag_without_fusion_test_20260526_003349',order,gt),None),('RAG w/o Repair',ab.collect_stage4_variant_rows(R/NOREPAIR,'rag_without_repair',order,gt),None)]
 for name,rows,folder in [('RAG w/o Feedback',ab.collect_stage4_variant_rows(R/ABL,'rag_without_feedback',order,gt),R/ABL/'reports_from_wsl/rag_without_feedback'),('Full TopoIntent',ab.collect_full_rows(R/FEEDBACK,order,gt),R/FEEDBACK/'reports_from_wsl/all_reports')]:
  files=s5.collect_reports(folder);assert len(files)==28
  for p in files:report_validate(p)
  rate,passed,executed,n=ab.post_acl_rate(folder);groups.append((name,rows,rate))
 return [ab.summarize_rows(n,r,p) for n,r,p in groups]

def flatten(d,p=''):
 if isinstance(d,dict):
  return {kk:vv for k,v in d.items() for kk,vv in flatten(v,p+'/'+k).items()}
 if isinstance(d,list):return {kk:vv for i,v in enumerate(d) for kk,vv in flatten(v,p+'/'+str(i)).items()}
 return {p:d}
def compare(actual,expected,tolerance):
 aa=flatten(actual);ee=flatten(expected);rows=[]
 for key,e in ee.items():
  a=aa.get(key);num=isinstance(e,(int,float)) and not isinstance(e,bool);diff=(a-e) if num and isinstance(a,(int,float)) else None
  ok=abs(diff)<=tolerance+1e-12 if diff is not None else a==e
  rows.append({'metric':key,'actual':a,'expected':e,'difference':diff,'tolerance':tolerance if num else 0,'status':'PASS' if ok else 'FAIL'})
 return rows

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--claim',choices=['all','stage1','retrieval','cis_repair','stage5_validation','end_to_end'],default='all');group=ap.add_mutually_exclusive_group();group.add_argument('--evidence-only',action='store_true',help='Compatibility alias for the default evidence verification');group.add_argument('--strict-paper',action='store_true',help='Also fail on paper-display discrepancies');args=ap.parse_args();start=time.monotonic()
 (A / 'outputs').mkdir(exist_ok=True)
 print(__doc__);print('This does not independently rerun Qwen2.5-72B inference or Mininet.')
 expected1=load(M/'metric_stage1_summary.json');expected1={k:expected1[k] for k in ['scenario_accuracy','zone_type_recall','node_type_recall']}
 jobs={'stage1':(stage1,expected1),'retrieval':(stage2,load(M/'metric_stage2_summary.json')['variants']),'cis_repair':(stage4,load(M/(INITIAL+'_metric_stage4_summary.json'))['summary']),'stage5_validation':(stage5,load(M/'test_all_single_vector_structfix_full_metric_stage5_summary.json')['summary']),'end_to_end':(end_to_end,load(M/'ablation_table_old_gt_original_sources_check_summary.json')['summary'])}
 papers=load(A/'claims/paper_expected.json');output={};ok=True
 for name,(fn,expected) in jobs.items():
  if args.claim not in ['all',name]:continue
  actual=fn();evidence=compare(actual,expected,0);paper=compare(actual,papers[name],.0050001)
  evidence_ok=all(x['status']=='PASS' for x in evidence);paper_ok=all(x['status']=='PASS' for x in paper)
  print(name,'original-output consistency:', 'PASS' if evidence_ok else 'FAIL','paper comparison:','PASS' if paper_ok else 'WARNING')
  for x in evidence:
   if x['status']=='FAIL':print('ERROR: RELEASED EVIDENCE INCONSISTENCY',x)
  report={'retrieval':'docs/TABLE2_PROVENANCE_REPORT.md','cis_repair':'docs/TABLE3_DISCREPANCY_REPORT.md'}.get(name,'CLAIMS.md')
  for x in paper:
   if x['status']=='FAIL':
    print(f"WARNING: PAPER VALUE DISCREPANCY — {name}{x['metric']}: paper={x['expected']}; recomputed={x['actual']}; difference={x['difference']:+.4f}; details={report}")
  output[name]={'recomputed':actual,'original_output_comparison':evidence,'paper_comparison':paper}
  ok &= evidence_ok and (paper_ok or not args.strict_paper)
 dest=A/'outputs'/('verification_'+args.claim+'.json');dest.write_text(json.dumps(output,indent=2))
 table=['# Reconstructed paper metrics', '', 'Metric/table verification from released original experimental outputs.', '', '| Claim | Metric | Recomputed | Paper | Difference | Status |', '|---|---|---:|---:|---:|---|']
 for name,item in output.items():
  for row in item['paper_comparison']:
   table.append(f"| {name} | {row['metric']} | {row['actual']} | {row['expected']} | {row['difference']} | {row['status']} |")
 (A/'outputs'/('reconstructed_'+args.claim+'.md')).write_text('\n'.join(table)+'\n')
 if 'cis_repair' in output:(A/'outputs/table3.tex').write_text(s4.build_latex_table(output['cis_repair']['recomputed']))
 if 'stage5_validation' in output:(A/'outputs/table4.tex').write_text(s5.build_latex_table(output['stage5_validation']['recomputed']))
 if 'end_to_end' in output:(A/'outputs/table5.tex').write_text(ab.latex_table(output['end_to_end']['recomputed']))
 print(f'Elapsed {time.monotonic()-start:.2f}s; details: {dest.relative_to(A)}')
 raise SystemExit(0 if ok else 1)
if __name__=='__main__':main()
