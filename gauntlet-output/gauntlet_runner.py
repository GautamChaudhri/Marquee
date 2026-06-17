#!/usr/bin/env python3
"""Marquee gauntlet runner.

This is intentionally an integration gauntlet, not a unit test suite. It talks
to a running Marquee API, exercises the public endpoint surface against the lab
movie set, records expected validation failures separately from genuine
failures, and keeps GPU-heavy phases serialized.

Environment knobs:
  MARQUEE_BASE_URL=http://localhost:3165
  SUBGEN_URL=http://localhost:9000
  GAUNTLET_OUTPUT_DIR=/forge/Marquee/gauntlet-output
  GAUNTLET_PIPELINE_SCOPE=representative  # smoke | representative | all
  GAUNTLET_PIPELINE_TIMEOUT_SECONDS=1800
  GAUNTLET_SUBGEN_TIMEOUT_SECONDS=1800
  GAUNTLET_CONFIRM_SUBTITLE_MUTATIONS=0
  GAUNTLET_APPLY_LETTERBOX=1
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASE_URL = os.getenv("MARQUEE_BASE_URL", "http://localhost:3165").rstrip("/")
SUBGEN_URL = os.getenv("SUBGEN_URL", "http://localhost:9000").rstrip("/")
OUTPUT_DIR = Path(os.getenv("GAUNTLET_OUTPUT_DIR", "/forge/Marquee/gauntlet-output"))
RESULTS_FILE = OUTPUT_DIR / "gauntlet_results.jsonl"
ERRORS_FILE = OUTPUT_DIR / "gauntlet_errors.jsonl"
SUMMARY_FILE = OUTPUT_DIR / "gauntlet_summary.json"
ENDPOINTS_FILE = OUTPUT_DIR / "endpoint_inventory.json"
GPU_FILE = OUTPUT_DIR / "gpu_snapshots.jsonl"
DB_DELTA_FILE = OUTPUT_DIR / "db_delta_report.json"
DB_PATH = Path(os.getenv("GAUNTLET_DB_PATH", "/forge/Marquee/data/marquee.db"))

PIPELINE_SCOPE = os.getenv("GAUNTLET_PIPELINE_SCOPE", "representative").strip().lower()
PIPELINE_TIMEOUT = int(os.getenv("GAUNTLET_PIPELINE_TIMEOUT_SECONDS", "1800"))
TASTE_TIMEOUT = int(os.getenv("GAUNTLET_TASTE_TIMEOUT_SECONDS", "1800"))
SUBGEN_TIMEOUT = int(os.getenv("GAUNTLET_SUBGEN_TIMEOUT_SECONDS", "1800"))
CONFIRM_SUBTITLE_MUTATIONS = os.getenv("GAUNTLET_CONFIRM_SUBTITLE_MUTATIONS", "0") == "1"
APPLY_LETTERBOX = os.getenv("GAUNTLET_APPLY_LETTERBOX", "1") == "1"
RUN_SYNC = os.getenv("GAUNTLET_RUN_SYNC", "1") == "1"
RUN_WEBHOOK_DOWNLOAD = os.getenv("GAUNTLET_WEBHOOK_DOWNLOAD", "0") == "1"
WEBHOOK_TOKEN = os.getenv("MARQUEE_WEBHOOK_TOKEN")

JSON_SENTINEL = object()
EXPECTED_MUTATION_TABLES = {
    "letterbox_events",
    "media_batches",
    "media_job_events",
    "media_jobs",
    "pipeline_runs",
    "subtitle_inventories",
    "subtitle_tracks",
}


# Lab movie fixtures. These mirror the curated gauntlet set: mixed age,
# resolution directory, container, subtitle sidecar coverage, and genres.
MOVIES: dict[int, dict[str, Any]] = {
    1: {"title": "2001: A Space Odyssey", "year": 1968, "tmdb_id": 62, "radarr_id": 519, "media_file_id": 1, "container": "mkv", "dir": "4K"},
    2: {"title": "A Quiet Place: Day One", "year": 2024, "tmdb_id": 762441, "radarr_id": 520, "media_file_id": 2, "container": "mkv", "dir": "4K"},
    3: {"title": "Alien: Romulus", "year": 2024, "tmdb_id": 945961, "radarr_id": 521, "media_file_id": 3, "container": "mkv", "dir": "4K"},
    5: {"title": "Arrival", "year": 2016, "tmdb_id": 329865, "radarr_id": 523, "media_file_id": 5, "container": "mkv", "dir": "4K"},
    12: {"title": "Captain America: Brave New World", "year": 2025, "tmdb_id": 822119, "radarr_id": 530, "media_file_id": 12, "container": "mkv", "dir": "4K"},
    15: {"title": "Color Out of Space", "year": 2020, "tmdb_id": 548473, "radarr_id": 533, "media_file_id": 15, "container": "mp4", "dir": "4K"},
    25: {"title": "Dune: Part Two", "year": 2024, "tmdb_id": 693134, "radarr_id": 543, "media_file_id": 25, "container": "mkv", "dir": "4K"},
    31: {"title": "Furiosa: A Mad Max Saga", "year": 2024, "tmdb_id": 786892, "radarr_id": 549, "media_file_id": 31, "container": "mkv", "dir": "4K"},
    36: {"title": "Hot Fuzz", "year": 2007, "tmdb_id": 4638, "radarr_id": 554, "media_file_id": 36, "container": "mkv", "dir": "4K"},
    52: {"title": "Moana 2", "year": 2024, "tmdb_id": 1241982, "radarr_id": 571, "media_file_id": 52, "container": "mkv", "dir": "4K"},
    61: {"title": "Reservoir Dogs", "year": 1992, "tmdb_id": 500, "radarr_id": 580, "media_file_id": 61, "container": "mp4", "dir": "4K"},
    73: {"title": "The Cabin in the Woods", "year": 2012, "tmdb_id": 22970, "radarr_id": 596, "media_file_id": 73, "container": "mp4", "dir": "4K"},
    148: {"title": "In the Mouth of Madness", "year": 1995, "tmdb_id": 2654, "radarr_id": 681, "media_file_id": 148, "container": "mp4", "dir": "1080p"},
    155: {"title": "Leave No Trace", "year": 2018, "tmdb_id": 443463, "radarr_id": 688, "media_file_id": 155, "container": "mkv", "dir": "1080p"},
    204: {"title": "The Game", "year": 1997, "tmdb_id": 2649, "radarr_id": 739, "media_file_id": 204, "container": "mkv", "dir": "1080p"},
    219: {"title": "The Sixth Sense", "year": 1999, "tmdb_id": 745, "radarr_id": 756, "media_file_id": 219, "container": "mp4", "dir": "4K"},
    224: {"title": "The Zone of Interest", "year": 2023, "tmdb_id": 467244, "radarr_id": 762, "media_file_id": 224, "container": "mkv", "dir": "1080p"},
    314: {"title": "Knives Out", "year": 2019, "tmdb_id": 546554, "radarr_id": 881, "media_file_id": 314, "container": "mp4", "dir": "4K"},
    338: {"title": "Catch Me If You Can", "year": 2002, "tmdb_id": 640, "radarr_id": 911, "media_file_id": 338, "container": "mkv", "dir": "1080p"},
    434: {"title": "Exit 8", "year": 2025, "tmdb_id": 1408208, "radarr_id": 1031, "media_file_id": 431, "container": "mkv", "dir": "1080p"},
    435: {"title": "Lincoln", "year": 2012, "tmdb_id": 72976, "radarr_id": 1032, "media_file_id": 432, "container": "mkv", "dir": "1080p"},
    439: {"title": "Burning", "year": 2018, "tmdb_id": 491584, "radarr_id": 1036, "media_file_id": 436, "container": "mkv", "dir": "1080p"},
    443: {"title": "The Borderlands", "year": 2014, "tmdb_id": 207774, "radarr_id": 1040, "media_file_id": 440, "container": "mkv", "dir": "1080p"},
}

MOVIES_WITH_SRT = [439, 148, 435, 15, 314, 61, 73]
SMALL_MOVIES = [148, 443, 73, 61, 15, 219, 3, 224, 434, 2]
MKV_MOVIES = [movie_id for movie_id, movie in MOVIES.items() if movie["container"] == "mkv"]
MP4_MOVIES = [movie_id for movie_id, movie in MOVIES.items() if movie["container"] == "mp4"]
NONEXISTENT_MOVIE_ID = 99999


@dataclass
class APIResult:
    method: str
    url: str
    status: int
    body: Any
    elapsed: float
    error: str | None = None
    label: str | None = None
    expected: bool = True
    binary: bool = False

    def json(self) -> dict[str, Any]:
        return self.body if isinstance(self.body, dict) else {}


class Gauntlet:
    def __init__(self) -> None:
        self.call_count = 0
        self.unexpected_errors = 0
        self.expected_non_2xx = 0
        self.results_log: list[dict[str, Any]] = []
        self.completed_runs: list[str] = []
        self.failed_runs: list[str] = []
        self.primary_run_id: str | None = None
        self.feedback_events: list[str] = []
        self.inventories: dict[int, dict[str, Any]] = {}
        self.pass_results: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # HTTP + logging
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        data: Any = JSON_SENTINEL,
        expected: set[int] | range | None = None,
        timeout: int = 120,
        label: str | None = None,
        binary: bool = False,
        external: bool = False,
    ) -> APIResult:
        url = path_or_url if external else f"{BASE_URL}{path_or_url}"
        headers: dict[str, str] = {}
        payload = None
        if data is not JSON_SENTINEL:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(data).encode("utf-8")

        status, body, elapsed, error, is_binary = self._request(
            method, url, payload, headers, timeout, binary=binary
        )
        expected_ok = self._expected(status, error, expected)
        self._log(method, url, status, body, elapsed, error, label, expected_ok, is_binary, expected)
        self._print_result(method, path_or_url, status, elapsed, label, expected_ok, error, body)
        return APIResult(method, url, status, body, elapsed, error, label, expected_ok, is_binary)

    def _request(
        self,
        method: str,
        url: str,
        payload: bytes | None,
        headers: dict[str, str],
        timeout: int,
        *,
        binary: bool,
    ) -> tuple[int, Any, float, str | None, bool]:
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                elapsed = time.monotonic() - start
                return resp.status, self._decode_body(raw, resp.headers.get("content-type"), binary), elapsed, None, self._is_binary(resp.headers.get("content-type"), binary)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            elapsed = time.monotonic() - start
            return exc.code, self._decode_body(raw, exc.headers.get("content-type"), binary), elapsed, None, self._is_binary(exc.headers.get("content-type"), binary)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            elapsed = time.monotonic() - start
            return 0, "", elapsed, str(exc), False

    @staticmethod
    def _is_binary(content_type: str | None, forced: bool) -> bool:
        if forced:
            return True
        if not content_type:
            return False
        textish = ("json", "text", "xml", "html", "csv", "srt")
        return not any(token in content_type.lower() for token in textish)

    def _decode_body(self, raw: bytes, content_type: str | None, binary: bool) -> Any:
        if self._is_binary(content_type, binary):
            return {
                "binary": True,
                "content_type": content_type,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text

    @staticmethod
    def _expected(status: int, error: str | None, expected: set[int] | range | None) -> bool:
        if error or status == 0:
            return False
        if expected is None:
            return 200 <= status < 300
        return status in expected

    def _log(
        self,
        method: str,
        url: str,
        status: int,
        body: Any,
        elapsed: float,
        error: str | None,
        label: str | None,
        expected_ok: bool,
        binary: bool,
        expected: set[int] | range | None,
    ) -> None:
        self.call_count += 1
        if status >= 400 and expected_ok:
            self.expected_non_2xx += 1
        if not expected_ok:
            self.unexpected_errors += 1

        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "seq": self.call_count,
            "label": label,
            "method": method,
            "url": url,
            "status": status,
            "expected": self._expected_repr(expected),
            "ok": expected_ok,
            "elapsed_s": round(elapsed, 3),
            "body": self._body_preview(body, binary),
            "error": error,
        }
        with RESULTS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if not expected_ok:
            with ERRORS_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.results_log.append(entry)

    @staticmethod
    def _expected_repr(expected: set[int] | range | None) -> str:
        if expected is None:
            return "2xx"
        if isinstance(expected, range):
            return f"{expected.start}-{expected.stop - 1}"
        return ",".join(str(code) for code in sorted(expected))

    @staticmethod
    def _body_preview(body: Any, binary: bool) -> Any:
        if binary:
            return body
        text = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
        return text[:2000]

    @staticmethod
    def _print_result(
        method: str,
        path: str,
        status: int,
        elapsed: float,
        label: str | None,
        expected_ok: bool,
        error: str | None,
        body: Any,
    ) -> None:
        tag = "OK" if expected_ok and status < 400 else "EXP" if expected_ok else "FAIL"
        label_str = f" [{label}]" if label else ""
        print(f"  {tag:4s} {method:6s} {path} -> {status} ({elapsed:.2f}s){label_str}")
        if error:
            print(f"       ERROR: {error}")
        elif not expected_ok or (status >= 400 and label):
            preview = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
            print(f"       BODY: {preview[:300]}")

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------

    @staticmethod
    def section(title: str) -> None:
        print(f"\n{'=' * 78}")
        print(f"  {title}")
        print(f"{'=' * 78}")

    @staticmethod
    def movie_name(movie_id: int) -> str:
        movie = MOVIES.get(movie_id, {})
        return str(movie.get("title") or movie_id)

    @staticmethod
    def terminal_run_status(status: str | None) -> bool:
        return status in {"completed", "failed", "flagged_manual"}

    @staticmethod
    def terminal_job_status(status: str | None) -> bool:
        return status in {"complete", "completed", "succeeded", "failed", "cancelled", "interrupted"}

    def pipeline_targets(self) -> list[int]:
        if PIPELINE_SCOPE == "all":
            return SMALL_MOVIES + [movie_id for movie_id in MOVIES if movie_id not in SMALL_MOVIES]
        if PIPELINE_SCOPE == "smoke":
            return [148]
        return [1, 5, 25, 36, 52, 31]

    def record_gpu_snapshot(self, label: str) -> dict[str, Any]:
        snapshot = {
            "ts": datetime.now(UTC).isoformat(),
            "label": label,
            "available": False,
            "query": None,
            "compute_apps": None,
            "error": None,
        }
        try:
            query = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            apps = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,process_name,used_memory",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            snapshot["available"] = True
            snapshot["query"] = query.stdout.strip()
            snapshot["compute_apps"] = apps.stdout.strip()
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            snapshot["error"] = str(exc)

        with GPU_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
        print(f"  GPU snapshot [{label}]: {snapshot['query'] or snapshot['error'] or 'none'}")
        return snapshot

    @staticmethod
    def gpu_compute_apps(snapshot: dict[str, Any]) -> list[str]:
        raw = str(snapshot.get("compute_apps") or "").strip()
        return [line.strip() for line in raw.splitlines() if line.strip()]

    @staticmethod
    def marquee_gpu_apps(snapshot: dict[str, Any]) -> list[str]:
        return [
            app
            for app in Gauntlet.gpu_compute_apps(snapshot)
            if "/forge/Marquee" in app or "marquee" in app.lower() or "uvicorn" in app.lower()
        ]

    def release_marquee_gpu_resources(self, label: str) -> APIResult:
        return self.request("POST", "/api/system/release-gpu", label=f"release-gpu-{label}")

    def ensure_gpu_free_for_external_phase(self, label: str, *, timeout: int = 120) -> bool:
        snapshot = self.record_gpu_snapshot(f"{label}-before-release")
        if not snapshot.get("available"):
            return True

        if self.marquee_gpu_apps(snapshot):
            self.release_marquee_gpu_resources(label)

        deadline = time.monotonic() + timeout
        last_apps = self.gpu_compute_apps(snapshot)
        while time.monotonic() < deadline:
            snapshot = self.record_gpu_snapshot(f"{label}-poll")
            apps = self.gpu_compute_apps(snapshot)
            last_apps = apps
            if not apps:
                return True
            time.sleep(5)

        self._manual_failure(f"{label}-gpu-not-free", f"GPU compute processes still active: {last_apps}")
        return False

    @staticmethod
    def db_snapshot() -> dict[str, Any]:
        if not DB_PATH.exists():
            return {"error": f"{DB_PATH} does not exist"}
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row[0] for row in cur.fetchall()]
            snapshot: dict[str, Any] = {}
            for table in tables:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                snapshot[table] = cur.fetchone()[0]
            conn.close()
            return snapshot
        except Exception as exc:  # noqa: BLE001 - snapshot must not abort gauntlet
            return {"error": str(exc)}

    def classify_db_deltas(
        self,
        db_before: dict[str, Any],
        db_after: dict[str, Any],
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "database": str(DB_PATH),
            "expected_tables": sorted(EXPECTED_MUTATION_TABLES),
            "tables": [],
            "unexpected_tables": [],
            "notes": [
                "The gauntlet is a live integration exercise and does not delete durable audit/provenance rows.",
                "Rows in expected tables are classified as intentional test residue; unrelated table deltas need review.",
            ],
        }
        if "error" in db_before or "error" in db_after:
            report["status"] = "snapshot_error"
            report["error"] = {
                "before": db_before.get("error"),
                "after": db_after.get("error"),
            }
            return report

        for table in sorted(set(db_before) | set(db_after)):
            before = db_before.get(table, 0)
            after = db_after.get(table, 0)
            if not isinstance(before, int) or not isinstance(after, int) or before == after:
                continue
            delta = after - before
            expected = table in EXPECTED_MUTATION_TABLES
            entry = {
                "table": table,
                "before": before,
                "after": after,
                "delta": delta,
                "expected": expected,
                "reason": self.db_delta_reason(table, delta) if expected else "Table is not part of the gauntlet's expected mutation set.",
            }
            report["tables"].append(entry)
            if not expected:
                report["unexpected_tables"].append(entry)

        report["status"] = "ok" if not report["unexpected_tables"] else "needs_review"
        report["expected_change_count"] = sum(1 for entry in report["tables"] if entry["expected"])
        report["unexpected_change_count"] = len(report["unexpected_tables"])
        return report

    @staticmethod
    def db_delta_reason(table: str, delta: int) -> str:
        reasons = {
            "letterbox_events": "Letterbox detect/apply/remove/heal endpoints append audit events.",
            "media_batches": "Subtitle generation creates media batch records.",
            "media_job_events": "Subtitle plan/generation jobs append lifecycle events.",
            "media_jobs": "Subtitle plan/generation endpoints create durable job records.",
            "pipeline_runs": "Poster pipeline tests create durable run provenance.",
            "subtitle_inventories": "Subtitle inventory scans persist one inventory per scanned media file.",
            "subtitle_tracks": "Subtitle inventory scans persist discovered embedded and external tracks.",
        }
        direction = "increased" if delta > 0 else "changed"
        return f"{reasons.get(table, 'Expected gauntlet mutation table.')} Row count {direction} by {delta:+d}."

    @staticmethod
    def save_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # Passes
    # ------------------------------------------------------------------

    def pass_baseline(self) -> None:
        self.section("PASS 1: BASELINE, OPENAPI, SYSTEM")
        self.record_gpu_snapshot("baseline-start")
        self.request("GET", "/health", label="health")
        openapi = self.request("GET", "/openapi.json", label="openapi")
        if openapi.status == 200 and isinstance(openapi.body, dict):
            endpoints = []
            for path, methods in sorted(openapi.body.get("paths", {}).items()):
                for method in sorted(methods):
                    endpoints.append({"method": method.upper(), "path": path})
            self.save_json(ENDPOINTS_FILE, {"count": len(endpoints), "endpoints": endpoints})
            print(f"  Endpoint inventory: {len(endpoints)} operations saved to {ENDPOINTS_FILE.name}")
        self.request("GET", "/api/system/status", label="system-status")
        self.request("GET", "/api/system/status/generators", label="generator-status")
        self.request("GET", "/api/config/pipeline", label="pipeline-config")

    def pass_library(self) -> None:
        self.section("PASS 2: LIBRARY MATRIX")
        for page, size in [(1, 1), (1, 10), (2, 10), (1, 50), (1, 200), (999, 100)]:
            self.request("GET", f"/api/library/movies?page={page}&page_size={size}", label=f"movies-page-{page}-{size}")
        for page, size in [(0, 10), (-1, 10), (1, 0), (1, -1), (1, 201)]:
            self.request(
                "GET",
                f"/api/library/movies?page={page}&page_size={size}",
                expected={422},
                label=f"movies-invalid-{page}-{size}",
            )
        for movie_id in MOVIES:
            self.request("GET", f"/api/library/movies/{movie_id}", label=f"movie-{movie_id}")
        self.request("GET", f"/api/library/movies/{NONEXISTENT_MOVIE_ID}", expected={404}, label="movie-404")

        series = self.request("GET", "/api/library/series?page=1&page_size=25", label="series-page")
        series_items = series.json().get("items", [])
        for page, size in [(0, 10), (1, 0), (1, 201)]:
            self.request("GET", f"/api/library/series?page={page}&page_size={size}", expected={422}, label=f"series-invalid-{page}-{size}")
        if series_items:
            series_id = series_items[0]["id"]
            self.request("GET", f"/api/library/series/{series_id}", label="series-detail")
            self.request("GET", f"/api/library/series/{series_id}/seasons", label="series-seasons")
        self.request("GET", "/api/library/series/999999", expected={404}, label="series-404")
        self.request("GET", "/api/library/episodes/1", expected={200, 404}, label="episode-detail")
        self.request("GET", "/api/library/episodes/999999", expected={404}, label="episode-404")

    def pass_sync_webhooks(self) -> None:
        self.section("PASS 3: SYNC AND WEBHOOKS")
        if RUN_SYNC:
            self.request("POST", "/api/sync/all", expected={200, 429}, timeout=600, label="sync-all")
            self.request("POST", "/api/sync/all", expected={429}, timeout=60, label="sync-all-rate-limit")
        else:
            print("  Sync skipped (GAUNTLET_RUN_SYNC=0)")

        radarr_test = {"eventType": "Test"}
        sonarr_test = {"eventType": "Test"}
        ignored = {"eventType": "MovieFileDelete", "movie": {"id": 519, "title": "2001: A Space Odyssey", "tmdbId": 62}}
        rename = {
            "eventType": "Rename",
            "movie": {
                "id": 519,
                "title": "2001: A Space Odyssey",
                "tmdbId": 62,
                "folderPath": "/mnt/lab/movies/4K/2001 - A Space Odyssey (1968)",
            },
        }
        self.request("POST", self._webhook_path("/api/webhooks/radarr"), data=radarr_test, expected={200, 401}, label="webhook-radarr-test")
        self.request("POST", self._webhook_path("/api/webhooks/radarr"), data=ignored, expected={200, 401}, label="webhook-radarr-ignored")
        self.request("POST", self._webhook_path("/api/webhooks/radarr"), data=rename, expected={200, 401}, label="webhook-radarr-rename")
        if RUN_WEBHOOK_DOWNLOAD:
            download = {**rename, "eventType": "Download", "isUpgrade": True}
            self.request("POST", self._webhook_path("/api/webhooks/radarr"), data=download, expected={200, 401}, label="webhook-radarr-download")
        else:
            print("  Radarr Download webhook skipped (GAUNTLET_WEBHOOK_DOWNLOAD=0)")
        self.request("POST", self._webhook_path("/api/webhooks/sonarr"), data=sonarr_test, expected={200, 401}, label="webhook-sonarr-test")
        self.request("POST", self._webhook_path("/api/webhooks/subgen"), data={"status": "complete", "path": "gauntlet"}, expected={200, 401}, label="webhook-subgen-callback")

    @staticmethod
    def _webhook_path(path: str) -> str:
        if not WEBHOOK_TOKEN:
            return path
        return f"{path}?token={urllib.parse.quote(WEBHOOK_TOKEN)}"

    def pass_pipeline(self) -> None:
        self.section("PASS 4: POSTER PIPELINE, RESULTS, RESCORE")
        targets = self.pipeline_targets()
        print(f"  Pipeline scope: {PIPELINE_SCOPE}; targets: {targets}; timeout/run: {PIPELINE_TIMEOUT}s")
        self.record_gpu_snapshot("before-poster-pipeline")

        for index, movie_id in enumerate(targets):
            title = self.movie_name(movie_id)
            run = self.request("POST", f"/api/pipeline/movie/{movie_id}/run", label=f"pipeline-start-{movie_id}")
            if run.status == 409:
                active = run.json().get("detail", {}).get("active_run_id")
                if isinstance(active, str) and active != "taste-profile rebuild":
                    self.poll_run(active, movie_id=None, label_prefix="preexisting")
                continue
            if run.status != 202:
                continue

            run_id = run.json().get("run_id")
            if not run_id:
                self._manual_failure("pipeline-start", f"{title} returned 202 without run_id")
                continue

            if index == 0:
                guard_target = next((mid for mid in targets if mid != movie_id), movie_id)
                self.request(
                    "POST",
                    f"/api/pipeline/movie/{guard_target}/run",
                    expected={409},
                    label="pipeline-concurrency-guard",
                )

            terminal = self.poll_run(run_id, movie_id=movie_id, label_prefix=f"pipeline-{movie_id}")
            if terminal == "completed":
                self.completed_runs.append(run_id)
                if self.primary_run_id is None:
                    self.primary_run_id = run_id
                self.inspect_completed_run(run_id, movie_id)
            else:
                self.failed_runs.append(run_id)

        self.request("POST", f"/api/pipeline/movie/{NONEXISTENT_MOVIE_ID}/run", data={}, expected={404}, label="pipeline-movie-404")
        if targets:
            self.request("POST", f"/api/test/pipeline/movie/{targets[0]}", expected={200, 409}, timeout=PIPELINE_TIMEOUT, label="legacy-test-pipeline")
        self.record_gpu_snapshot("after-poster-pipeline")

    def poll_run(self, run_id: str, *, movie_id: int | None, label_prefix: str) -> str | None:
        deadline = time.monotonic() + PIPELINE_TIMEOUT
        attempt = 0
        last_status: str | None = None
        while time.monotonic() < deadline:
            result = self.request("GET", f"/api/pipeline/runs/{run_id}", label=f"{label_prefix}-status-{attempt}")
            if result.status == 200:
                body = result.json()
                last_status = body.get("status")
                if self.terminal_run_status(last_status):
                    print(f"  Run {run_id} terminal: {last_status}")
                    return last_status
            attempt += 1
            time.sleep(min(5 + attempt * 2, 30))
        self._manual_failure("pipeline-timeout", f"Run {run_id} did not finish within {PIPELINE_TIMEOUT}s; last={last_status}")
        if movie_id is not None:
            self.request("GET", f"/api/movies/{movie_id}/runs", label=f"movie-runs-timeout-{movie_id}")
        return last_status

    def inspect_completed_run(self, run_id: str, movie_id: int) -> None:
        results = self.request("GET", f"/api/pipeline/runs/{run_id}", label=f"pipeline-results-{run_id[:8]}")
        ranked = results.json().get("ranked") or []
        if ranked:
            first = ranked[0].get("orig_filename")
            if first:
                self.request(
                    "GET",
                    f"/api/pipeline/runs/{run_id}/posters/{first}",
                    binary=True,
                    label="pipeline-top-poster",
                )
        self.request("POST", f"/api/pipeline/runs/{run_id}/rescore", data={"weights": {"knn_sim": 0.35, "aesthetic": 0.1}, "gates": {"GATE_MIN_AESTHETIC": 4.0}}, label="pipeline-rescore")
        self.request("POST", f"/api/pipeline/runs/{run_id}/rescore", data={"weights": {"knn_sim": -1}}, expected={400}, label="pipeline-rescore-invalid")
        self.request("GET", f"/api/movies/{movie_id}/runs", label=f"movie-runs-{movie_id}")
        self.request("GET", f"/api/movies/{movie_id}/artwork-events", label=f"artwork-events-{movie_id}")

    def pass_feedback(self) -> None:
        self.section("PASS 5: FEEDBACK APPROVE, OVERRIDE, REJECT, UNDO")
        run_id = self.primary_run_id
        if not run_id:
            self._manual_failure("feedback-skipped", "No completed pipeline run available for feedback tests")
            return

        results = self.request("GET", f"/api/pipeline/runs/{run_id}", label="feedback-run-results")
        ranked = results.json().get("ranked") or []
        override_filename = ranked[1]["orig_filename"] if len(ranked) > 1 else None

        reject = self.request("POST", "/api/feedback", data={"run_id": run_id, "action": "reject_all", "deploy": False}, label="feedback-reject-all")
        self._remember_and_undo_feedback(reject, "reject-all")

        approve = self.request("POST", "/api/feedback", data={"run_id": run_id, "action": "approve", "deploy": False}, label="feedback-approve")
        self._remember_and_undo_feedback(approve, "approve")

        if override_filename:
            override = self.request(
                "POST",
                "/api/feedback",
                data={"run_id": run_id, "action": "override", "selected_filename": override_filename, "deploy": False},
                label="feedback-override",
            )
            self._remember_and_undo_feedback(override, "override")
        else:
            self._manual_failure("feedback-override-skipped", f"Run {run_id} did not have a second ranked candidate")

        self.request("POST", "/api/feedback", data={"run_id": "does-not-exist", "action": "approve"}, expected={404}, label="feedback-run-404")
        self.request("POST", "/api/feedback", data={"run_id": run_id, "action": "override"}, expected={400}, label="feedback-override-missing-file")
        self.request("POST", "/api/feedback", data={"run_id": run_id, "action": "bogus"}, expected={400}, label="feedback-action-invalid")
        self.request("POST", "/api/feedback", data={}, expected={422}, label="feedback-body-invalid")
        self.request("POST", "/api/feedback/undo", data={"event_id": "does-not-exist"}, expected={404}, label="feedback-undo-404")

    def _remember_and_undo_feedback(self, result: APIResult, suffix: str) -> None:
        event_id = result.json().get("event_id")
        if not event_id:
            return
        self.feedback_events.append(event_id)
        self.request("POST", "/api/feedback/undo", data={"event_id": event_id}, label=f"feedback-undo-{suffix}")

    def pass_taste(self) -> None:
        self.section("PASS 6: TASTE STATUS, MAP, CANDIDATES, RETRAIN")
        self.request("GET", "/api/taste/status", label="taste-status")
        self.request("GET", "/api/taste/map", expected={200, 404}, label="taste-map")
        if self.primary_run_id:
            self.request("POST", "/api/taste/map/candidates", data={"run_id": self.primary_run_id}, expected={200, 404}, label="taste-map-candidates")
        self.request("POST", "/api/taste/map/candidates", data={"run_id": "does-not-exist"}, expected={404}, label="taste-map-candidates-404")
        self.request("POST", "/api/taste/map/rebuild", expected={202}, label="taste-map-rebuild")
        self.release_marquee_gpu_resources("before-taste-retrain")
        retrain = self.request("POST", "/api/taste/retrain", expected={202, 409}, label="taste-retrain")
        if retrain.status == 202:
            self.poll_taste_rebuild()
        self.request("GET", "/api/taste/exemplars/default/image", expected={200, 404}, binary=True, label="taste-exemplar-image")
        self.request("GET", "/api/taste/exemplars/default/neighbors", expected={200, 404}, label="taste-exemplar-neighbors")

    def poll_taste_rebuild(self) -> None:
        deadline = time.monotonic() + TASTE_TIMEOUT
        attempt = 0
        last_progress: tuple[Any, Any, Any] | None = None
        last_progress_at = time.monotonic()
        while time.monotonic() < deadline:
            status = self.request("GET", "/api/taste/status", label=f"taste-rebuild-poll-{attempt}")
            rebuild = status.json().get("rebuild", {})
            progress = (
                rebuild.get("stage"),
                rebuild.get("processed"),
                rebuild.get("total"),
            )
            if progress != last_progress:
                last_progress = progress
                last_progress_at = time.monotonic()
            rebuild_status = rebuild.get("status")
            if rebuild_status in {"failed", "timeout", "cancelled"}:
                self._manual_failure("taste-rebuild-failed", f"Taste rebuild ended with {rebuild_status}: {rebuild.get('error')}")
                return
            if not rebuild.get("running"):
                return
            if time.monotonic() - last_progress_at > 300:
                self._manual_failure("taste-rebuild-no-progress", f"Taste rebuild made no progress for 300s: {rebuild}")
                return
            attempt += 1
            time.sleep(5)
        self._manual_failure("taste-rebuild-timeout", f"Taste rebuild still running after {TASTE_TIMEOUT}s")

    def pass_subtitle_inventory(self) -> None:
        self.section("PASS 7: SUBTITLE INVENTORY, PREVIEW, DOWNLOAD")
        for movie_id, movie in MOVIES.items():
            media_file_id = movie["media_file_id"]
            inventory = self.request("GET", f"/api/media-files/{media_file_id}/subtitles", expected={200, 422, 404}, label=f"subtitles-{movie_id}")
            if inventory.status == 200 and isinstance(inventory.body, dict):
                self.inventories[media_file_id] = inventory.body

        for movie_id in MOVIES_WITH_SRT[:5]:
            media_file_id = MOVIES[movie_id]["media_file_id"]
            scan = self.request("POST", f"/api/media-files/{media_file_id}/subtitles/scan", expected={200, 422}, label=f"subtitle-force-scan-{movie_id}")
            if scan.status == 200 and isinstance(scan.body, dict):
                self.inventories[media_file_id] = scan.body

        external_tracks, embedded_tracks = self.current_subtitle_tracks()
        for media_file_id, track_id in external_tracks[:8]:
            self.request("GET", f"/api/media-files/{media_file_id}/subtitles/{track_id}/preview", label=f"subtitle-preview-{track_id}")
            self.request("GET", f"/api/media-files/{media_file_id}/subtitles/{track_id}/download", expected={200, 404}, label=f"subtitle-download-{track_id}")
        for media_file_id, track_id in embedded_tracks[:5]:
            self.request("GET", f"/api/media-files/{media_file_id}/subtitles/{track_id}/preview", expected={200, 404}, label=f"subtitle-embedded-preview-{track_id}")

        first_media = next(iter(self.inventories), 1)
        self.request("GET", f"/api/media-files/{first_media}/subtitles/not-a-track/preview", expected={404}, label="subtitle-preview-404")
        self.request("GET", f"/api/media-files/{first_media}/subtitles/not-a-track/download", expected={404}, label="subtitle-download-404")
        for movie_id in MOVIES_WITH_SRT[:3]:
            self.request("POST", f"/api/movies/{movie_id}/subtitles/inspect", expected={200, 422}, label=f"movie-subtitle-inspect-{movie_id}")

    def current_subtitle_tracks(self) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
        external_tracks: list[tuple[int, str]] = []
        embedded_tracks: list[tuple[int, str]] = []
        for media_file_id, inventory in self.inventories.items():
            for track in inventory.get("tracks", []):
                track_id = track.get("id")
                if not track_id:
                    continue
                if track.get("source") == "external":
                    external_tracks.append((media_file_id, track_id))
                else:
                    embedded_tracks.append((media_file_id, track_id))
        return external_tracks, embedded_tracks

    def pass_subtitle_plans_jobs(self) -> None:
        self.section("PASS 8: SUBTITLE PLANS, MEDIA JOBS, POLICIES")
        self.exercise_subtitle_plans()
        self.exercise_media_jobs()
        self.exercise_subtitle_policies()

    def exercise_subtitle_plans(self) -> None:
        remove_media_file_id, remove_inventory = self._inventory_for_movie(2)
        if remove_inventory is None or not remove_inventory.get("tracks"):
            remove_media_file_id, remove_inventory = self._inventory_for_movie(338)
        if remove_inventory is None or not remove_inventory.get("tracks"):
            remove_media_file_id, remove_inventory = self._inventory_with_tracks()

        embed_media_file_id, embed_inventory = self._inventory_with_external_track()
        metadata_media_file_id, metadata_inventory = self._inventory_for_movie(155)
        if metadata_inventory is None or not metadata_inventory.get("tracks"):
            metadata_media_file_id, metadata_inventory = self._inventory_with_tracks()

        if remove_media_file_id is None or remove_inventory is None:
            self._manual_failure("subtitle-plans-skipped", "No subtitle inventory with tracks available")
            return
        tracks = remove_inventory.get("tracks", [])
        any_track = tracks[0] if tracks else None

        if any_track:
            remove = self.request(
                "POST",
                f"/api/media-files/{remove_media_file_id}/subtitle-plans",
                data={"operation": "subtitle_remove", "track_ids": [any_track["id"]], "backup": False, "allow_break": False},
                expected={201, 422},
                label="subtitle-plan-remove",
            )
            self._inspect_or_cancel_job(remove)
        if embed_media_file_id is not None and embed_inventory is not None:
            external = next((track for track in embed_inventory.get("tracks", []) if track.get("source") == "external"), None)
        else:
            external = None
        if embed_media_file_id is not None and external:
            embed = self.request(
                "POST",
                f"/api/media-files/{embed_media_file_id}/subtitle-plans",
                data={"operation": "subtitle_embed", "track_ids": [external["id"]], "backup": False, "allow_break": False},
                expected={201, 422},
                label="subtitle-plan-embed",
            )
            self._inspect_or_cancel_job(embed)
        media_file_id = metadata_media_file_id or remove_media_file_id
        metadata = self.request(
            "POST",
            f"/api/media-files/{media_file_id}/subtitle-plans",
            data={"operation": "subtitle_metadata", "edits": [], "backup": False, "allow_break": False},
            expected={201, 422},
            label="subtitle-plan-metadata",
        )
        self._inspect_or_cancel_job(metadata)

        self.request(
            "POST",
            f"/api/media-files/{media_file_id}/subtitle-plans",
            data={"operation": "subtitle_remove", "track_ids": ["does-not-exist"]},
            expected={422},
            label="subtitle-plan-unknown-track",
        )
        self.request(
            "POST",
            f"/api/media-files/{media_file_id}/subtitle-plans",
            data={"operation": "not-real"},
            expected={422},
            label="subtitle-plan-invalid-op",
        )

    def _inventory_with_tracks(self) -> tuple[int | None, dict[str, Any] | None]:
        for media_file_id, inventory in self.inventories.items():
            if inventory.get("tracks"):
                return media_file_id, inventory
        return None, None

    def _inventory_for_movie(self, movie_id: int) -> tuple[int | None, dict[str, Any] | None]:
        movie = MOVIES.get(movie_id)
        if not movie:
            return None, None
        media_file_id = movie["media_file_id"]
        inventory = self.inventories.get(media_file_id)
        return (media_file_id, inventory) if inventory is not None else (None, None)

    def _inventory_with_external_track(self) -> tuple[int | None, dict[str, Any] | None]:
        for movie_id in MOVIES_WITH_SRT:
            media_file_id, inventory = self._inventory_for_movie(movie_id)
            if inventory and any(track.get("source") == "external" for track in inventory.get("tracks", [])):
                return media_file_id, inventory
        for media_file_id, inventory in self.inventories.items():
            if any(track.get("source") == "external" for track in inventory.get("tracks", [])):
                return media_file_id, inventory
        return None, None

    def _inspect_or_cancel_job(self, plan_result: APIResult) -> None:
        job_id = plan_result.json().get("job_id")
        if not job_id:
            return
        self.request("GET", f"/api/media-jobs/{job_id}", label=f"media-job-{job_id[:8]}")
        if CONFIRM_SUBTITLE_MUTATIONS:
            self.request("POST", f"/api/media-jobs/{job_id}/confirm", expected={200, 409, 422}, label=f"media-job-confirm-{job_id[:8]}")
            self.poll_media_job(job_id, timeout=300)
        else:
            self.request("POST", f"/api/media-jobs/{job_id}/cancel", label=f"media-job-cancel-{job_id[:8]}")

    def exercise_media_jobs(self) -> None:
        for query in ["", "?status=planned", "?status=queued", "?status=failed", "?operation=subtitle_remove", "?operation=subtitle_generate", "?limit=5"]:
            self.request("GET", f"/api/media-jobs{query}", label=f"media-jobs{query or '-all'}")
        self.request("GET", "/api/media-jobs/does-not-exist", expected={404}, label="media-job-404")
        self.request("GET", "/api/media-jobs/does-not-exist/events", expected={404}, label="media-job-events-404")
        self.request("POST", "/api/media-jobs/does-not-exist/confirm", expected={404}, label="media-job-confirm-404")
        self.request("POST", "/api/media-jobs/does-not-exist/cancel", expected={404}, label="media-job-cancel-404")
        self.request("POST", "/api/media-jobs/does-not-exist/restore", expected={404}, label="media-job-restore-404")
        self.request("DELETE", "/api/media-jobs/does-not-exist/backup", expected={404}, label="media-job-delete-backup-404")

    def exercise_subtitle_policies(self) -> None:
        self.request("GET", "/api/subtitle-policies", label="policies-list")
        body = {
            "name": f"gauntlet-policy-{int(time.time())}",
            "mode": "blocklist",
            "languages": [],
            "enabled": True,
            "unknown_action": "keep",
            "protect_forced": True,
            "protect_default": True,
            "protect_last_full_dialogue": True,
            "include_external": False,
            "auto_apply": False,
            "audit_only": True,
            "hardlink_action": "block",
            "backup_mode": "none",
        }
        created = self.request("POST", "/api/subtitle-policies", data=body, expected={201}, label="policy-create")
        policy_id = created.json().get("id")
        if policy_id:
            self.request("GET", f"/api/subtitle-policies/{policy_id}", label="policy-get")
            updated = {**body, "name": body["name"] + "-updated", "languages": [], "enabled": False}
            self.request("PUT", f"/api/subtitle-policies/{policy_id}", data=updated, label="policy-update")
            policy_movie_ids = [2, 338]
            self.request("POST", f"/api/subtitle-policies/{policy_id}/audit", data={"movie_ids": policy_movie_ids}, label="policy-audit")
            self.request("POST", f"/api/subtitle-policies/{policy_id}/apply", data={"movie_ids": policy_movie_ids}, expected={202}, label="policy-apply")
            self.request("DELETE", f"/api/subtitle-policies/{policy_id}", label="policy-delete")
            self.request("GET", f"/api/subtitle-policies/{policy_id}", expected={404}, label="policy-get-after-delete")
        self.request("POST", "/api/subtitle-policies", data={"languages": ["en"]}, expected={422}, label="policy-create-invalid")
        self.request("GET", "/api/subtitle-policies/999999", expected={404}, label="policy-404")

    def pass_subtitle_generation(self) -> None:
        self.section("PASS 9: SUBTITLE GENERATION (SUBGEN ISOLATED)")
        self.ensure_gpu_free_for_external_phase("subgen")
        system = self.request("GET", "/api/system/status", label="system-before-subgen")
        active_jobs = {
            status: count
            for status, count in system.json().get("media_jobs", {}).items()
            if status in {"queued", "running"} and count
        }
        if active_jobs:
            self._manual_failure("subgen-media-jobs-not-idle", f"Media jobs active before Subgen: {active_jobs}")
        self.request("GET", f"{SUBGEN_URL}/status", expected={200, 404}, timeout=15, label="subgen-direct-status", external=True)
        self.request("GET", "/api/subtitle-generators", label="subtitle-generators")
        health = self.request("GET", "/api/system/status/generators", label="generator-health-before-generation")
        healthy = any(g.get("healthy") for g in health.json().get("generators", []))
        if not healthy:
            self.request("POST", "/api/media-files/1/subtitle-generations", data={"language_hint": "en", "output": "external"}, expected={503}, label="generation-disabled-shape")
            self.record_gpu_snapshot("after-subgen-skipped")
            return

        candidate = self._generation_candidate()
        if candidate is None:
            self._manual_failure("subtitle-generation-skipped", "No media file candidate available")
            self.record_gpu_snapshot("after-subgen-skipped")
            return

        media_file_id = candidate
        job = self.request(
            "POST",
            f"/api/media-files/{media_file_id}/subtitle-generations",
            data={"language_hint": "en", "output": "external"},
            expected={202},
            timeout=60,
            label=f"subtitle-generate-media-{media_file_id}",
        )
        job_id = job.json().get("job_id")
        if job_id:
            self.poll_media_job(job_id, timeout=SUBGEN_TIMEOUT)
        self.request("POST", f"/api/movies/{NONEXISTENT_MOVIE_ID}/subtitle-generations", data={"language_hint": "en", "output": "external"}, expected={404}, label="movie-generation-404")
        self.record_gpu_snapshot("after-subgen")

    def _generation_candidate(self) -> int | None:
        candidates: list[tuple[float, int]] = []
        for media_file_id, inventory in self.inventories.items():
            tracks = inventory.get("tracks", [])
            has_external = any(track.get("source") == "external" for track in tracks)
            duration = inventory.get("duration_seconds") or 999999.0
            if not has_external:
                candidates.append((float(duration), media_file_id))
        if candidates:
            candidates.sort()
            return candidates[0][1]
        if MOVIES:
            return MOVIES[next(iter(MOVIES))]["media_file_id"]
        return None

    def poll_media_job(self, job_id: str, *, timeout: int) -> str | None:
        deadline = time.monotonic() + timeout
        attempt = 0
        last_status: str | None = None
        while time.monotonic() < deadline:
            result = self.request("GET", f"/api/media-jobs/{job_id}", expected={200, 404}, label=f"media-job-poll-{job_id[:8]}-{attempt}")
            if result.status == 200:
                last_status = result.json().get("status")
                if self.terminal_job_status(last_status):
                    self.request("GET", f"/api/media-jobs/{job_id}/events", expected={200, 404}, label=f"media-job-events-{job_id[:8]}")
                    if last_status in {"failed", "cancelled", "interrupted"}:
                        self._manual_failure("media-job-terminal", f"Job {job_id} ended with status {last_status}")
                    return last_status
            attempt += 1
            time.sleep(min(10 + attempt * 5, 30))
        self._manual_failure("media-job-timeout", f"Job {job_id} did not finish within {timeout}s; last={last_status}")
        return last_status

    def pass_letterbox(self) -> None:
        self.section("PASS 10: LETTERBOX DISCOVERY, DETECT, PREVIEW, APPLY")
        self.request("GET", "/api/letterbox/status", label="letterbox-status")
        self.request("GET", "/api/letterbox/candidates", label="letterbox-candidates")
        for include_skipped in (False, True):
            for include_analyzed in (False, True):
                query = f"include_skipped={str(include_skipped).lower()}&include_analyzed={str(include_analyzed).lower()}"
                self.request("GET", f"/api/letterbox/movies/find-candidates?{query}", label=f"letterbox-find-{query}")

        for movie_id in MOVIES:
            self.request("POST", f"/api/letterbox/movies/{movie_id}/detect", expected={200, 422, 404}, timeout=180, label=f"letterbox-detect-{movie_id}")

        preview_targets = [25, 36, 5, 1, 443, 15]
        for movie_id in preview_targets:
            self.request("GET", f"/api/letterbox/movies/{movie_id}", expected={200, 404}, label=f"letterbox-state-{movie_id}")
            for mode in ("before", "after"):
                self.request("GET", f"/api/letterbox/movies/{movie_id}/preview?mode={mode}&minute=5", expected={200, 404, 422}, binary=True, timeout=120, label=f"letterbox-preview-{movie_id}-{mode}")

        if APPLY_LETTERBOX and MKV_MOVIES:
            for movie_id in [435, 5, 12, 25, 36]:
                result = self.request("POST", f"/api/letterbox/movies/{movie_id}/apply", data={}, expected={200, 422}, label=f"letterbox-apply-{movie_id}")
                if result.status == 200:
                    self.request("POST", f"/api/letterbox/movies/{movie_id}/remove", expected={200, 422}, label=f"letterbox-remove-{movie_id}")
                    break
        if MP4_MOVIES:
            self.request("POST", f"/api/letterbox/movies/{MP4_MOVIES[0]}/apply", data={}, expected={422}, label="letterbox-apply-mp4")
        self.request("POST", "/api/letterbox/detect", data={"movie_ids": MKV_MOVIES[:2]}, expected={202, 409}, label="letterbox-batch-detect")
        self.request("POST", "/api/letterbox/apply", data={"movie_ids": MKV_MOVIES[:2], "only_high": True}, expected={200}, label="letterbox-batch-apply")
        if MKV_MOVIES:
            self.request("POST", f"/api/letterbox/movies/{MKV_MOVIES[-1]}/ignore", expected={200}, label="letterbox-ignore")
        self.request("POST", "/api/letterbox/heal", label="letterbox-heal")
        self.request("POST", f"/api/letterbox/movies/{NONEXISTENT_MOVIE_ID}/detect", expected={404}, label="letterbox-detect-404")
        self.request("POST", f"/api/letterbox/movies/{NONEXISTENT_MOVIE_ID}/apply", data={}, expected={404}, label="letterbox-apply-404")
        self.request("GET", f"/api/letterbox/movies/{NONEXISTENT_MOVIE_ID}/preview", expected={404}, label="letterbox-preview-404")

    def pass_system_config(self) -> None:
        self.section("PASS 11: SYSTEM HEAL AND CONFIG ROUND TRIP")
        self.request("GET", "/api/system/status", label="system-status-final")
        self.request("GET", "/api/system/status/generators", label="generator-status-final")
        self.request("POST", "/api/system/heal", label="system-heal")
        config = self.request("GET", "/api/config/pipeline", label="config-get-final")
        values = config.json().get("values", {})
        original = values.get("GATE_MIN_AESTHETIC", 4.5)
        new_value = 4.25 if original != 4.25 else 4.5
        self.request("PUT", "/api/config/pipeline", data={"values": {"GATE_MIN_AESTHETIC": new_value}}, label="config-update-live-safe")
        self.request("PUT", "/api/config/pipeline", data={"values": {"GATE_MIN_AESTHETIC": original}}, label="config-restore-live-safe")
        self.request("PUT", "/api/config/pipeline", data={"values": {}}, expected={400}, label="config-empty")
        self.request("PUT", "/api/config/pipeline", data={"values": {"NOT_A_REAL_KEY": 1}}, expected={400}, label="config-unknown-key")
        self.request("PUT", "/api/config/pipeline", data={"values": {"OCR_DEVICE": "bogus"}}, expected={400}, label="config-invalid-value")
        if "AI_MODEL" in values:
            self.request("PUT", "/api/config/pipeline", data={"values": {"AI_MODEL": values["AI_MODEL"]}}, expected={400}, label="config-restart-required")
        self.request("GET", "/health", label="health-final")
        self.record_gpu_snapshot("final")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _manual_failure(self, label: str, message: str) -> None:
        self.unexpected_errors += 1
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "seq": self.call_count,
            "label": label,
            "method": "CHECK",
            "url": label,
            "status": 0,
            "expected": "manual",
            "ok": False,
            "elapsed_s": 0,
            "body": "",
            "error": message,
        }
        with ERRORS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.results_log.append(entry)
        print(f"  FAIL CHECK {label}: {message}")

    def run(self) -> int:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for path in (RESULTS_FILE, ERRORS_FILE, GPU_FILE):
            if path.exists():
                path.unlink()

        start = datetime.now(UTC)
        print(f"Marquee gauntlet starting at {start.isoformat()}")
        print(f"  Target: {BASE_URL}")
        print(f"  Subgen: {SUBGEN_URL}")
        print(f"  Lab movies: {len(MOVIES)} (MKV={len(MKV_MOVIES)}, MP4={len(MP4_MOVIES)}, sidecar-set={len(MOVIES_WITH_SRT)})")
        print(f"  Output: {OUTPUT_DIR}")

        self.section("BASELINE DB SNAPSHOT")
        db_before = self.db_snapshot()
        self.save_json(OUTPUT_DIR / "db_baseline.json", db_before)
        print("  DB snapshot saved: db_baseline.json")

        passes = [
            ("Baseline", self.pass_baseline),
            ("Library", self.pass_library),
            ("Sync Webhooks", self.pass_sync_webhooks),
            ("Poster Pipeline", self.pass_pipeline),
            ("Feedback", self.pass_feedback),
            ("Taste", self.pass_taste),
            ("Subtitle Inventory", self.pass_subtitle_inventory),
            ("Subtitle Plans Jobs Policies", self.pass_subtitle_plans_jobs),
            ("Subtitle Generation", self.pass_subtitle_generation),
            ("Letterbox", self.pass_letterbox),
            ("System Config", self.pass_system_config),
        ]

        for pass_name, fn in passes:
            before_calls = self.call_count
            before_errors = self.unexpected_errors
            started = time.monotonic()
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - gauntlet continues
                traceback.print_exc()
                self._manual_failure(f"pass-{pass_name}", f"Unhandled exception: {exc}\n{traceback.format_exc()}")
            elapsed = time.monotonic() - started
            self.pass_results[pass_name] = {
                "calls": self.call_count - before_calls,
                "unexpected_errors": self.unexpected_errors - before_errors,
                "elapsed_s": round(elapsed, 1),
            }
            print(f"\n  Pass done: {pass_name} - {self.call_count - before_calls} calls, {elapsed:.1f}s")

        self.section("FINAL DB SNAPSHOT")
        db_after = self.db_snapshot()
        self.save_json(OUTPUT_DIR / "db_final.json", db_after)
        print("  DB snapshot saved: db_final.json")
        db_delta_report = self.classify_db_deltas(db_before, db_after)
        self.save_json(DB_DELTA_FILE, db_delta_report)
        print("  DB delta report saved: db_delta_report.json")
        print("  DB changes:")
        for table in sorted(set(db_before) | set(db_after)):
            before = db_before.get(table, 0)
            after = db_after.get(table, 0)
            if isinstance(before, int) and isinstance(after, int) and before != after:
                marker = "expected" if table in EXPECTED_MUTATION_TABLES else "needs review"
                print(f"    {table}: {before} -> {after} ({after - before:+d}, {marker})")

        finished = datetime.now(UTC)
        summary = {
            "started_at": start.isoformat(),
            "finished_at": finished.isoformat(),
            "elapsed_s": round((finished - start).total_seconds(), 1),
            "total_calls": self.call_count,
            "successful_or_expected": self.call_count - self.unexpected_errors,
            "expected_non_2xx": self.expected_non_2xx,
            "unexpected_errors": self.unexpected_errors,
            "passes": self.pass_results,
            "completed_pipeline_runs": self.completed_runs,
            "failed_pipeline_runs": self.failed_runs,
            "primary_run_id": self.primary_run_id,
            "feedback_events": self.feedback_events,
            "db_before": db_before,
            "db_after": db_after,
            "db_delta_report": db_delta_report,
        }
        self.save_json(SUMMARY_FILE, summary)

        self.section("SUMMARY")
        print(f"  Total calls:           {self.call_count}")
        print(f"  Expected non-2xx:      {self.expected_non_2xx}")
        print(f"  Unexpected errors:     {self.unexpected_errors}")
        print(f"  Completed runs:        {len(self.completed_runs)}")
        print(f"  DB delta status:       {db_delta_report.get('status')}")
        print(f"  Summary file:          {SUMMARY_FILE}")
        print(f"  DB delta report:       {DB_DELTA_FILE}")
        print(f"  Results file:          {RESULTS_FILE}")
        print(f"  Errors file:           {ERRORS_FILE}")
        return 0 if self.unexpected_errors == 0 else 1


def main() -> int:
    return Gauntlet().run()


if __name__ == "__main__":
    sys.exit(main())
