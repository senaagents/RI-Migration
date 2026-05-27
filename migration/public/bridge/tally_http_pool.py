#!/usr/bin/env python3
"""Parallel HTTP/XML extractor for Tally.

The HTTP option pulls masters and vouchers from one or more TallyPrime
instances over the TDL/XML interface. TallyPrime's HTTP server is
single-threaded *per instance*, so parallelism = multiple instances on
multiple ports (9000, 9001, …), one worker each.

This module owns the parallel layer:

  - ``probe_active_ports``    — probe a list of ports, return the responsive ones
  - ``BatchTask``             — one (date-range, voucher-type) unit of work
  - ``split_date_range_monthly`` / ``build_task_list`` — task generation
  - ``build_voucher_batch_xml`` — the TDL/XML envelope for one batch
  - ``voucher_types_from_db`` — read voucher type names already pulled into the
    masters table so we can split work per (type, month)
  - ``parallel_voucher_extract`` — coordinator: N workers + one writer thread
    drain a task queue into SQLite

The writer thread holds the only SQLite handle; workers push gzipped XML
+ metadata into a queue. That keeps the hot path lock-free and avoids
SQLite's "database is locked" footguns under concurrency.

Failures retry up to ``MAX_BATCH_RETRIES`` times before being persisted
to ``failed_batches`` for inspection.
"""
from __future__ import annotations

import gzip
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from html import escape

from sena_tally_bridge import (
	BridgeConfig,
	clean_xml,
	log,
	post_tally_xml,
	post_tally_xml_with_retries,
)

MAX_BATCH_RETRIES = 3
PROBE_TIMEOUT_S = 3.0
WORKER_POLL_INTERVAL_S = 1.0
PROBE_PAYLOAD = (
	"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>"
	"<TYPE>COLLECTION</TYPE><ID>List of Companies</ID></HEADER></ENVELOPE>"
)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
@dataclass
class BatchTask:
	"""One unit of voucher-pull work — a (date range, voucher type) cell.

	Empty `voucher_type` means "all types in this date range" — used as the
	fallback when masters didn't yield a type list (rare, but keeps the
	extractor productive instead of bailing out).
	"""
	from_date: str   # YYYYMMDD
	to_date: str     # YYYYMMDD
	voucher_type: str = ""
	retry_count: int = 0

	def label(self) -> str:
		vt = self.voucher_type or "*"
		return f"{vt} {self.from_date}..{self.to_date}"


def split_date_range_monthly(from_date: str, to_date: str) -> list[tuple[str, str]]:
	"""Coarse first-pass bucketing: one calendar month per bucket.

	Adaptive bucketing using a pre-count pass is the next iteration; for now
	this gives bounded responses (~one month of vouchers each) and is
	deterministic, which makes resume/retry simple.
	"""
	from datetime import date, timedelta
	def _parse(s: str) -> date:
		s = s.replace("-", "").replace("/", "")
		return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
	start = _parse(from_date)
	end = _parse(to_date)
	if end < start:
		raise ValueError(f"to_date {to_date} is before from_date {from_date}")
	buckets: list[tuple[str, str]] = []
	cur = start
	while cur <= end:
		if cur.month == 12:
			next_month = date(cur.year + 1, 1, 1)
		else:
			next_month = date(cur.year, cur.month + 1, 1)
		bucket_end = min(next_month - timedelta(days=1), end)
		buckets.append((cur.strftime("%Y%m%d"), bucket_end.strftime("%Y%m%d")))
		cur = bucket_end + timedelta(days=1)
	return buckets


def build_task_list(
	voucher_types: list[str],
	monthly_buckets: list[tuple[str, str]],
) -> list[BatchTask]:
	"""Cross every voucher type with every monthly bucket.

	Splitting by (type, month) gives many small tasks instead of a few huge
	ones — better load distribution across N workers, and each task fits
	comfortably in TallyPrime's response buffer (no megabyte XMLs).
	"""
	if not voucher_types:
		return [BatchTask(f, t, "") for f, t in monthly_buckets]
	return [BatchTask(f, t, vt) for f, t in monthly_buckets for vt in voucher_types]


def voucher_types_from_db(conn: sqlite3.Connection) -> list[str]:
	"""Read voucher-type names from the masters table this run wrote earlier.

	Uses what's actually in the company instead of a hardcoded list, so
	custom voucher types (very common in real Tally setups) are covered.
	"""
	try:
		rows = conn.execute(
			"SELECT name FROM masters WHERE object_type = 'Voucher Type' AND name != ''"
			" ORDER BY name"
		).fetchall()
	except sqlite3.Error:
		return []
	return [r[0] for r in rows if r[0]]


