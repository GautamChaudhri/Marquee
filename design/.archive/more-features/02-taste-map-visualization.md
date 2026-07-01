# Taste Map Visualization

**Status:** Reconciled with the codebase on 2026-06-16.

The backend taste-map feature is implemented in `marquee/ml/taste_map.py` and
`marquee/api/routes/taste.py`. A frontend visualization is not implemented in
this repository.

## Implemented Backend

Routes:

| Endpoint | Purpose |
|---|---|
| `GET /api/taste/map` | Load or rebuild taste map; `recompute=true` forces rebuild |
| `POST /api/taste/map/rebuild` | Starts background map rebuild |
| `POST /api/taste/map/candidates` | Projects archived run candidates onto the map |
| `GET /api/taste/exemplars/{name}/image` | Serves thumbnail or full exemplar image |
| `GET /api/taste/exemplars/{name}/neighbors` | Returns nearest exemplar neighbors |

## Implementation Notes

- Map artifacts live under `data/cache/taste_map.*` and related cache/history
  directories.
- The implementation uses PCA fallback and optional dimensionality reduction
  support when dependencies are present.
- Candidate projection is on-demand and uses cached embeddings/run archives.
- Thumbnails are generated under the data cache.
- The map rebuilds when stale relative to the taste profile.

## Data Exposed

Responses include exemplar points, negative exemplar points when present,
cluster metadata when available, outlier information, thumbnail URLs, and
projection metadata.

## Planned Frontend

- `[PLANNED]` Plotly or Three.js visualization.
- `[PLANNED]` Cluster/genre/year/colorfulness/aesthetic color modes.
- `[PLANNED]` Taste evolution timeline UI.

## Needs Verification

- `[NEEDS VERIFICATION]` Optional UMAP/HDBSCAN behavior depends on installed
  visualization dependencies; verify on the deployment target before promising
  UMAP output.
- `[NEEDS VERIFICATION]` Exact response shape should be queried from
  `GET /api/taste/map` before frontend implementation.
