# ArtFlix 0.4.1

- Added a collapsible episode progress panel to TV scanning.
- Added a discovery/counting stage, current filename, per-file elapsed time,
  total/remaining file counts and per-show progress.
- Persisted scan status across restarts and added Resume scan.
- Added SQLite folder checkpoints, skipping unchanged completed shows.
- Reused successful video probes in partially completed shows after interruption.
- Retried failed video probes instead of caching temporary failures indefinitely.
- Listed subtitles once per directory, reducing repeated work in large seasons.

Existing appdata and media mappings remain unchanged. Update between active
scans/saves. Earlier versions cannot supply a persisted scan status, but their
successful probe cache is compatible. Counts are video files, not episode numbers.