# ---------------------------------------------------------------------------
# Per-batch envelope
# ---------------------------------------------------------------------------
def build_voucher_batch_xml(
	from_date: str,
	to_date: str,
	voucher_type: str | None,
	company_name: str,
) -> str:
	"""Build a TDL/XML request that walks the Day Book for one date range.

	Day Book is the canonical all-vouchers-in-range view — Tally serialises
	the full nested tree (header + ledger entries + inventory entries) in
	one response. Restricting via VoucherTypeName keeps responses small.
	EXPLODEFLAG=Yes triggers full child-list expansion.
	"""
	statics = {
		"SVCURRENTCOMPANY": company_name,
		"SVFROMDATE": from_date,
		"SVTODATE": to_date,
		"SVEXPORTFORMAT": "$$SysName:XML",
	}
	if voucher_type:
		statics["VoucherTypeName"] = voucher_type
	static_xml = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in statics.items())
	return (
		"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST>"
		"<TYPE>Data</TYPE><ID>Day Book</ID></HEADER>"
		f"<BODY><DESC><STATICVARIABLES>{static_xml}<EXPLODEFLAG>Yes</EXPLODEFLAG></STATICVARIABLES>"
		"</DESC></BODY></ENVELOPE>"
	)


# ---------------------------------------------------------------------------
# Port probing
# ---------------------------------------------------------------------------
def probe_active_ports(host: str, ports: list[int], timeout: float = PROBE_TIMEOUT_S) -> list[int]:
	"""Return the subset of `ports` where a TallyPrime HTTP server answered.

	Cheap "List of Companies" probe — few hundred bytes, no auth. We do
	this serially (N is small, ports already fast on localhost/LAN); the
	point isn't speed, it's not starting a worker against a dead port.
	"""
	active: list[int] = []
	for port in ports:
		cfg = BridgeConfig(server="", pairing_code="", tally_host=host,
		                   tally_port=port, timeout=int(timeout))
		try:
			response = post_tally_xml(cfg, PROBE_PAYLOAD)
			if response and "ENVELOPE" in response:
				active.append(port)
				log(f"  port {port}: Tally up")
			else:
				log(f"  port {port}: empty response, skipping")
		except Exception as exc:  # noqa: BLE001
			log(f"  port {port}: not responding ({exc})")
	return active


# ---------------------------------------------------------------------------
# Worker + writer threads
# ---------------------------------------------------------------------------
def _worker(
	worker_id: int,
	config: BridgeConfig,
	port: int,
	task_q: "queue.Queue[BatchTask | None]",
	result_q: "queue.Queue[dict]",
	company_name: str,
	stop_event: threading.Event,
) -> None:
	"""Drain tasks from `task_q`, POST to Tally, push results to `result_q`.

	Tasks failing transiently (network blip, Tally hung mid-walk) are
	requeued up to MAX_BATCH_RETRIES times. After that they go to the
	writer as a fail event, which persists them to `failed_batches`.
	"""
	worker_config = replace(config, tally_port=port)
	while not stop_event.is_set():
		try:
			task = task_q.get(timeout=WORKER_POLL_INTERVAL_S)
		except queue.Empty:
			continue
		if task is None:
			task_q.task_done()
			break
		try:
			payload = build_voucher_batch_xml(
				task.from_date, task.to_date, task.voucher_type or None, company_name,
			)
			started = time.time()
			xml_text = post_tally_xml_with_retries(worker_config, payload, attempts=1)
			elapsed = time.time() - started
			cleaned = clean_xml(xml_text)
			# Cheap voucher count without a full parse — count VOUCHER tag opens.
			voucher_count = cleaned.count("<VOUCHER ") + cleaned.count("<VOUCHER>")
			xml_gz = gzip.compress(cleaned.encode("utf-8"))
			result_q.put({
				"kind": "ok",
				"task": task,
				"port": port,
				"elapsed": elapsed,
				"voucher_count": voucher_count,
				"xml_gz": xml_gz,
			})
		except Exception as exc:  # noqa: BLE001
			if task.retry_count < MAX_BATCH_RETRIES:
				retried = replace(task, retry_count=task.retry_count + 1)
				log(f"[w{worker_id}:{port}] {task.label()} failed (retry {retried.retry_count}/{MAX_BATCH_RETRIES}): {exc}")
				task_q.put(retried)
				result_q.put({"kind": "retry", "task": retried, "error": str(exc)})
			else:
				log(f"[w{worker_id}:{port}] {task.label()} FAILED after {MAX_BATCH_RETRIES} retries: {exc}")
				result_q.put({"kind": "fail", "task": task, "error": str(exc)})
		finally:
			task_q.task_done()


