# ArtFlix Movie Manager

A small, self-hosted Unraid web app for scanning movie folders, matching titles against TMDB, choosing fanart.tv images, and writing Kodi/Jellyfin style metadata after an explicit review.

## What it does

- Scans a configured root for immediate child folders containing common video formats.
- Uses TMDB search and localized movie detail endpoints, with an optional year hint.
- Fetches the selected country's certification options when you set a two-letter region code (for example `GB` or `US`) and lets you choose one during review.
- Lets you click artwork to inspect the original fanart.tv image at full size, and manually enter a certificate if TMDB has no entry.
- Clicking an already processed folder loads its existing `movie.nfo` and video stream information so you can update details and save again.
- Shows duration, size, resolution, codecs, audio tracks, and subtitle tracks using `ffprobe` in the container.
- Offers editable NFO fields and a video filename pattern (`{title}`, `{original_title}`, `{year}`, `{tmdb_id}`); the review screen previews renames before saving.
- Lets you choose TMDB cast portraits to download into an `actors` subfolder within the movie folder.
- Uses fanart.tv's movie API with your developer key and optional personal client key.
- Creates `movie.nfo` with editable title, plot, release date, runtime, genres, studios, people, certificate, and TMDB ID. It can also save selected `poster.jpg`, `landscape.jpg`, `fanart.jpg`, `disc.png`, or `clearart.png` assets.
- Requires clicking **Approve and save changes** after reviewing the TMDB match, edits, and selected fanart. Existing output files cause a conflict unless you confirm replacement in the review or enable overwrite in Settings.
- Stores settings and credentials in `/config/settings.json` with owner-only file permissions. Secret values never come back from the settings API.

## Build and run with Docker Compose

1. Set the two host paths in `docker-compose.yml` to your Unraid movie share. The read-only mapping is for scanning; the write mapping grants write access. They can point at the same host directory.
2. From this directory run `docker compose up -d --build`.
3. Open `http://UNRAID-IP:8787` and save the container paths (`/movies` and `/movies-write`), language (TMDB format such as `en-US`), country code, TMDB Read Access Token, and fanart.tv API key.
4. Scan, choose a folder, search and review, optionally select fanart assets, then approve the write.

The app has no built-in account system. Keep the web port on a trusted LAN or put it behind an authenticated reverse proxy. API keys are server side, but anyone with access to the UI can use the app's API features and approve writes.

## Unraid template

`unraid-template.xml` is a Community Applications style template example. Replace `ghcr.io/art101mark46/artflix:latest` with the image you build and publish. The example maps the share read-only at `/movies`, and again read/write at `/movies-write`; set these exact container paths in the app's Settings. The configured appdata directory persists credentials across restarts.

To build locally on Unraid, use a terminal in a checkout of this project:

```sh
docker build -t movie-metadata:latest .
```

Then install a container using image `movie-metadata:latest`, bridge networking, container port `8000` mapped to host port `8787`, `/mnt/user/appdata/movie-metadata` mapped to `/config`, and the two movie path mappings described above. For repeatable updates across machines, publish a tagged image and use that image in the template.

## API credentials and attribution

- TMDB: create an API Read Access Token in your TMDB account API settings. This app sends it in the server-side `Authorization: Bearer` header.
- fanart.tv: create an API key; the optional personal client key is sent only from the server.
- The interface includes the TMDB attribution notice and links to both providers. TMDB requires the notice “This product uses the TMDB API but is not endorsed or certified by TMDB.” Commercial use may require a separate license. Review each provider's current usage terms before distributing the app or its generated files.

## Safety notes and limits

- Scanning reads only immediate child folders under the configured root. Files are written only into the corresponding existing folder beneath the configured write root.
- Search results are not written automatically. Metadata artwork uses fixed supported names; video names use the reviewed template; actor portraits use sanitized names and TMDB IDs.
- fanart.tv assets are downloaded only from its `assets.fanart.tv` host.
- A failed or missing certification is represented as blank; select the correct region for your library.
- This initial version handles movie folders, not TV seasons, multi-edition naming, subtitles, or automatic background scans.

