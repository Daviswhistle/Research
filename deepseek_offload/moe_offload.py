#!/usr/bin/env python3
"""DeepSeek V4.1 split-expert safetensors disk I/O experiment.

Not an inference engine: no routing, matrix multiplication, attention, or Engram.
Uses stdlib only; leaves checkpoint bytes untouched.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import time

EXPERT_KEY = re.compile(
    r"(?:^|\.)layers\.(\d+)\.ffn\.experts\.(\d+)\.(w[123])(?:\.|$)"
)
MAX_HEADER_BYTES = 128 * 1024 * 1024


def scan_safetensors(root: Path) -> dict:
    """Index exact byte ranges; do not decode quantized weights."""
    root = root.resolve(strict=True)
    shards = sorted(root.glob("*.safetensors"))
    if not shards:
        raise ValueError("no .safetensors shards found")
    entries = []
    seen_names = set()
    for shard in shards:
        with shard.open("rb") as handle:
            prefix = handle.read(8)
            if len(prefix) != 8:
                raise ValueError(f"short header in {shard.name}")
            header_len = struct.unpack("<Q", prefix)[0]
            if not 0 < header_len <= MAX_HEADER_BYTES:
                raise ValueError(f"invalid header length in {shard.name}")
            raw = handle.read(header_len)
            if len(raw) != header_len:
                raise ValueError(f"truncated header in {shard.name}")
            header = json.loads(raw)
        data_start = 8 + header_len
        total_bytes = shard.stat().st_size
        for name, meta in header.items():
            match = EXPERT_KEY.search(name)
            if not match:
                continue
            if name in seen_names:
                raise ValueError(f"duplicate tensor: {name}")
            seen_names.add(name)
            if not isinstance(meta, dict) or "data_offsets" not in meta:
                raise ValueError(f"missing offsets for {name}")
            offsets = meta["data_offsets"]
            if (not isinstance(offsets, list) or len(offsets) != 2
                    or any(type(n) is not int for n in offsets)):
                raise ValueError(f"invalid offsets for {name}")
            start, end = offsets
            if start < 0 or end <= start or data_start + end > total_bytes:
                raise ValueError(f"out-of-bounds tensor: {name}")
            entries.append({
                "name": name,
                "layer": int(match[1]),
                "expert": int(match[2]),
                "projection": match[3],
                "file": shard.name,
                "offset": data_start + start,
                "length": end - start,
                "dtype": meta.get("dtype"),
                "shape": meta.get("shape"),
            })
    if not entries:
        raise ValueError("no split routed-expert tensors detected; "
                         "fused/GGUF checkpoints need a different indexer")

    # An index file, when supplied, lets us detect missing expert shards.
    index_path = root / "model.safetensors.index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        expected = {k for k in index.get("weight_map", {}) if EXPERT_KEY.search(k)}
        missing = expected - seen_names
        if missing:
            raise ValueError(f"incomplete split-expert checkpoint: "
                             f"{len(missing)} indexed tensors missing")
    entries.sort(key=lambda x: (x["layer"], x["expert"], x["name"]))
    return {"schema": 1, "model": "deepseek_v41_split_experts",
            "segments": entries, "source": str(root)}


def read_exact(fd: int, offset: int, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        part = os.pread(fd, remaining, offset + size - remaining)
        if not part:
            raise EOFError(f"short read at {offset}, expected {size} bytes")
        chunks.append(part)
        remaining -= len(part)
    return b"".join(chunks)


class ExpertCache:
    """Bounded *retained payload* LRU; one cache entry is one routed expert.

    Returned byte buffers remain owned by the caller. Do not interpret budget
    as an RSS cap: Python, open descriptors, temporary buffers and callers add
    memory. This is synchronous I/O, without predictive prefetch.
    """
    def __init__(self, root: Path, manifest: dict, budget_bytes: int):
        if budget_bytes <= 0:
            raise ValueError("cache budget must be positive")
        if manifest.get("schema") != 1:
            raise ValueError("unsupported manifest schema")
        self.root = root.resolve(strict=True)
        self.budget = budget_bytes
        self.groups = defaultdict(list)
        self.fds = {}
        self.lru = OrderedDict()
        self.resident = 0
        self.peak_resident = 0
        self.hits = self.misses = self.evictions = self.bytes_read = 0
        names = set()
        for entry in manifest["segments"]:
            key = (entry["layer"], entry["expert"])
            if not (type(key[0]) is int and type(key[1]) is int
                    and key[0] >= 0 and key[1] >= 0):
                raise ValueError("invalid layer/expert")
            if entry["name"] in names:
                raise ValueError("duplicate segment name")
            names.add(entry["name"])
            offset, length = entry["offset"], entry["length"]
            if (type(offset) is not int or type(length) is not int
                    or offset < 0 or length <= 0):
                raise ValueError("invalid byte range")
            rel = Path(entry["file"])
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("manifest file must be relative to model dir")
            full = (self.root / rel).resolve()
            if not full.is_relative_to(self.root) or not full.is_file():
                raise ValueError("unsafe or missing checkpoint shard")
            self.groups[key].append(entry)

    def _fd(self, file: str) -> int:
        if file not in self.fds:
            self.fds[file] = os.open(self.root / file, os.O_RDONLY)
        return self.fds[file]

    def get(self, layer: int, expert: int) -> dict[str, bytes]:
        key = (layer, expert)
        if key in self.lru:
            self.hits += 1
            self.lru.move_to_end(key)
            return self.lru[key]
        if key not in self.groups:
            raise KeyError(f"no expert (layer={layer}, expert={expert})")
        parts = self.groups[key]
        size = sum(entry["length"] for entry in parts)
        if size > self.budget:
            raise ValueError(f"single expert {key} requires {size} bytes; "
                             f"budget only {self.budget}")
        # Evict *before* fetching to avoid exceeding the retained cache budget.
        while self.resident + size > self.budget:
            _, old = self.lru.popitem(last=False)
            self.resident -= sum(len(b) for b in old.values())
            self.evictions += 1
        loaded = {}
        for entry in parts:
            data = read_exact(self._fd(entry["file"]), entry["offset"], entry["length"])
            loaded[entry["name"]] = data
            self.bytes_read += len(data)
        self.lru[key] = loaded
        self.resident += size
        self.peak_resident = max(self.peak_resident, self.resident)
        self.misses += 1
        return loaded

    def stats(self) -> dict:
        requests = self.hits + self.misses
        return {
            "requests": requests, "hits": self.hits, "misses": self.misses,
            "hit_rate": self.hits / requests if requests else 0.0,
            "evictions": self.evictions, "bytes_read": self.bytes_read,
            "resident_payload_bytes": self.resident,
            "peak_retained_payload_bytes": self.peak_resident,
            "budget_bytes": self.budget,
        }

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def replay(root: Path, manifest: dict, trace: Path, budget_bytes: int) -> dict:
    digest = hashlib.sha256()
    start = time.perf_counter()
    tokens = layers = 0
    with ExpertCache(root, manifest, budget_bytes) as cache:
        with trace.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                layer = item["layer"]
                experts = item["experts"]
                if type(layer) is not int or not isinstance(experts, list):
                    raise ValueError(f"bad trace line {line_no}")
                if len(experts) != len(set(experts)):
                    raise ValueError(f"duplicate expert on trace line {line_no}")
                for expert in experts:
                    if type(expert) is not int:
                        raise ValueError(f"invalid expert on line {line_no}")
                    for name, data in sorted(cache.get(layer, expert).items()):
                        digest.update(name.encode("utf-8"))
                        digest.update(len(data).to_bytes(8, "little"))
                        digest.update(data)  # full-byte checksum verifies I/O
                layers += 1
                if item.get("layer") == 0:
                    tokens += 1
        result = cache.stats()
    result.update({
        "layer_steps": layers, "layer_zero_steps": tokens,
        "elapsed_seconds": time.perf_counter() - start,
        "checksum_sha256": digest.hexdigest(),
        "note": "I/O replay only, not model tokens/s; OS page cache may affect timing",
    })
    return result


def write_fixture(dest: Path):
    """Tiny deterministic split-expert checkpoint and hot/cold routing trace."""
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("fixture destination must be empty")
    dest.mkdir(parents=True, exist_ok=True)
    offsets, payload = {}, bytearray()
    for layer in range(3):
        for expert in range(8):
            for projection in ("w1", "w2", "w3"):
                name = f"layers.{layer}.ffn.experts.{expert}.{projection}.weight"
                blob = bytes([layer * 30 + expert * 3 + int(projection[-1])]) * 1024
                start = len(payload)
                payload.extend(blob)
                offsets[name] = {"dtype": "U8", "shape": [1024],
                                 "data_offsets": [start, len(payload)]}
    header = json.dumps(offsets, separators=(",", ":")).encode()
    (dest / "toy.safetensors").write_bytes(
        struct.pack("<Q", len(header)) + header + payload)
    with (dest / "trace.jsonl").open("w", encoding="utf-8") as out:
        for token in range(12):
            for layer in range(3):
                experts = [0, 1] if token % 4 else [2, 3]
                out.write(json.dumps({"layer": layer, "experts": experts}) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("model_dir", type=Path)
    scan.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("replay")
    run.add_argument("model_dir", type=Path)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--trace", type=Path, required=True)
    run.add_argument("--budget-mib", type=float, required=True)
    demo = sub.add_parser("fixture")
    demo.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "fixture":
            write_fixture(args.out)
            print(json.dumps({"fixture": str(args.out)}))
        elif args.command == "scan":
            manifest = scan_safetensors(args.model_dir)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(manifest, indent=2) + "\n",
                                   encoding="utf-8")
            print(json.dumps({"tensors": len(manifest["segments"]),
                              "experts": len({(e["layer"], e["expert"])
                                              for e in manifest["segments"]})}))
        else:
            if not (0 < args.budget_mib < float("inf")):
                raise ValueError("budget-mib must be finite and positive")
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            print(json.dumps(replay(args.model_dir, manifest, args.trace,
                                    int(args.budget_mib * 1024 * 1024)),
                             indent=2))
    except (OSError, ValueError, KeyError, EOFError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
