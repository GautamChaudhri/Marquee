"""Media-file inspection and metadata mutation.

This package shells out to ffmpeg/ffprobe (frame analysis) and mkvtoolnix
(``mkvpropedit`` / ``mkvmerge``) for the letterbox crop-detection feature
(design ``04-letterbox-cropping.md``). Nothing here touches the GPU — detection
is CPU-bound, so it runs independently of the poster pipeline's GPU lock.

All filesystem paths that originate outside the process MUST be validated with
``marquee.core.path_utils.safe_translate_and_validate`` before any write; that
guard lives at the call site (``LetterboxService``), not here.
"""
