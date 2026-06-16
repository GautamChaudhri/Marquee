# Endpoint Test Bug Log

## BUG-001 — Pipeline run fails with GPU OOM on OCR stage for 101-candidate movie

**Endpoint:** `POST /api/pipeline/movie/257/run` (Blade Runner 2049)  
**Fixture:** Movie ID=257 (Blade Runner 2049, tmdb_id=335984)  
**When:** 2026-06-16T06:55:XX UTC  
**Severity:** high  
**Expected:** Pipeline completes for any movie with ≤200 TMDB candidates.  
**Actual:** Status `failed`. OCR worker GPU OOM when processing 101 candidates on an 8GB RTX 3070. Full error: `Out of memory error on GPU 0. Cannot allocate 4.500000MB memory on GPU 0, 7.646790GB memory has been allocated. OCR_WORKERS=10.`  
**Repro:** `curl -sS -X POST http://192.168.4.199:3165/api/pipeline/movie/257/run`  
**Artifacts:** `responses/pipeline-run-257-result.json`, `sse/pipeline-run-257.log`  
**Notes:** Same error, it still failed for movie ID=165 (Monsters, 20 candidates) also failed with GPU OOM, indicating the VRAM issue persists across runs. Pipeline runs 1 (21 cand), 2 (?, ok), 3 (ok) succeeded before this failure. The GPU VRAM may not be fully freed between runs. OCR_WORKERS=10 is likely too high for 8GB VRAM.

## BUG-002 — Pipeline run fails with GPU OOM on second consecutive large-OCR movie

**Endpoint:** `POST /api/pipeline/movie/165/run` (Monsters)  
**Fixture:** Movie ID=165 (Monsters, 2010)  
**When:** 2026-06-16T06:56:XX UTC  
**Severity:** high  
**Expected:** Pipeline completes.  
**Actual:** Status `failed`. Same GPU OOM as BUG-001. `available memory is only 5.500000MB`. 20 candidates after gate-style survived, but OCR could not initialize.  
**Repro:** Run production pipeline for movie 165 immediately after another failed run.  
**Artifacts:** `responses/pipeline-run-165-result.json`, `sse/pipeline-run-165.log`  
**Notes:** The GPU OOM persists across runs — VRAM not freed between pipeline invocations. This suggests PaddleOCR CUDA contexts or CLIP embeddings remain allocated on GPU between runs.
