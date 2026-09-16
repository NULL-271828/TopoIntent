# Hardware/software

Default recommendation (estimated): Linux x86-64; Python 3.11–3.13, venv/pip; 4 CPU threads; 16 GB RAM; 8 GB disk for environment, model, and results. Network is needed for pip/model acquisition only. No paid API, GUI, CUDA, Qdrant server, privileged network access, or LLM is required. Tested Python 3.13.13, four CPU threads; environment details are in `docs/environment.json` and `docs/requirements-tested.txt`.

Install pins are preparation-tested dependencies, not a recovered original lock. PyTorch CPU wheels avoid installing CUDA into default AE. The model is roughly 2.3 GB; downloaded weights/environment are never shipped in the source artifact. Batch size 2 avoids unnecessarily large padded inputs. All encoded strings are checked against the 8192-token limit; no silent truncation.

The author-described full model setup uses about 4×RTX 4090. The supplied manuscript instead describes an 8×4090 server. Original source uses the 72B AWQ identifier. Exact allocation, revisions, and quantization require author confirmation; no 72B resource measurement was attempted here.

Public infrastructure: ordinary CloudLab/Chameleon CPU Linux instances can host default evaluation. A disposable privileged bare-metal/VM Linux host is appropriate for Mininet. No public instance was allocated or tested during preparation. Full 72B GPU capacity cannot be assumed available to every evaluator, so the default releases original outputs and independently executes affordable downstream work.

Historical runtime evidence: the released main-run manifest records 7073.787 seconds (117.9 minutes) for 14 intents across both IGs through script generation; the feedback manifest records 4095.683 seconds (68.3 minutes). These are original-run timings, not measurements on the preparation CPU or a guarantee for other hardware. Mininet time is separate.
