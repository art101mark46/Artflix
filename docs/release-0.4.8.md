# ArtFlix 0.4.8

- Recoverable Movie/TV folder renaming on shares without protected atomic rename
  support. Stages hard links before removing original names, preserves video
  contents, nested assets and directory attributes, and refuses existing folders.
  No video copying fallback; the share must support the required hard links.
- Journals in `/config/folder-renames`; Settings → About offers Finish rename or
  pre-commit cancellation. Pending operations block other library writes/scans.
  Interrupted commits can leave both names until recovery is completed. Avoid
  external modifications during a rename; unexpected files are preserved.
- Persistent New discovery badges and ★ New (count) filter above Movies,
  independent of existing NFO files. Mark all seen clears discovery highlights.
  Previously indexed movies are grandfathered as seen on upgrade.
- Incremental movie scans reuse unchanged folder entries in SQLite. New/changed
  folders are inspected locally, failed video probes retried, and completed
  checkpoints reused after interruption. Status survives restarts and reports
  new/unchanged/updated counts. The first upgrade scan establishes checkpoints.
  Scans still list/stat files; they do not refresh TMDB or download metadata.

120 automated tests pass, covering conflicts, rollback, crash recovery in staging
and commit, unexpected concurrent files, symlink refusal, Movie/TV save/recovery
routes, read-only/padlock protection, incremental reuse, probe retries, New flag
persistence and upgrade migration. Browser verification used disposable fixtures
and checked New filtering, selection, folder recovery and unchanged-scan counts.
