# ArtFlix 0.4.3

Added persistent skipped-file and scan-warning reports for movies and TV.
Warnings are saved immediately in SQLite, including their scan ID, UTC time,
path, reason and original path bytes for diagnosing malformed filenames.

Open Settings → About → Skipped files and scan warnings → Show scan warnings.
The newest 200 entries are shown; Download full report exports all entries to a
text file. The scan warning section also links to the download. Reports survive
browser/container restarts and interrupted scans. They contain historical entries;
fixing a file does not erase its previous warning. Reporting never changes media.

Reports begin collecting with this version. Older warnings can be recorded by
rescanning the affected files; this version does not invent missing history.
