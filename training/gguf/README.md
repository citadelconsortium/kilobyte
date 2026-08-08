# Release GGUF

The canonical brain is distributed as the `kilobyte.gguf` asset on the
[`brain-1.1` GitHub Release](https://github.com/citadelconsortium/kilobyte/releases/tag/brain-1.1),
not committed to Git (GitHub rejects files of this size). `manifest.json` records
the exact bytes, format, and provenance; the installer verifies the same SHA-256
before an atomic install.

The local artifact is intentionally ignored. Maintainers can reproduce it with
the scripts in `training/` and must upload only the verified GGUF release asset.
