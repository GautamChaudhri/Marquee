# Starter taste profile seed

A tiny generic taste profile (`taste_profile.seed.npz`) copied into
`TASTE_PROFILE_PATH` on first run if no profile exists yet, so the pipeline never
hard-fails while a new user is still onboarding (design 20).

Ships **empty** (only this README). Build it on a host with the models exported:

```bash
python -m marquee.onboarding.build_seed_profile
```

That runs the normal taste-profile builder (`taste_trainer.rebuild_profile`) over
the bundled taste-test images and writes `taste_profile.seed.npz` here. Commit the
`.npz` so it ships. `service.ensure_starter_profile()` consumes it.
