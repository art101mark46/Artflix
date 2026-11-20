# ArtFlix 0.4.7

File renaming now supports shares which reject Linux protected atomic renames but
allow hard links, including compatible Unraid user shares. This fallback links
the existing file under its new name before removing the old name: no video data
is copied, and existing files or symlinks are never overwritten. It also applies
to connected subtitle renames and TV episode files. An interruption between the
two operations can leave both names pointing to the same intact file.

Shares supporting neither operation retain the original file and report a clear
explanation. Folder renames still require protected atomic rename support; on
unsupported shares, rename folders manually and scan again. No unsafe ordinary
rename or video-copy fallback is used.

Tests cover video/subtitle contents and inode preservation, existing destinations,
symlinks, unsupported hard links, folder refusal and rollback after a concurrent
subtitle destination appears.
