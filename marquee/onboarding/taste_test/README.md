# Bundled taste test

The taste test is the no-library onboarding seed (design 20): ~40 genre-diverse
movies × 3–5 posters that a new user ranks to seed **both** engine layers.

This directory ships **empty** (only this README). Until a `manifest.json` exists
here, `service.taste_test_available()` returns `False` and onboarding falls back
to the library path. Generate the bundle on a host with the CLIP/DINO models
exported and a TMDB key configured.

## Structure (after building)

```
taste_test/
  manifest.json      # movies + posters + precomputed normalized_features
  images/            # the poster JPEGs referenced by manifest.json
  source/            # (input only) curated posters grouped per movie
```

`manifest.json`:

```jsonc
{
  "model_name": "clip-vit-b-32",
  "movies": [
    { "id": "tt_inception", "title": "Inception", "year": 2010,
      "genres": ["Science Fiction"],
      "posters": [
        { "file": "inception_a.jpg", "normalized_features": { "knn_sim": 0.7, ... } }
      ] } ]
}
```

`normalized_features` are the same 0–1 scorer features a live run produces, so
the pairwise trainer reads a taste-test ranking exactly like a real one.

## Building

1. Curate posters into `source/<Movie Title (Year)>/poster1.jpg …` (3–5 each,
   across genres). Pull them from TMDB by hand or with your own helper.
2. Run the builder (needs models + a reference taste profile present):

   ```bash
   python -m marquee.onboarding.build_taste_test
   ```

   It computes features for each poster (via `retro_features.compute_full_features`
   against the current taste profile), copies images into `images/`, and writes
   `manifest.json`.
3. Optionally build the shipped starter profile from these images:

   ```bash
   python -m marquee.onboarding.build_seed_profile
   ```

Commit `manifest.json` + `images/` (and the seed profile) so they ship.
