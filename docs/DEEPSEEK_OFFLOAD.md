# DeepSeek V4.1 routed-expert SSD cache experiment

Status: **experimental storage/I/O prototype**. This is **not** a DeepSeek
inference engine or a kimi-k3-in-c port. It produces no model tokens,
does not decode quantized tensors, does not route experts, and does not
implement CED, CSA2, Engram, mHC, attention, MTP or GPU compute.

## Research question

When a DeepSeek V4.1-family checkpoint stores split routed-expert tensors
in safetensors, how much checkpoint I/O can a bounded RAM LRU save for a
given expert-routing trace? The experiment isolates disk reads, byte
correctness, retained cache occupancy, and hit rate from model math.

The official DeepSeek V4.1-Flash family uses 384 routed experts per MoE
layer and selects 6 per token (plus one shared expert). This prototype
does not calculate the selection: it replays a provided selection trace.

Sources:
- [Official DeepSeek V4.1 Flash model](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)
- [NVIDIA checkpoint structure notes](https://docs.nvidia.com/nemo/automodel/nemo-automodel/nemo_automodel/components/models/deepseek_v41/state_dict_adapter)
- [kimi-k3-in-c (inspiration; no code dependency)](https://github.com/FareedKhan-dev/kimi-k3-in-c)

## 1. Local smoke test (no weights needed)

Requires Python 3.11+ and no extra runtime dependencies. From the repository root:

    python -m deepseek_offload.moe_offload fixture --out /tmp/dsv41-toy
    python -m deepseek_offload.moe_offload scan /tmp/dsv41-toy --output /tmp/dsv41-manifest.json
    python -m deepseek_offload.moe_offload replay /tmp/dsv41-toy --manifest /tmp/dsv41-manifest.json --trace /tmp/dsv41-toy/trace.jsonl --budget-mib 0.01
    python -m pytest tests/test_deepseek_offload.py

The fixture creates 72 small routed-expert projection tensors
(3 layers x 8 experts x 3 matrices) and a deterministic 36-layer-step
trace. It is synthetic, not a functioning DeepSeek model.

## 2. Scan real checkpoint headers

    python -m deepseek_offload.moe_offload scan /models/DeepSeek-V4.1-Flash-1017 --output /tmp/deepseek-v41-manifest.json

The parser matches tensors such as:
- layers.2.ffn.experts.17.w1.weight
- layers.2.ffn.experts.17.w1.weight_scale
- layers.2.ffn.experts.17.w2.weight

It reads safetensors headers only, retains byte offsets, detects
duplicate names and invalid ranges, and checks names against
model.safetensors.index.json when present. The parser targets
original split-expert layouts. GGUF, grouped/repacked expert tensors,
and other naming conventions need a separate adapter.

Input routing trace: JSONL, one step per layer with routed expert IDs:

    {"layer": 2, "experts": [17, 41, 63, 80, 127, 201]}
    {"layer": 3, "experts": [12, 26, 79, 110, 180, 256]}

Only replay after obtaining real routing IDs from a compatible model
runtime. The fixture's trace does not reflect real model routing.

    python -m deepseek_offload.moe_offload replay /models/DeepSeek-V4.1-Flash-1017 --manifest /tmp/deepseek-v41-manifest.json --trace /path/to/real-routing.jsonl --budget-mib 4096

Each cache entry is a complete (layer, expert) set of matched
projection/scaling tensors. LRU evicts complete experts. os.pread()
reads exact ranges and SHA-256 covers the bytes returned during replay.

## 3. Interpretation and decision gate

- hits / misses / hit_rate: expert reuse; not model throughput.
- bytes_read: requested filesystem bytes, not necessarily NVMe bytes;
  OS page cache may serve every read.
- peak_retained_payload_bytes: payload cache only; **not** process RSS
  or a complete 8GB RAM cap. Compute, scratch buffers, OS page cache
  and callers consume additional memory.
- elapsed_seconds: checksum-heavy replay time; **not** decode tokens/s.

A future engine must integrate correct V4.1 routing, attention,
quantization kernels, Engram, and token generation. The next experiment
should obtain real routing traces and compare no cache, LRU and
prefetch strategies under fixed weights, cache budgets, identical
traces and controlled OS page-cache conditions.

No full-model inference or real-hardware speed claim is made here.
