"""Shared safety bounds for stage-major poster groups."""

from __future__ import annotations

# A group may cover an entire admitted poster batch, but remains finite.  Keep
# this aligned with the normal batch admission default and use it in every
# request, snapshot, host, and runner boundary so no layer silently re-chunks.
MAX_POSTER_GROUP_MEMBERS = 500

# Large group payloads travel through authenticated workspace files, never the
# 64 KiB runner control channel.  These limits still fail closed on corruption
# or an unexpectedly unbounded document.
MAX_POSTER_GROUP_INPUT_BYTES = 16 * 1024 * 1024
MAX_POSTER_GROUP_RESULT_BYTES = 64 * 1024 * 1024
