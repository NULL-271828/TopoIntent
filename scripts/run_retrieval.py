#!/usr/bin/env python3
"""Real BGE-M3 dense retrieval, exact cosine or isolated Qdrant local backend."""
import argparse,json,os,time,hashlib
from pathlib import Path
from retrieval_core import flatten_template_text,build_query_texts,build_field_texts,unique_templates_by_id,compute_metrics
A=Path(__file__).resolve().parents[1];P=A/'artifact/src'
WEIGHTS={'scenario':.30,'security':.25,'description':.18,'topology_summary':.17,'title':.10}
def load(p):return json.loads(p.read_text())
def resolve_bge_model(model_id,source):
 source=str(source or 'huggingface').strip().lower()
 if source=='huggingface':
  print('Model source: Hugging Face',flush=True);print(f'Model: {model_id}',flush=True);return model_id,source
 if source=='modelscope':
  try:
   from modelscope import snapshot_download
  except ImportError:
   raise SystemExit('ModelScope support is optional and is not installed.\nInstall it with:\n\n  .venv/bin/pip install -r requirements-modelscope.txt\n\nThen rerun:\n\n  BGE_SOURCE=modelscope ./claims/retrieval/run.sh')
  print('Model source: ModelScope',flush=True);print(f'Model: {model_id}',flush=True)
  try:
   # Transformers needs the PyTorch checkpoint and tokenizer/configuration
   # files; avoid downloading unrelated ONNX and image assets from the hub.
   local_dir=snapshot_download(model_id=model_id,allow_patterns=['*.json','*.txt','*.model','pytorch_model.bin','*.safetensors'])
  except Exception as exc:raise SystemExit(f'ModelScope download failed for {model_id}: {exc}') from exc
  local_dir=Path(local_dir).resolve()
  if not local_dir.is_dir():raise SystemExit(f'ModelScope returned an invalid model directory: {local_dir}')
  print(f'Resolved local model directory: {local_dir}',flush=True);return str(local_dir),source
 raise SystemExit(f'Unsupported BGE_SOURCE: {source}\nSupported values: huggingface, modelscope')