def _writer_loop(
	sqlite_path,
	result_q: "queue.Queue[dict]",
	stop_event: threading.Event,
	out_summary: dict,
) -> None:
	"""Single-thread SQLite writer. Owns its own connection — workers never
	touch the DB. Runs until `stop_event` is set AND the queue is drained.
	"""
	conn = sqlite3.connect(sqlite_path)
	try:
		summary = {"ok_count": 0, "retry_count": 0, "fail_count": 0,
		           "voucher_count": 0, "bytes_gz": 0,
		           "elapsed_total_s": 0.0}
		while not (stop_event.is_set() and result_q.empty()):
			try:
				r = result_q.get(timeout=WORKER_POLL_INTERVAL_S)
			except queue.Empty:
				continue
			try:
				kind = r["kind"]
				if kind == "ok":
					task = r["task"]
					batch_id = uuid.uuid4().hex[:16]
					conn.execute(
						"INSERT INTO voucher_batches(batch_id, from_date, to_date, voucher_type, "
						"port, bytes, voucher_count, xml_gz, pulled_at) "
						"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
						(batch_id, task.from_date, task.to_date, task.voucher_type,
						 r["port"], len(r["xml_gz"]), r["voucher_count"],
						 r["xml_gz"], time.strftime("%Y-%m-%dT%H:%M:%S")),
					)
					conn.commit()
					summary["ok_count"] += 1
					summary["voucher_count"] += r["voucher_count"]
					summary["bytes_gz"] += len(r["xml_gz"])
					summary["elapsed_total_s"] += r.get("elapsed", 0.0)
				elif kind == "retry":
					summary["retry_count"] += 1
				elif kind == "fail":
					task = r["task"]
					conn.execute(
						"INSERT INTO failed_batches(from_date, to_date, voucher_type, "
						"retry_count, error, failed_at) VALUES (?, ?, ?, ?, ?, ?)",
						(task.from_date, task.to_date, task.voucher_type,
						 task.retry_count, r["error"],
						 time.strftime("%Y-%m-%dT%H:%M:%S")),
					)
					conn.commit()
					summary["fail_count"] += 1
			finally:
				result_q.task_done()
		out_summary.update(summary)
	finally:
		conn.close()


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------
def parallel_voucher_extract(
	config: BridgeConfig,
	sqlite_path,
	company_name: str,
	from_date: str,
	to_date: str,
	ports: list[int],
	voucher_types: list[str],
	progress_cb=None,
) -> dict:
	"""Run N workers (one per port) against a shared task queue, draining
	results to SQLite via a single writer thread. Returns a summary dict.

	`progress_cb(done, total, summary)` is called every few seconds with
	the running totals so the bridge can heartbeat progress to Sena.
	"""
	if not ports:
		raise RuntimeError("parallel_voucher_extract requires at least one active port")
	monthly = split_date_range_monthly(from_date, to_date)
	tasks = build_task_list(voucher_types, monthly)
	total_tasks = len(tasks)
	log(f"Parallel extract: {total_tasks} tasks (×{len(monthly)} months × "
	    f"{max(1, len(voucher_types))} types) across {len(ports)} workers")

	task_q: queue.Queue[BatchTask | None] = queue.Queue()
	result_q: queue.Queue[dict] = queue.Queue()
	for task in tasks:
		task_q.put(task)

	stop_event = threading.Event()
	writer_summary: dict = {}
	writer_t = threading.Thread(
		target=_writer_loop,
		args=(sqlite_path, result_q, stop_event, writer_summary),
		daemon=True,
		name="sena-tally-writer",
	)
	writer_t.start()

	worker_threads: list[threading.Thread] = []
	for i, port in enumerate(ports):
		t = threading.Thread(
			target=_worker,
			args=(i, config, port, task_q, result_q, company_name, stop_event),
			daemon=True,
			name=f"sena-tally-worker-{i}",
		)
		t.start()
		worker_threads.append(t)

	# Watch progress from the main thread until all tasks complete.
	last_log = time.time()
	while True:
		# task_q.unfinished_tasks counts puts minus task_dones; retries put
		# the task back, so this naturally accounts for them.
		remaining = task_q.unfinished_tasks
		done = total_tasks - remaining + writer_summary.get("retry_count", 0)
		# Cap at total — retries inflate the diff temporarily.
		done = min(done, total_tasks)
		if remaining <= 0:
			break
		if time.time() - last_log >= 5:
			ok = writer_summary.get("ok_count", 0)
			fail = writer_summary.get("fail_count", 0)
			vc = writer_summary.get("voucher_count", 0)
			log(f"  progress: {ok} ok, {fail} fail, {vc:,} vouchers, {remaining} tasks remaining")
			if progress_cb:
				try:
					progress_cb(done, total_tasks, dict(writer_summary))
				except Exception:  # noqa: BLE001 — heartbeat must never break extract
					pass
			last_log = time.time()
		time.sleep(0.5)

	# Ask each worker to exit by feeding poison pills.
	for _ in ports:
		task_q.put(None)
	for t in worker_threads:
		t.join(timeout=5)

	# Stop writer once result_q has drained.
	stop_event.set()
	writer_t.join(timeout=15)

	writer_summary["total_tasks"] = total_tasks
	writer_summary["worker_count"] = len(ports)
	writer_summary["ports"] = list(ports)
	return writer_summary
