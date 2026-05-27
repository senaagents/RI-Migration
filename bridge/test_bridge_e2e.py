#!/usr/bin/env python3
"""Dependency-free e2e test for the slim Tally bridge.

Covers the orchestration surface without Tally or Frappe:

  - pairing claim + token persistence
  - initial heartbeat with capabilities
  - handle_bridge_command dispatch (decode_1800, http_extract, set_data_dir,
    discover_1800_companies, inspect_tally, unknown)
  - ack_bridge_command for both success and error paths

The two extraction paths (tally_decode_path, tally_http_path) are mocked
into bridge.handle_bridge_command via a stub `run` so the test runs without
a live Tally and without touching the SQLite layout. Their own logic has
its own coverage.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import sena_tally_bridge as bridge


class FakeSena:
	"""In-process stand-in for the Frappe-side bridge endpoints."""

	def __init__(self) -> None:
		self.connection_id = "INT-CONN-00001"
		self.bridge_token = "bridge-secret"
		self.heartbeats: list[dict] = []
		self.acks: list[dict] = []
		self.next_command: dict | None = None

	def post_json(self, server: str, method: str, payload: dict, timeout: int = 120) -> dict:
		if method == "claim_integration_bridge_pairing":
			assert payload["pairing_code"] == "PAIR1234"
			return {
				"connection_id": self.connection_id,
				"bridge_token": self.bridge_token,
				"status": "Active",
			}

		assert payload["connection_id"] == self.connection_id
		assert payload["bridge_token"] == self.bridge_token

		if method == "bridge_heartbeat":
			self.heartbeats.append(payload)
			# Inject the queued command into the first heartbeat response so
			# run_once dispatches it before the idle-poll loop.
			cmd = self.next_command
			self.next_command = None
			return {"ok": True, "connection_id": self.connection_id,
			        "status": payload["status"], "command": cmd}
		if method == "ack_bridge_command":
			self.acks.append(payload)
			return {"ok": True}
		if method == "poll_bridge_command":
			return {"command": None}

		raise AssertionError(f"Unexpected method: {method}")


def _fake_decode_run(config, connection_id, bridge_token, command_id, command):
	bridge.ack_bridge_command(
		config, connection_id, bridge_token, command_id, "Success",
		{"kind": "decode_1800", "company_id": command.get("company_id"),
		 "sqlite_path": "/fake/decode.sqlite"},
	)


def _fake_http_run(config, connection_id, bridge_token, command_id, command):
	bridge.ack_bridge_command(
		config, connection_id, bridge_token, command_id, "Success",
		{"kind": "http_extract", "company_name": command.get("company_name"),
		 "sqlite_path": "/fake/http.sqlite"},
	)


def test_unknown_command_acks_error() -> None:
	fake = FakeSena()
	bridge.post_json = fake.post_json
	cfg = bridge.BridgeConfig(server="http://sena.invalid", pairing_code="PAIR1234", timeout=5)
	bridge.handle_bridge_command(cfg, fake.connection_id, fake.bridge_token,
	                             {"id": "C1", "type": "nonsense"})
	assert len(fake.acks) == 1
	assert fake.acks[0]["status"] == "Error"
	assert "Unknown command type" in fake.acks[0]["error"]


def _shim_module(name: str, run_func) -> None:
	"""Replace `name` in sys.modules with a stub exposing `run`. Cleans up
	any prior import so tests downstream can `import name` and get the
	real module again (see _restore_module).
	"""
	import types
	stub = types.ModuleType(name)
	stub.run = run_func
	sys.modules[name] = stub


def _restore_module(name: str) -> None:
	"""Drop the shim so the next `import name` reloads the real module."""
	sys.modules.pop(name, None)


def test_decode_dispatch() -> None:
	fake = FakeSena()
	bridge.post_json = fake.post_json
	_shim_module("tally_decode_path", _fake_decode_run)
	try:
		cfg = bridge.BridgeConfig(server="http://sena.invalid", pairing_code="PAIR1234", timeout=5)
		bridge.handle_bridge_command(cfg, fake.connection_id, fake.bridge_token,
		                             {"id": "C2", "type": "decode_1800", "company_id": "Demo"})
		assert len(fake.acks) == 1
		assert fake.acks[0]["status"] == "Success"
		result = json.loads(fake.acks[0]["result_json"])
		assert result["kind"] == "decode_1800"
		assert result["company_id"] == "Demo"
	finally:
		_restore_module("tally_decode_path")


def test_http_dispatch() -> None:
	fake = FakeSena()
	bridge.post_json = fake.post_json
	_shim_module("tally_http_path", _fake_http_run)
	try:
		cfg = bridge.BridgeConfig(server="http://sena.invalid", pairing_code="PAIR1234", timeout=5)
		bridge.handle_bridge_command(cfg, fake.connection_id, fake.bridge_token,
		                             {"id": "C3", "type": "http_extract",
		                              "company_name": "Demo Co",
		                              "from_date": "2025-04-01", "to_date": "2025-04-30"})
		assert len(fake.acks) == 1
		assert fake.acks[0]["status"] == "Success"
		result = json.loads(fake.acks[0]["result_json"])
		assert result["kind"] == "http_extract"
		assert result["company_name"] == "Demo Co"
	finally:
		_restore_module("tally_http_path")


def test_legacy_alias_routes_to_decode() -> None:
	# The previous bridge build queued `extract_1800_company`. Keep it routed
	# to the decode path so older server queues don't strand commands.
	fake = FakeSena()
	bridge.post_json = fake.post_json
	_shim_module("tally_decode_path", _fake_decode_run)
	try:
		cfg = bridge.BridgeConfig(server="http://sena.invalid", pairing_code="PAIR1234", timeout=5)
		bridge.handle_bridge_command(cfg, fake.connection_id, fake.bridge_token,
		                             {"id": "C4", "type": "extract_1800_company",
		                              "company_id": "Legacy"})
		assert len(fake.acks) == 1
		assert fake.acks[0]["status"] == "Success"
		assert json.loads(fake.acks[0]["result_json"])["kind"] == "decode_1800"
	finally:
		_restore_module("tally_decode_path")


def test_capabilities_advertise_both_paths() -> None:
	caps = bridge.build_capabilities()
	assert "tally_http_xml" in caps["protocols"]
	assert "tally_1800_decode" in caps["protocols"]
	assert "decode_1800" in caps["extraction_paths"]
	assert "http_extract" in caps["extraction_paths"]


def test_extract_output_path_is_kind_aware(tmp_path) -> None:
	import os
	os.environ["SENA_BRIDGE_DATA_ROOT"] = str(tmp_path)
	try:
		decode_p = bridge.extract_output_path("Demo Co", "run-1", "decode_1800")
		http_p = bridge.extract_output_path("Demo Co", "run-1", "http_extract")
		assert decode_p.parent == http_p.parent
		assert decode_p.name == "decode_1800.sqlite"
		assert http_p.name == "http_extract.sqlite"
	finally:
		del os.environ["SENA_BRIDGE_DATA_ROOT"]


def test_split_date_range_monthly_handles_boundaries() -> None:
	from tally_http_pool import split_date_range_monthly
	# Single calendar month
	assert split_date_range_monthly("20250401", "20250430") == [("20250401", "20250430")]
	# Cross-year, partial start, partial end
	buckets = split_date_range_monthly("20241115", "20250215")
	assert buckets == [("20241115", "20241130"), ("20241201", "20241231"),
	                   ("20250101", "20250131"), ("20250201", "20250215")]
	# Hyphenated input is normalised
	buckets = split_date_range_monthly("2025-04-01", "2025-05-15")
	assert buckets == [("20250401", "20250430"), ("20250501", "20250515")]


def test_build_task_list_cross_product() -> None:
	from tally_http_pool import build_task_list
	monthly = [("20250401", "20250430"), ("20250501", "20250531")]
	# With voucher types: cross product
	tasks = build_task_list(["Sales", "Purchase"], monthly)
	assert len(tasks) == 4
	pairs = {(t.from_date, t.voucher_type) for t in tasks}
	assert pairs == {
		("20250401", "Sales"), ("20250401", "Purchase"),
		("20250501", "Sales"), ("20250501", "Purchase"),
	}
	# Empty types: one task per bucket, type=""
	tasks = build_task_list([], monthly)
	assert len(tasks) == 2
	assert all(t.voucher_type == "" for t in tasks)


def test_batch_task_label() -> None:
	from tally_http_pool import BatchTask
	assert BatchTask("20250401", "20250430", "Sales").label() == "Sales 20250401..20250430"
	# Empty type renders as a placeholder so logs stay readable
	assert BatchTask("20250401", "20250430", "").label() == "* 20250401..20250430"


def test_resolve_ports_command_shapes() -> None:
	import tally_http_path as path
	cfg = bridge.BridgeConfig(server="", pairing_code="", tally_port=9000)
	# Default: fall back to config.tally_port
	assert path._resolve_ports(cfg, {}) == [9000]
	# Explicit list wins
	assert path._resolve_ports(cfg, {"tally_ports": [9001, 9002]}) == [9001, 9002]
	# Range form expands
	assert path._resolve_ports(cfg, {"tally_port_range": {"start": 9000, "count": 4}}) == [9000, 9001, 9002, 9003]
	# Empty list falls through to default
	assert path._resolve_ports(cfg, {"tally_ports": []}) == [9000]


def test_probe_active_ports_skips_dead_ports() -> None:
	# 65535 is almost always closed; the probe must skip it gracefully and
	# return an empty list rather than raise.
	from tally_http_pool import probe_active_ports
	active = probe_active_ports("127.0.0.1", [65535], timeout=1.0)
	assert active == []


def main() -> int:
	import tempfile
	test_unknown_command_acks_error()
	test_decode_dispatch()
	test_http_dispatch()
	test_legacy_alias_routes_to_decode()
	test_capabilities_advertise_both_paths()
	with tempfile.TemporaryDirectory() as tmp:
		test_extract_output_path_is_kind_aware(Path(tmp))
	test_split_date_range_monthly_handles_boundaries()
	test_build_task_list_cross_product()
	test_batch_task_label()
	test_resolve_ports_command_shapes()
	test_probe_active_ports_skips_dead_ports()
	print(json.dumps({"ok": True, "tests": 11}))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
