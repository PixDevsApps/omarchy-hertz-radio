# Changelog

## 1.2.0 — 2026-09-25

Full independent security audit; all findings fixed.

- **Sandbox rebuilt from nothing.** mpv (and now the logo decoder) run with
  only `/usr` and a few files from `/etc`. They get no home folder, no
  session or system sockets (D-Bus, Wayland, X11, PipeWire, systemd, agents,
  Docker), an empty environment, all namespaces unshared, nested user
  namespaces disabled, no capabilities, and a seccomp filter. Previously the
  whole filesystem was visible read-only, and sockets on it were reachable.
- **Audio leaves the sandbox as raw PCM** played by `pw-cat`, so the player
  needs no audio server or D-Bus access.
- **MPRIS is published by hertz-ctl itself**, with the artist, song, station
  and logo. `OpenUri` is refused.
- **Logos are decoded only in the sandbox** (ffmpeg with pixel, memory, CPU
  and time limits) and re-encoded to a ≤160×160 PNG. The shell never
  decodes downloaded files. Old cached logos are removed.
- **LAN protection:** destinations must be reached through a gateway on a
  default-route interface. That also refuses LAN devices with public IPv6
  addresses, and VPN-specific routes.
- Resource limits inside the sandbox: 256 processes and threads and 1.5 GB of
  address space for the player; the logo decoder is limited more tightly.
  Player logs are capped.
- The sandbox can write only to its own control folder. State, session and
  logs are written through fresh temp files or with `O_NOFOLLOW`.
- Every station record, including those read back from disk, is validated
  by `clean_station()`.
- mpv IPC lines are capped at 1 MiB; the directory API is HTTPS-only,
  redirects included; the route checks respect request deadlines.
- New tests: sandbox escape probe against every socket on the machine,
  logo transcoding and decompression bombs, record validation, and IPC and
  file robustness.

## 1.1.1 — 2026-09-25

- Every logo and directory request now has a total deadline (15 s per logo,
  20 s per mirror, 45 s across mirrors) covering lookup, connect, redirects,
  headers and body. A watchdog shuts the sockets down when it passes, so a
  server trickling one byte per socket timeout can no longer hold the artwork
  workers. The player proxy's lookup + connect are bounded the same way.
  Reported in marketplace review.

## 1.1.0 — 2026-09-24

Security review, prompted by marketplace review of 1.0.1.

- **Sandboxed player.** mpv now runs in bubblewrap with its own empty network
  namespace, no capabilities and a read-only filesystem. Its only way out is a
  guarded HTTP/HTTPS proxy, so redirects, `.m3u`/`.pls` entries, HLS segments
  and mpv's own DNS lookups can no longer reach local or private addresses.
  The sandbox checks itself and refuses to start the player if it isn't sealed.
- Address policy also refuses IPv6 forms embedding a private IPv4 address
  (6to4, Teredo, NAT64, IPv4-compatible) and addresses that route to this
  machine; WHATWG "bad ports" are refused.
- Stream TLS certificates are verified (`--tls-verify=yes`); yt-dlp is never
  used (`--ytdl=no`).
- Logos: format is taken from the bytes, not the Content-Type; SVG is no
  longer shown; the cache is bounded.
- The runtime folder must be a private, user-owned directory (no symlinks);
  files are created with `umask 077`.
- `SECURITY.md` describes the threat model; `tests/test_player_sandbox.py`
  tests the real sandboxed player against hostile URLs.

## 1.0.1 — 2026-09-24

- Security: station logo and stream URLs from the directory can no longer
  reach local or private-network addresses. All requests resolve the host,
  require every address to be public, connect to that exact address, check
  each redirect hop (at most 3), and logos are limited to ports 80 and 443.
  Streams get the same check before playback. Reported in marketplace review.
- A download error can no longer stop the artwork worker threads.
- Tests for the network guard in `tests/`.

## 1.0.0 — 2026-09-24

First public release.

- Genre lists rank stations whose own main genre matches first, so "Jazz" opens
  with jazz stations rather than big stations that merely carry a jazz tag.
- Row subtitles skip tags that are just the station's state or language.
- The selected genre chip scrolls into view, including a remembered genre.
- The runtime directory falls back to the user's cache folder, never to a
  shared `/tmp` path, when `XDG_RUNTIME_DIR` is unset.
- Marketplace preview and README screenshots.

Earlier versions below were development builds that were never published.

## 0.5.1 — 2026-09-24

- No open-panel underline under the bar widget.
- The "Stations from radio-browser.info" line is gone from the panel; the credit
  now lives in the plugin description and README.

## 0.5.0 — 2026-09-24

- Paused and off no longer use bandwidth. Pause disconnects the stream at
  once, and Play rejoins live. A media-key pause holds the stream for 10 s,
  then disconnects. An idle player is shut down after 5 minutes.
- Power button in the player (also right click on the bar icon, or `S`) turns
  the radio off and closes the player process; the station stays selected.
- The bar shows the now-playing title only while playing; otherwise just the icon.
- mpv is killed if it fails to open its control socket, so no player is left behind.

## 0.4.0 — 2026-09-24

- Infinite scroll: the station list loads the next page as you near its end
  (mouse or keyboard), replacing the Load more button. A small waveform breathes
  while a page loads, and a failed page offers Retry.
- Single queries (one genre tag, a country, or all stations) page on the server,
  so scrolling never re-downloads earlier pages, up to 10,000 stations.
  Merged queries (search text, multi-tag genres) go up to 800 per source.

## 0.3.0 — 2026-09-24

- Now playing (artist – song) shown in the bar to the right of the icon, falling
  back to the station name. Long titles scroll left in a loop (pausing on the
  artist, which is drawn bold), within 150 px by default. The text dims while paused.
  It is clickable like the icon. `showTitle` and `maxTitleWidth` settings.

## 0.2.0 — 2026-09-24

- Country filter: a searchable country picker next to the search field that
  combines with the genre chips and the search text. Press `C` to open it.
- Readable country names ("United States" rather than "The United States Of America").
- The last country is remembered along with the last genre.

## 0.1.0 — 2026-09-24

First release.

- Bar icon with a waveform glyph that breathes while a station is live.
- Player: tinted station artwork, live track metadata, play/pause, previous/next
  station, favorite and volume.
- Station browser that expands in place: search, genre chips, favorites and Load more.
- Station artwork re-coloured in the active theme through a duotone shader.
- Playback in mpv with MPRIS through mpv-mpris; streams survive shell restarts.
- Keyboard control and cached results when the directory is unreachable.