## Development

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./config uvicorn app.main:app --reload
```

The web app starts with unset API keys and region; configure them in Settings. The app reads `root_path` and `write_path` from its persistent settings file.

## Library search workspace

Use the Search library tab to filter the scanned library. Each field suggests values present in that field in the library. Results update in the Movie folders column as you type; selecting a movie switches to Movie details and clears the filters. The layout adapts to desktop, tablet, and phone widths.

## Library cache and per-movie reset

The app creates `/config/artflix.sqlite3` automatically. It caches video probe results by full path, size, and modification time and stores the scanned library index. The initial scan still probes each movie; subsequent scans reuse unchanged results. Keep `/config` persistent. No database setup is required.

The Metadata tab offers **Clear stored metadata for this movie**. It previews the exact deletion list and requires confirmation. It only deletes NFO and image files in that movie folder, actor images in its `actors` directory, the artwork source record, and cached database entries. It preserves videos, subtitle files, symlinks, and other file types. Only the reviewed list can be deleted; if it changes, repeat the preview.

Artwork previews support left/right arrows and arrow keys within their category. **Save image to movie folder** saves the displayed artwork to its conventional filename and remembers the source for highlighting.

Aspect ratio is the encoded display ratio, reduced to a ratio and decimal value, accounting for sample aspect ratio when needed. Encoded black bars are part of that frame; detecting the theatrical content ratio would require additional frame analysis.

BBFC rating images are loaded from BBFC's official website when the selected region is GB (or unset). A manually entered certificate is user-supplied. See [BBFC symbol usage guidance](https://www.bbfc.co.uk/using-a-bbfc-age-rating/for-film-tv-listing-services-and-other-uses).

## Saved workspace and supplied BBFC badges

On startup, the saved library is restored from `/config/artflix.sqlite3`, without a new scan. The last selected movie is remembered in the browser and reopened for the matching library root. Keep the `/config` mapping unchanged when recreating the container. Scan again to discover newly added folders.

The supplied BBFC PNGs are bundled under `app/static/bbfc` unchanged. They are opaque indexed PNGs; CSS clips the exterior around each symbol while retaining its interior white areas. These badges appear beside the poster. The application header spans the browser width with the title left and theme/settings controls right.

## Find incomplete movies

Library search includes a **Find missing fields** section. Select fields such as certificate, plot, genres, TMDB ID, poster or background artwork; choose any or all selected fields missing. This combines with the normal title/year/cast/technical filters. Blank values and zero IDs/runtime/year count as missing. Manual certificates such as NR count as present. Actor portrait filtering checks for no downloaded portraits, not a portrait for every cast member. Subtitle checks include embedded and external tracks. Results reflect the last scan; scan again after external library changes. Existing saved index entries acquire these checks automatically when restored.

## ArtFlix branding

The supplied transparent logo is bundled unchanged at `/static/branding/artflix-logo.png` and shown beside the application name. Square PNG and ICO versions are provided for browser and container icons; they preserve the complete logo on a dark square. These assets ship inside the image.

For Unraid, set the template's Icon URL to `http://YOUR-UNRAID-IP:8787/static/branding/artflix-icon.png` after the app is running, replacing the IP and port with your actual server address. The template uses `UNRAID-IP` as a placeholder. If using Compose, `ARTFLIX_ICON_URL` can override its default icon label. For a future public template, host the icon at a permanent HTTPS URL; [Unraid's example template](https://github.com/unraid/unraid-community-apps-starter/blob/main/templates/example-app.xml) shows this format.

The header now uses the approved edited version at `/static/branding/artflix-logo-clean.png`; favicon and container icons are regenerated from it. The original supplied logo remains at `/static/branding/artflix-logo.png`.

## Initial scan of an existing library

ArtFlix reads existing Kodi/Jellyfin XML NFO files: `movie.nfo`, matching video-basename NFOs, or one unambiguous NFO in the folder. Both `<tmdbid>` and `<uniqueid type="tmdb">` are recognized. It reads common poster/fanart names, cast, genres and certificates. It does not fetch or overwrite metadata during scanning. Unsupported or ambiguous NFOs are reported rather than rewritten.

The sidebar uses a red right edge for no readable metadata, amber for incomplete core metadata/artwork, and no edge when complete. A star indicates no NFO file was found. Optional tagline, subtitles and actor portrait downloads do not trigger the amber marker. Hover the entry for missing fields. The application's supplied logo is now the only header branding; its duplicate title/subtitle are removed.

