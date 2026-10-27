# Open-weight base shortlist and compute decision — 2026-10-27

Primary model cards checked on 2026-10-27:

| Candidate | Card license | Intended experiment |
|---|---|---|
| [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) | Apache-2.0 | Small general/instruction baseline candidate |
| [Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B) | Apache-2.0 | Compare capability/cost if budget permits |
| [SmolLM2-135M](https://huggingface.co/HuggingFaceTB/SmolLM2-135M) | Apache-2.0 | Very small base-model pipeline smoke candidate |

These are candidates, not a claim of best/current market coverage or approved training use.
Pin exact model/tokenizer revisions, review full license/notices and distribution obligations,
and retain checksums before download/redistribution. Do not retain provider hidden reasoning
as training data; the output collection policy applies regardless of model mode.

Observed host: ARM64 Android/PRoot environment, about 5.3 GiB total RAM and 2 GiB available
at probe time; no `nvidia-smi` found. Do not infer a usable training accelerator from phone GPU
presence. No sustained training, thermal or memory-budget qualification exists.
At 2 bytes/parameter, weights alone are roughly 0.27 / 1.2 / 3.4 GB for the nominal sizes;
optimizer, gradients, activations, tokenizer/runtime and OS costs are additional. This simple
arithmetic is not a training-memory forecast. Quantization/adapter feasibility needs measurement.

Next: fix an affordable compute budget, acquire a permitted corpus and independent evaluation,
benchmark base inference and peak memory, then choose SFT/domain adaptation scope. No frontier
pretraining. Specialists only if they beat the general baseline on their intended jobs.
