# ArtFlix 0.4.2

Fix filenames containing bytes that cannot be represented as UTF-8. Previously
these could break scan-status JSON responses and SQLite probe lookups, leaving
stale progress on screen. Status responses now safely escape these values.

TV scans skip malformed filenames/directories with readable warnings and keep
scanning supported entries. Movie scans report unsupported folder/file names
instead of failing. Original files are never renamed or changed by this handling.
Normal accented and other valid Unicode names remain supported. Failed progress
polls are retried rather than permanently stopping browser updates.

After updating, refresh the browser and Resume scan (or Scan). Saved successful
probes/checkpoints are retained. Correct any reported filenames manually only
after inspecting them; ArtFlix does not guess their intended encoding.