For an initial deployment, enable `ARTFLIX_READ_ONLY=true` and mount **both** movie paths read-only. `/config` stays writable for settings and SQLite. The Unraid template now defaults to read-only mode and read-only movie mappings; change both that setting and `/movies-write` mount mode when intentionally enabling edits. Existing installed templates are not changed automatically. Compose users can apply `docker-compose.readonly.yml`; set `ARTFLIX_MOVIES_PATH` to the host movie root in both configurations.

For Podman, recreate using the same movie host path and appdata folder, adding `-e ARTFLIX_READ_ONLY=true` and changing both movie mappings to `:ro`. Do not confuse a read-only `/movies` scan mapping with protection of the whole library while `/movies-write` remains writable. Once validated, changing `/movies-write` to `:rw` and disabling the environment flag permits approved changes. No movie deletion operation exists.

Video renaming is now off by default. When explicitly selected, atomic no-overwrite renames are used for videos and matching subtitles. A name conflict aborts instead of overwriting another file. The Linux filesystem must support safe no-replace renames; otherwise renaming fails with the original contents preserved. If a multi-file rename fails, completed moves are rolled back where possible without overwriting a concurrent file; any unrolled moves retain their video contents under the new name.

Before replacing an existing NFO or artwork, the previous file is copied under `/config/backups/` with restore information. Unmanaged existing NFO fields (including watched/play-count/resume information and non-TMDB unique IDs) are retained when regenerating metadata. Reset is a separate explicitly confirmed operation and deletes only reviewed NFO/image files and actor images; it does not delete videos or subtitles.

Verification covers temporary movie fixtures, unchanged hashes/names/modification times during scan/rescan, read-only write/reset rejection, NFO import, reset video/subtitle preservation, and rename collisions/concurrent destination creation. This is not a quantified failure probability or testing on every Unraid/storage setup. Start with read-only mounts and retain your normal backups/snapshots before enabling library edits.

## Alphabet navigation, sorting, and sets

The alphabet beside the Movies list jumps to the first matching movie in the current filtered results. Sorting uses the saved movie title, falling back to its folder name. Settings can ignore configurable leading articles (default: The, A, An); leave that setting blank to sort full titles. Alphabet jumps follow the same sorting rules.

Incomplete entries show a brief missing-fields summary. Movie Details lists the missing saved fields and marks editable fields, even if a TMDB preview has already filled them. Optional omissions are shown separately.

Movie Details includes a Movie set field, with existing set suggestions and TMDB collection suggestions when available. Sets are written to Kodi/Jellyfin-compatible `<set><name>…</name></set>` NFO elements. Existing nested and older plain-text set names are read during scanning and index upgrade. **Show movies in set** filters the scanned library to exact matching set names; it does not list films you do not own. Save an edited set with the other metadata before expecting its changed membership in searches. Standalone movies do not require a set to be considered complete.

## Padlock and reviewed changes

The top-right padlock starts locked on every page load. Unlocking allows editing in that browser tab; locking revokes its temporary edit token. Settings changes also require unlocking. Browsing, searching, health checks and scanning remain available while locked. `ARTFLIX_READ_ONLY=true` still prevents all library mutations, even when the padlock is unlocked; you can unlock Settings to configure API keys in that mode. The padlock is an accidental-edit guard, not a user account or password system.

Every save, portrait download, single-artwork download and backup restore has a before/after review. The review includes metadata fields, exact file outputs, connected subtitle renames and any folder rename. Approval expires after 15 minutes and is tied to the current files, requested changes and settings. External changes invalidate approval; a padlock relocked during an image download prevents committing it.

The Metadata tab lists previous NFO/image versions under **Restore previous metadata or artwork**. Each file has **Review restore**; confirming creates a backup of the current version before restoring the chosen one. Backups remain associated with the movie after approved folder renames. Metadata reset now also backs up its reviewed NFO/image deletion list. Videos and subtitles remain excluded from reset and restoration.

## Background scan and local-first viewing