def inputs():
 data=load(P/'dataset/topology/metadata_process/Stage4_synthetic_intent/Stage4_synthetic_intent_train.json')['items']
 base=P/'logs/stable_outputs/stage1_output/reference_train'
 meta=load(base/'stage1_extracted_intents.json');queries=load(base/'stage1_generated_templates.json')
 assert len(data)==len(meta)==len(queries)==44
 assert [x['sample_id'] for x in data]==[x['sample_id'] for x in meta]
 assert all(q['template_id']=='generated_'+m['sample_id'] for m,q in zip(meta,queries))
 refs=unique_templates_by_id([x['ground_truth']['topology_template'] for x in data]);assert len(refs)==22
 return refs,meta,queries

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--model',default=os.getenv('BGE_MODEL','BAAI/bge-m3'));ap.add_argument('--device',choices=['cpu','cuda'],default='cpu');ap.add_argument('--variant',choices=['all','single_vector','multi_field_uniform','multi_field_weighted'],default='single_vector');ap.add_argument('--backend',choices=['exact','qdrant'],default='exact');ap.add_argument('--threads',type=int,default=4);ap.add_argument('--batch-size',type=int,default=2);ap.add_argument('--limit',type=int);ap.add_argument('--check-inputs',action='store_true');ap.add_argument('--all','--strict',dest='variant',action='store_const',const='all',help='Strict audit of all retrieval strategies, including historical ablations');args=ap.parse_args()
 model_source=os.getenv('BGE_SOURCE','huggingface').strip().lower()
 if model_source not in {'huggingface','modelscope'}:
  raise SystemExit(f'Unsupported BGE_SOURCE: {model_source}\nSupported values: huggingface, modelscope')
 refs,meta,queries=inputs()
 if args.variant=='single_vector':print('Main strategy: single-vector. Historical multi-field ranking discrepancies remain disclosed in docs/TABLE2_PROVENANCE_REPORT.md; run --all for strict live ablation audit.',flush=True)
 if args.check_inputs: print('PASS: 22 reference templates, 44 aligned original Stage 1 queries');return
 if args.limit is not None:
  if not 1<=args.limit<=44:ap.error('--limit must be 1..44')
  meta=meta[:args.limit];queries=queries[:args.limit]
 import numpy as np
 import torch
 from transformers import AutoTokenizer,AutoModel
 torch.set_num_threads(args.threads)
 resolved_model,model_source=resolve_bge_model(args.model,model_source)
 start=time.monotonic();print('Loading real BAAI/bge-m3; dense CLS pooling; float32; no LLM inference',flush=True)
 tokenizer=AutoTokenizer.from_pretrained(resolved_model,trust_remote_code=False)
 model=AutoModel.from_pretrained(resolved_model,trust_remote_code=False).to(args.device).eval()
 if model.config.model_type!='xlm-roberta' or model.config.hidden_size!=1024:raise ValueError('Expected BGE-M3 xlm-roberta, 1024 dimensions')
 cache={};texts=[]
 ref_fields=[build_field_texts(t) for t in refs];query_fields=[build_query_texts(t) for t in queries]
 texts += [flatten_template_text(t) for t in refs+queries]
 if args.variant != 'single_vector':
  texts += [d[f] for d in ref_fields+query_fields for f in WEIGHTS if d.get(f)]
 texts=list(dict.fromkeys(texts))
 for start_i in range(0,len(texts),args.batch_size):
  batch=texts[start_i:start_i+args.batch_size]
  tokens=tokenizer(batch,padding=True,truncation=False,return_tensors='pt')
  if tokens['input_ids'].shape[1]>8192:raise ValueError('Input exceeds BGE-M3 limit; refusing silent truncation')
  with torch.inference_mode():
   out=model(**{k:v.to(args.device) for k,v in tokens.items()}).last_hidden_state[:,0]
   out=torch.nn.functional.normalize(out,p=2,dim=1).cpu().numpy()
  cache.update(zip(batch,out))
  if start_i%20==0:print(f'Embedded {min(start_i+len(batch),len(texts))}/{len(texts)} texts',flush=True)
 client=None
 if args.backend=='qdrant' and args.variant != 'single_vector':
  from qdrant_client import QdrantClient,models
  client=QdrantClient(':memory:')
  client.create_collection('ae',vectors_config=models.VectorParams(size=1024,distance=models.Distance.COSINE))
  points=[]
  for j,d in enumerate(ref_fields):
   for f in WEIGHTS:
    if d.get(f):points.append(models.PointStruct(id=len(points),vector=cache[d[f]].tolist(),payload={'index':j,'field_name':f}))
  client.upsert('ae',points)
 expected=load(A/'artifact/precomputed/metrics/metric_stage2_summary.json')['variants']
 paper={'single_vector':[.75,.91,1.,.84],'multi_field_uniform':[.60,.93,1.,.75],'multi_field_weighted':[.57,.93,.98,.75]}
 results={};all_ok=True
 for variant in expected:
  if args.variant not in ['all',variant]:continue
  ranks=[]
  for m,q,qf in zip(meta,queries,query_fields):
   if variant=='single_vector':scores=np.array([cache[flatten_template_text(q)] @ cache[flatten_template_text(r)] for r in refs])
   else:
    active=[f for f in WEIGHTS if qf.get(f)];scores=np.zeros(len(refs))
    for f in active:
     weight=1/len(active) if variant=='multi_field_uniform' else WEIGHTS[f]
     if client:
      hits=client.query_points('ae',query=cache[qf[f]].tolist(),query_filter=models.Filter(must=[models.FieldCondition(key='field_name',match=models.MatchValue(value=f))]),limit=50).points
      for h in hits:scores[h.payload['index']]+=weight*h.score
     else:
      for j,d in enumerate(ref_fields):
       if d.get(f):scores[j]+=weight*float(cache[qf[f]] @ cache[d[f]])
   order=sorted(range(len(refs)),key=lambda j:-scores[j])[:5]
   ranks.append({'sample_id':m['sample_id'],'gt_template_id':m['template_id'],'retrieved_templates':[{'template_id':refs[j]['template_id'],'score':round(float(scores[j]),4)} for j in order]})
  metric=compute_metrics(ranks);actual=[*metric['top_k'].values(),metric['mrr']];exp=[*expected[variant]['top_k'].values(),expected[variant]['mrr']]
  # At most one reference query's contribution. No tolerance tuning after observing run.
  tolerance=1/44+0.0001
  passed=all(abs(a-e)<=tolerance for a,e in zip(actual,exp)) if len(queries)==44 else None
  all_ok=all_ok and passed is True
  print(variant, 'actual=',actual,'original=',exp,'paper=',paper[variant], 'difference=',[round(a-e,4) for a,e in zip(actual,exp)],'tolerance=',tolerance,'status=',('PASS' if passed else 'FAIL') if passed is not None else 'SCALED RUN; no full-table comparison',flush=True)
  results[variant]={'metrics':metric,'rankings':ranks,'expected_original':exp,'expected_paper':paper[variant],'pass':passed}
 if client:client.close()
 out={'mode':'LIVE BGE-M3 retrieval from released Stage 1 outputs; not an LLM rerun','model':'BAAI/bge-m3','model_source':model_source,'resolved_model_directory':str(resolved_model) if model_source=='modelscope' else None,'backend':args.backend,'device':args.device,'dtype':'float32','hash_seed':os.getenv('PYTHONHASHSEED'),'samples':len(queries),'elapsed_seconds':time.monotonic()-start,'model_architecture':model.config.model_type,'hidden_size':model.config.hidden_size,'variants':results,'limitation':'Original index used unordered set serialization in security text; original hash seed/model revision/vLLM pooling not recorded. Exact cosine scoring is equivalent for identical embeddings, but index text/encoder parity is not established.'}
 dest=A/'outputs'/('retrieval_'+args.backend+('' if args.variant=='all' else '_'+args.variant)+('_scaled' if args.limit else '')+'.json');dest.parent.mkdir(exist_ok=True);dest.write_text(json.dumps(out,indent=2));print('Saved',dest.relative_to(A),flush=True)
 if len(queries)==44 and not all_ok:raise SystemExit(1)
if __name__=='__main__':main()
