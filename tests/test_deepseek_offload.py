"""Disk expert cache tests using a reproducible, tiny safetensors checkpoint."""
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from deepseek_offload.moe_offload import (
    ExpertCache,
    read_exact,
    replay,
    scan_safetensors,
    write_fixture,
)


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "fixture"
    write_fixture(root)
    return root


def test_header_scan_and_byte_exact_loading(toy):
    manifest = scan_safetensors(toy)
    assert manifest["schema"] == 1
    assert len(manifest["segments"]) == 72
    assert len({(e["layer"], e["expert"]) for e in manifest["segments"]}) == 24
    with ExpertCache(toy, manifest, 12 * 1024) as cache:
        loaded = cache.get(1, 2)
        assert set(loaded) == {
            f"layers.1.ffn.experts.2.w{x}.weight" for x in (1, 2, 3)
        }
        assert loaded["layers.1.ffn.experts.2.w1.weight"] == bytes([37]) * 1024
        assert cache.get(1, 2) is loaded
        assert cache.stats()["hits"] == 1
        assert cache.stats()["misses"] == 1


def test_replay_bounded_cache_and_hash_consistency(toy):
    manifest = scan_safetensors(toy)
    trace = toy / "trace.jsonl"
    small = replay(toy, manifest, trace, 9 * 1024)
    large = replay(toy, manifest, trace, 128 * 1024)
    assert small["checksum_sha256"] == large["checksum_sha256"]
    assert small["layer_steps"] == large["layer_steps"] == 36
    assert large["hits"] > small["hits"]
    assert large["bytes_read"] < small["bytes_read"]
    assert small["evictions"] > 0
    assert small["peak_retained_payload_bytes"] <= small["budget_bytes"]
    assert large["peak_retained_payload_bytes"] <= large["budget_bytes"]


def test_too_small_budget_fails_without_unbounded_cache(toy):
    manifest = scan_safetensors(toy)
    with ExpertCache(toy, manifest, 2048) as cache:
        with pytest.raises(ValueError, match="single expert"):
            cache.get(0, 0)


def test_rejects_manifest_path_escape(toy, tmp_path):
    manifest = scan_safetensors(toy)
    manifest["segments"][0]["file"] = "../outside.safetensors"
    with pytest.raises(ValueError, match="manifest file"):
        ExpertCache(toy, manifest, 8192)


def test_rejects_corrupt_and_missing_split_tensors(toy):
    checkpoint = toy / "toy.safetensors"
    original = checkpoint.read_bytes()
    checkpoint.write_bytes(struct.pack("<Q", 2**40) + original[8:])
    with pytest.raises(ValueError, match="header length"):
        scan_safetensors(toy)
    checkpoint.write_bytes(original)
    (toy / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {
            "layers.100.ffn.experts.0.w1.weight": "missing.safetensors"}})
    )
    with pytest.raises(ValueError, match="incomplete"):
        scan_safetensors(toy)


def test_rejects_duplicate_expert_per_step(toy):
    manifest = scan_safetensors(toy)
    trace = toy / "bad_trace.jsonl"
    trace.write_text(json.dumps({"layer": 0, "experts": [0, 0]}) + "\n")
    with pytest.raises(ValueError, match="duplicate expert"):
        replay(toy, manifest, trace, 16 * 1024)


def test_exact_read_range_and_cli(toy, tmp_path):
    with (toy / "toy.safetensors").open("rb") as f:
        assert read_exact(f.fileno(), 0, 8) != b""
    manifest_path = tmp_path / "manifest.json"
    proc = subprocess.run(
        [sys.executable, "-m", "deepseek_offload.moe_offload", "scan",
         str(toy), "--output", str(manifest_path)],
        text=True, capture_output=True, check=True,
    )
    assert json.loads(proc.stdout)["experts"] == 24
    proc = subprocess.run(
        [sys.executable, "-m", "deepseek_offload.moe_offload", "replay",
         str(toy), "--manifest", str(manifest_path),
         "--trace", str(toy / "trace.jsonl"), "--budget-mib", "0.01"],
        text=True, capture_output=True, check=True,
    )
    assert json.loads(proc.stdout)["requests"] == 72
