# ArtFlix 0.4.4

- Fixed library cards shrinking and clipping titles/status text in long lists.
- Added Episode availability controls in TV → Episodes. Unlock the padlock first.
- Choose Included in an existing file for joined episodes, or Ignore episode to
  remove an unwanted episode from the completeness target.
- Choose Automatic detection to undo a correction. Ignored episodes have their
  own filter; linked episodes are labelled with the containing video.
- Corrections are stored in SQLite per show and TVDB series ID, survive scans and
  restarts, and follow video/folder renames performed through ArtFlix.
- A linked episode becomes missing if its video is no longer present on inspection
  or rescan. Corrections do not modify videos or NFO files.
- Season summaries show full season totals regardless of the active episode filter.