Scan shows folders checked, movies found, new movies, errors, elapsed time and the current folder. Cancellation waits for the current file probe to finish (up to its timeout). Completed entries are persisted as the scan proceeds. Cancellation retains previously indexed movies that were not reached and does not remove or alter library files. Reopening the page reconnects to an active scan; completed progress is not a new scan. The current scan job runs in the application process; restarting the container stops it, while already indexed entries remain in SQLite.

Opening a movie uses its saved NFO and local artwork/portraits without contacting TMDB or fanart.tv. Use **refresh movie** or an explicit TMDB search to fetch provider alternatives. Approved changes refresh only the affected index entry rather than scanning all 2,000 movies again.

## Health and duplicate review

Library search includes clickable health counts for unmatched movies, missing posters/backgrounds/certificates, incomplete metadata, unreadable or unprobed files, and possible duplicate movies. Counts describe the whole scanned library; clicking narrows the sidebar. Movies sharing a positive TMDB ID are grouped for review, since they may be alternate editions rather than unwanted duplicates. There is no automatic merge or deletion.

## Verification

With the dependencies from `requirements.txt` installed, run `python -m unittest discover -s tests -v`. Tests exercise the actual FastAPI endpoints for the padlock, immutable approvals, local-only browsing, metadata backup/restore, folder renames, reset exclusions, provider-image saves and cancellable scans. Separate filesystem tests cover atomic no-overwrite renames and concurrent destination creation. Tests use disposable fixtures, not a production movie share.


## Extras, editions and collections (0.2.0)

Scanning separates main-video candidates from recognized trailers, samples and extras. Common extras directories and explicit filename suffixes are recognized; symlinked media is skipped. The Metadata tab lists excluded extras and lets you select main files. Multiple editions or uncertain filenames require explicit selection before a video rename. Matching subtitle renames exclude subtitles attached to recognized extras. Classification is conservative, not a content analysis: review unfamiliar naming conventions before enabling renames. Extras are never automatically renamed or deleted.

