# ArtFlix 0.4.5

Fix TV metadata and actor portrait saves on shares rejecting RENAME_NOREPLACE
with EINVAL (as seen on some user-share/FUSE filesystems).

New metadata files try the protected rename, then a complete-file hard link.
If neither operation is supported, metadata/image creation uses an exclusive
new-file open and copy. Existing destinations and symlinks are never opened by
that fallback. Failed copies remove only the newly created metadata file.

Video, subtitle and folder renames retain the original atomic no-overwrite
requirement. The compatibility fallback cannot be used for media moves.

Also fixes rollback tracking so a concurrently created portrait is preserved,
and restores prior app-owned TV state if a later save step fails. Destination
collisions return a review-again message rather than an internal server error.
