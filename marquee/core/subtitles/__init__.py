"""Subtitle management (design 03) — inspect, classify, mutate, and generate.

Read paths (probe/inventory/coverage/preview) are non-destructive. Mutation
paths funnel through the durable media-job queue and container adapters; they
NEVER write a media file in place — every write goes to a temp file beside the
source and is atomically swapped only after post-write probe validation.
"""
