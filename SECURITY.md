# Security

Hertz Radio plays stations from [Radio Browser](https://www.radio-browser.info),
a public directory that anyone can edit. Everything in a station record, and
everything a stream sends back, is treated as untrusted.

## What is untrusted

| Input | Where it goes | How it is handled |
|---|---|---|
| Station names, tags, countries | Panel, bar, tooltips | Rendered as plain text only (`Text.PlainText`), never as rich text or HTML. |
| Logo (favicon) URLs | Downloaded automatically for listed stations | Guarded opener (below), ports 80/443, max 768 KB, format decided from the bytes (PNG, JPEG, GIF, WebP, ICO, BMP; no SVG), cached under `~/.cache/hertz-radio/art/` with a bounded size. |
| Stream URLs | Opened only when you press play | Played by mpv inside the network sandbox (below). |
| Stream contents: redirects, `.m3u`/`.pls`, HLS playlists and segments, ICY metadata | mpv / ffmpeg | Every connection mpv makes goes through the guarded proxy. ICY titles are plain text, max 200 characters. |
| Directory API responses | `hertz-ctl` | HTTPS with certificate checks, size-capped (4 MB), parsed as JSON, reduced to a fixed set of fields. |

## Network policy

Every outgoing connection, from `hertz-ctl` itself or from the player, is
made by `public_connection()`:

- The host is resolved first, and **every** returned address must be a
  globally routable unicast address. Loopback, private (RFC 1918, ULA),
  link-local (including `169.254.169.254`), CGNAT, multicast, reserved and
  unspecified addresses are refused, as are IPv6 forms that embed a
  non-public IPv4 address (IPv4-mapped, 6to4, Teredo, NAT64, IPv4-compatible).
- Addresses the kernel would deliver to this machine (for example its own
  public IP) are refused (`ip route get`; fails closed).
- The socket connects to exactly the address that was checked, so a second
  DNS answer cannot swap in a local one (DNS rebinding).
- Ports on the WHATWG Fetch "bad ports" list (SMTP, SSH, DNS, IMAP, ...)
  are refused; logos may only use ports 80 and 443.
- Redirects are followed at most 3 times, each hop checked the same way,
  http/https only. No proxy, FTP or `file:` handlers are installed.

## Player sandbox

mpv resolves names, follows redirects and expands playlists on its own, so
checking a stream URL before playback would not be enough. `hertz-ctl player`
therefore runs mpv like this:

1. A guarded HTTP/HTTPS (CONNECT) proxy starts outside the sandbox, on a Unix
   socket in the private runtime folder `$XDG_RUNTIME_DIR/hertz-radio/` (mode
   0700, ownership and symlinks checked). Every request is checked with the
   network policy above.
2. bubblewrap starts the sandbox: read-only filesystem except the runtime
   folder, minimal `/dev`, own PID namespace, new session, dies with its parent.
3. Inside, `hertz-ctl sandbox-net` enters a new user and network namespace
   that contains only a loopback interface, then re-executes itself as the
   normal user, so the kernel clears every capability.
4. `hertz-ctl sandbox` verifies it has no capabilities and no route to the
   internet (and refuses to start the player otherwise), bridges
   `127.0.0.1:<port>` to the proxy socket, and runs mpv with that proxy.

mpv therefore has no direct network access at all: DNS, raw TCP, UDP or RTSP
from inside have no route, and all HTTP(S) goes through the proxy. It also
runs with `--ytdl=no` (no yt-dlp), `--load-unsafe-playlists=no` and
`--tls-verify=yes`. If bubblewrap or user namespaces are unavailable, playback
does not start; it never falls back to an unsandboxed player.

## Local surfaces

- `hertz-ctl` never uses a shell; child processes get argument lists. mpv is
  driven over its JSON IPC socket with fixed commands.
- The mpv IPC socket and the proxy socket live in the private runtime folder.
- State and cache files are created with `umask 077`.
- The plugin does not change `shell.json` or any other configuration.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

`tests/test_network_guard.py` covers the address policy, ports, redirects,
the proxy's request handling, logo formats and the private folder check.
`tests/test_player_sandbox.py` runs the real sandboxed player against hostile
URLs (loopback, LAN address, public redirect to loopback, `.m3u` and HLS
playlists pointing at loopback, raw `tcp://`) and asserts that a local server
receives no request. A control run with plain mpv confirms every one of those
URLs does reach the server without the sandbox.

## Reporting

Please open an issue at
https://github.com/PixDevsApps/omarchy-hertz-radio/issues.