**Check collection completeness** beside Movie set compares the TMDB collection with indexed TMDB movie IDs. It distinguishes owned, missing released films, upcoming films and films without release dates. Cached results remain available; **Refresh from TMDB** explicitly updates them. Custom local set names remain usable independently. See [TMDB collection details](https://developer.themoviedb.org/reference/collection-details) and [Jellyfin movie organization](https://jellyfin.org/docs/general/server/media/movies/) for provider and media conventions.

## Saved searches

Search includes presets for missing certificates, missing artwork, 4K without subtitles, unmatched movies and possible duplicates. Save your current filters with **Save as new**, then load or update them later. Searches are stored in SQLite and survive browser/container restarts when `/config` is persistent. Archiving is recoverable under **Archived searches**. Loading needs no unlock; saving, updating, archiving and restoring require the padlock to be unlocked.

## Deployment checks and diagnostics

Settings → **Version, deployment checks and diagnostics** shows the release/build and indexed movie count. **Run deployment checks** checks both library mappings, available space, appdata write access, SQLite integrity, ffprobe and credential configuration. It never creates a test file in the movie share. **Test API connections** explicitly contacts the providers using server-side credentials; this is separate from local checks.

**Download diagnostic report** exports system/check results and recent structured events. It excludes movie filenames, configured paths, API keys, edit tokens, request bodies and headers. Local logs live at `/config/artflix.log`, rotate at 1 MiB and retain up to three files. Review reports before sharing. Build labels can be supplied through the Docker build argument `ARTFLIX_BUILD`; the default is `local`.

Release verification: 36 automated checks cover existing safety workflows plus extras/subtitle exclusion, ambiguous selections, saved-search persistence and recovery, collection caching/status, deployment checks and credential redaction. Browser verification uses a separate disposable fixture library.


## Movie Metadata display and artwork refresh (0.3.0)

The Metadata tab displays one video at a time. If a folder contains more than one edition/candidate, use **Video file to inspect** to switch its technical details and file path. This does not select files for renaming; the explicit main-file checkboxes remain separate. Our demo folder contains an extended-version test file, so two candidates are expected in that selector.

Artwork now has **Refresh artwork from fanart.tv**. It fetches provider choices without refreshing the movie, changing your metadata edits or reloading the page. Saved local images remain available and downloaded provider selections remain highlighted. An existing TMDB ID and configured fanart.tv key are required for fresh choices.

## TV Shows

Click **Movies** in the sidebar heading to switch to **TV Shows**, and click it again to return. Each library has its own index and saved searches in the persistent SQLite database. The browser remembers the library and last selected show. Configure TV paths and credentials under Settings → **TV Shows settings**. Movie paths are never reused for TV; overlapping roots and mismatched TV read/write folders are rejected.

Supported layout:

```text
TV/
  Example Show/
    tvshow.nfo
    poster.jpg
    Season 01/
      Example Show - S01E01 - Pilot.mkv
      Example Show - S01E01 - Pilot.nfo
      Example Show - S01E01 - Pilot.en.srt
```

Season folders are optional. S01E01, S01E01E02, S01E01-E03 and 1x01 numbering are recognized. Existing `tvshow.nfo` and episode NFO files are read locally. Unknown files are listed for review; extras and symlinked directories are excluded from episode renaming. Scanning does not contact TVDB or fanart.tv, and technical probing is cached.

TVDB v4 requires an API key; subscriber-supported keys additionally require the subscriber PIN. Get access under [TVDB API and licensing](https://www.thetvdb.com/api-information), and consult [the official v4 authentication documentation](https://thetvdb.github.io/v4-api/). The app accepts your own key/PIN and does not include a shared developer key. They stay on the server, are blanked from settings responses, and are included in diagnostic redaction. TVDB's language setting is a three-letter code, such as `eng`, `fra` or `deu`. TVDB attribution links are visible in the application.

Match a show through **Find or change TVDB match**, or enter its TVDB ID and review it. Refresh explicitly fetches localized details and all episode pages; a failed or repeated page never replaces a complete cached inventory. This release supports **aired order**. The show entry displays owned aired episodes, missing aired episodes and upcoming episodes. The Episodes tab groups seasons and separates specials, upcoming and undated episodes. Completeness compares unique season/episode numbers, so duplicate editions do not inflate it. Specials are excluded from the main percentage. A 100% result means all dated aired episodes in the cached provider list are present; undated episodes are listed separately. Refresh the provider list and scan after external library changes.

TV has Show Details, Episodes, Actors, Artwork and Metadata tabs. You can edit show fields/certificates and genre tags, browse and save actor portraits, refresh fanart.tv choices, preview artwork with arrows/cursor keys, save an image individually, inspect an individual file and edit its episode NFO fields. TV searches autocomplete from indexed values, include missing episodes and missing metadata, and support saved/archived searches separately from movies.

Saving uses the same padlock, server read-only protection and signed before/after review as movies. Existing show and episode NFOs preserve unmanaged fields such as watched state. Replacements and metadata clearing are backed up under `/config/tv-backups`; **Restore previous metadata or artwork** restores a reviewed file. Metadata clearing never deletes videos or subtitles. Episode and show-folder renaming is off by default, includes matching subtitle/NFO sidecars, and uses atomic no-overwrite moves. Collisions stop the operation. Combined-episode files count correctly and can be renamed, but this release preserves their existing NFOs: disable bulk episode NFO generation for a show containing combined files, or use separate episode files for individual NFO editing. This release does not generate `season.nfo` or fetch provider season artwork.

See [Jellyfin TV organization](https://jellyfin.org/docs/general/server/media/shows/) and [Kodi TV NFO files](https://kodi.wiki/view/NFO_files/TV_shows) for media-server conventions. TV metadata and fanart calls are tested with controlled provider responses; live access depends on your own keys and provider licensing.

### TV container mappings

Compose includes read-only TV mappings by default. Set `ARTFLIX_TV_PATH` to the host TV root before recreating, then set TV scan path `/tv` and TV write path `/tv-write` in Settings. With no TV path supplied, Compose maps an empty local `demo-tv` folder. Do not point TV and movie mappings at the same share. The Unraid template includes optional `/tv` and `/tv-write` mappings, initially read-only.

For a manually created Podman preview, add both mappings when recreating the same container with its existing config volume:

```sh
-v /path/to/TV:/tv:ro -v /path/to/TV:/tv-write:ro
```

After testing read-only, change only `/tv-write` to `rw` and disable `ARTFLIX_READ_ONLY` when intentionally enabling approved writes. Keep `/tv` read-only and `/config` persistent. Rebuilding an image alone does not update a running container; recreate it and refresh the browser.

Release verification: 58 automated tests cover movies and TV, including media preservation, separate indexes, multi-episode counting, provider pagination/localization, review invalidation, portrait/artwork state, rename collisions, extra-subtitle exclusion, metadata reset/restore and saved-search recovery. Browser tests use isolated fixture movies/shows, not a production library.


## Episode navigation and filename example (0.3.1)

The TV Metadata tab includes Previous episode and Next episode buttons alongside the dropdown, with a position indicator. Buttons stop at the first/last local episode file and work while editing is locked. Unsaved episode edits remain associated with their file when navigating.

A live filename example below the episode format shows the selected file’s current and proposed names. It updates when the format, show title, match or selected episode changes. It uses the same server-side formatting and filename cleanup as the actual rename, including combined episodes, and warns about an existing destination. The example uses cached TVDB episode titles; refresh the show if no episode list is cached. Previewing never writes to the share or calls TVDB. All renames still require the full final review.

Verification: 60 automated tests passed, including preview/actual-rename agreement, combined episodes, invalid patterns/paths and destination conflicts. Browser checks verified Previous/Next boundaries, dropdown synchronization and live format changes using fixture episodes.


## Movie panel cleanup (0.3.2)

The reusable movie file-details section is rebuilt on selection and review so generated main-file controls, extras lists and paths cannot accumulate across movies. The previous per-file selector change did not address this separate panel-lifecycle bug. Browser regression verification switched through three fixture movies twice and checked exactly one main-file section with only the selected folder’s candidates after each switch. All 60 automated tests also pass. Rebuild/recreate the container and reload the browser to load the corrected assets.


## TV writer and director import (0.3.3)

Refresh show now extracts explicitly credited writers/directors from TVDB series records and fetches extended episode records for numbered episodes present in the local show. Episode crew stays with the corresponding episode; it is not copied onto the whole show. TVDB role names are used, with `/people/types` resolving numeric roles when necessary. Directors/writers are excluded from cast portrait selections, and duplicate credit names are removed.

The Show Details fields and individual episode editor display the available suggestions. Existing local credits take precedence, and explicit edits take precedence over both local and provider data. Saving show details writes series credits; selecting **Generate NFO files for matched episodes** writes available episode credits to `<director>` and `<credits>` fields. Review includes the proposed credit changes before writing.

Extended episode records are cached for seven days and refreshed sooner when TVDB reports a different episode `lastUpdated`. Optional credit-request failures retain usable cached/local credits and do not discard a valid episode inventory. Scanning or opening a saved show remains local-only. Sources: [TVDB v4 extended episode and character schema](https://thetvdb.github.io/v4-api/), [official OpenAPI specification](https://github.com/thetvdb/v4-api/blob/main/docs/swagger.yml).

64 automated tests pass, covering owned-episode-only fetching, caching, role separation, duplicate names, NFO credit output, manual credit preservation and optional provider failures. Browser verification uses controlled provider responses and fixture media.


## TV search layout and episode thumbnails (0.3.4)

TVDB search results use their own responsive two-column cards rather than the three-column movie layout, preventing descriptions from being squeezed into the movie poster column. Title, year, TVDB ID and description have a readable text area, with a compact Review match button. At narrow widths the action moves below the text. The direct TVDB ID/order controls are also grouped cleanly. A trailing bracketed folder year is separated into an optional year search field; titles consisting of numbers (such as 1899) are retained. Clear the year field to broaden the search.

After refreshing a show, the Metadata tab displays the selected episode's saved thumbnail and/or a TVDB candidate. Click the image to enlarge it; **Review and save episode thumbnail** writes only that thumbnail through the existing padlock and signed review. It does not rewrite show/episode NFOs. The save bar also has **Download episode thumbnails where available** for downloading alongside other selected show/episode work. Existing images remain protected unless replacement is explicitly enabled; bulk thumbnail downloads skip existing thumbnails when replacement is off. Replacement images are backed up.

Images use the video's basename plus `-thumb` and the source image extension, for example `Show S01E01-thumb.jpg`. This follows [Jellyfin's episode image naming](https://jellyfin.org/docs/general/server/media/shows/). TVDB's episode `image` field comes from the [v4 API](https://thetvdb.github.io/v4-api/), with the extended episode record used as a fallback. Only supported HTTPS provider image hosts are accepted; the existing bounded image-download routine and review invalidation protect the write.

Thumbnails follow episode renames and local NFO thumbnail references are updated where applicable. When generating an episode NFO with a downloaded thumbnail, its `<thumb>` points to the local image. Saved images are recognized from disk and highlighted. Metadata reset backs up and removes thumbnails while preserving videos and subtitles. A combined-episode file uses its first episode's thumbnail; combined NFO generation remains excluded as documented above.

69 automated tests pass. Added checks cover search/year separation, thumbnail-only saves, existing-image protection, NFO references, rename/backups/reset preservation, and unsafe file/URL rejection. Browser checks used a no-NFO fixture show named `11 Minutes (2022)`, controlled provider responses and a test image; they verified readable desktop results, a 600px responsive layout, the review dialog and saved-image recognition. Live thumbnails depend on availability in TVDB.


## Relative TVDB images and footer version (0.3.5)

TVDB episode images can be relative `/banners/...` paths. ArtFlix now resolves those paths against `https://artworks.thetvdb.com` while retaining the HTTPS host allowlist. Normalization happens both on refresh and when reading an existing cached episode list, so previously scanned/matched shows do not need a rescan solely for this fix. Unsupported image locations remain rejected. The former full-URL-only check silently skipped these valid relative paths.

The review dialog lists thumbnails ready to download, already saved, unavailable, and unmatched. The completed save reports actual thumbnail downloads separately from other files. The bulk checkbox also explains that it takes effect when reviewing and confirming a save.

The footer displays `Artflix version 0.3.5`, rendered from the same application VERSION value as the system panel rather than a separate hard-coded label.

72 tests pass, including cached relative-path downloads without a provider refresh, host rejection, and mixed bulk-result counts. Browser verification used an isolated fixture library and downloaded a real JPEG from the TVDB artwork server through the normal bulk review/save path. The save reported one thumbnail downloaded; its JPEG contents and the unchanged fixture video were verified. Production library files and settings were not modified.

### Settings tabs

Settings is divided into TV Shows, Movies, API, File and format, and About.
Movie and TV libraries have separate ignored sorting prefixes. Existing TV
sorting preferences inherit the old shared prefixes until changed. API keys stay
server-side; leaving their fields empty retains saved credentials.

TV settings include separate single-episode and multi-episode filename formats.
Both accept `{show}`, `{season}`, `{episode}`, and `{title}`. For combined files,
`{episode}` expands to `01E02` and titles join with ` + `; for example
`{show} - S{season}E{episode} - {title}` becomes
`Example Show - S01E01E02 - Pilot + Second.mkv`. Settings control naming across the library; the Metadata tab shows a rename
preview without duplicate format editors. Changing a setting never renames existing media automatically.

About contains version details, deployment checks, diagnostics, config backups
and sample NFO checks, with both panels always expanded. File and format holds
the shared replacement selection preference; with it off, existing files start
unticked in the review. Each replacement must be selected before confirming.

The movie/show workspace uses one main **Review and save changes** action below
its tabs. Actor-only and individual image save shortcuts remain available.
Actor and artwork tabs offer Select all and Clear selection; artwork Select all
chooses the first available image per destination to avoid conflicting outputs.
Saved-image highlights remain visible. Descriptive metadata is edited in Details,
while Metadata contains technical file details, rename previews and maintenance
actions. Named TV formats are managed from Settings → TV Shows.

### TV artwork sources and Show Details image

In a show's Artwork tab, **Refresh artwork from all providers** gathers available
fanart.tv, TVDB and TMDB choices. Images remain grouped by category and show their
provider and language. Configure each service's key in Settings → API. A failed
or unconfigured service does not hide images from the others. TMDB artwork uses
an exact TVDB-ID lookup; ambiguous or missing matches are reported rather than
guessed from titles. TVDB remains the show/episode metadata source.

Preview an image and choose **Use in Show Details**. Remote images first go
through the normal reviewed download; saved images can be selected directly.
Show Details also has a dropdown for saved show/season artwork. The display
choice is stored in SQLite, independently of the standard poster used by media
players. Default poster restores the normal selection. Browsing artwork never
automatically replaces files.

Provider references: [TMDB external-ID lookup](https://developer.themoviedb.org/reference/find-by-id),
[TMDB TV images](https://developer.themoviedb.org/reference/tv-series-images),
[TVDB v4 schema](https://github.com/thetvdb/v4-api/blob/main/docs/swagger.yml).

Movie artwork also supports fanart.tv, TMDB and TVDB. Open Artwork and click
**Refresh artwork from all providers**. Provider/language labels, full-size
previews, saved highlights and reviewed downloads use the same workflow as TV.
TMDB uses the selected movie ID; TVDB uses that movie's IMDb identifier and skips
missing or ambiguous matches. No title-based artwork match is applied silently.
Posters, backgrounds, logos and other available categories share compatible
output filenames. Selecting another image for the same filename clears the
previous choice. Provider errors leave the other services' choices available.
Configure the existing keys in Settings → API; no new credential fields are needed.
See [TMDB movie images](https://developer.themoviedb.org/reference/movie-images)
and [TVDB v4 search](https://thetvdb.github.io/v4-api/).

### Unraid Compose with a locally loaded image

After loading `artflix:0.4.0` into Unraid's Docker engine, copy
`compose.unraid.yml` and `unraid.env.example` to a deployment directory such as
`/mnt/user/appdata/artflix-deploy`. Copy the example to `.env`, then set the exact
existing movie and TV share roots. Create `/mnt/user/appdata/artflix` and set its
owner to UID 99/GID 100. With Docker Compose v2 available, run:

```bash
docker compose -f compose.unraid.yml config
docker compose -f compose.unraid.yml up -d
```

This standalone file uses the imported image without pulling or rebuilding,
keeps all four media mounts read-only, and enables `ARTFLIX_READ_ONLY=true`.
Missing host directories cause an error rather than being created silently.
Use Compose from the same deployment directory for subsequent changes. Inside
ArtFlix use `/movies`, `/movies-write`, `/tv`, `/tv-write` as the library paths.

## Source and container updates

Source: https://github.com/art101mark46/Artflix

Published image: `ghcr.io/art101mark46/artflix:latest`. GitHub Actions tests each
change before publishing. See [GitHub and Unraid updates](docs/github-updates.md)
for switching an existing installation without changing its appdata or media
mappings. The local-image Compose example is retained for offline installations.

### TV scan progress and recovery (0.4.1)

The TV scan first counts video files without opening their contents. Expand
**Episode scan progress** in the scan panel to see the current show/episode file,
time on that file, checked/remaining video counts and the current show's progress.
A combined-episode video counts as one file; extras, actor folders and symlinks
are excluded. The initial count reflects the files discovered at scan time.

Status is saved atomically under `/config/tv-scan-status.json`. Completed-folder
checkpoints and per-video technical details are stored in SQLite. After a server
restart or cancelled scan, open TV Shows and click **Resume scan**. Discovery
runs again to detect changes, unchanged completed shows are skipped, and a partial
show reuses successful probes for unchanged videos. Subsequent normal scans use
the same incremental behaviour. Changed NFO/artwork/file statistics invalidate a
folder checkpoint; changed video size/mtime invalidates its technical-details
cache. Failed probes remain visible as warnings and are retried on later scans.
Scanning only reads the media shares; index and checkpoint writes go to config.

An interrupted scan from a version before 0.4.1 has no saved job status, but its
existing successful video-probe cache is still reused when Scan is clicked.

### Skipped-file reports (0.4.3)

Movie and TV scan warnings are saved immediately in the `scan_issues` table in
`/config/artflix.sqlite3`. Open Settings → About → Skipped files and scan warnings,
then Show scan warnings, or download the full text report from there or the scan
warning panel. The view lists the newest 200; the export includes the full history.
Each record includes the library, time, file/folder path and reason. For malformed
filenames, the report also retains the exact original path bytes in hexadecimal
so the intended file can be identified without guessing its encoding. Reports
survive cancelled scans and restarts. Historical entries remain after corrections;
scan again to check the corrected file. Collection begins with this release.
