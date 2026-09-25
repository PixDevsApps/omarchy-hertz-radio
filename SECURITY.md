# Security

Hertz Radio plays stations from [Radio Browser](https://www.radio-browser.info),
a public directory anyone can edit. Everything in a station record, and
everything a stream or logo server sends back, is treated as hostile.

This document says what is protected, how, and what is **not**.

## Components

| Part | Runs | Handles untrusted data by |
|---|---|---|
| `Panel.qml` and friends | in the Omarchy shell | Showing text as plain text only, and images only from the logo cache (PNGs made by the sandbox, below). |
| `hertz-ctl daemon` | as you, unsandboxed | Talking to Radio Browser, downloading logos (bytes only, never decoded here), validating every station record, and driving the player over a socket. |
| `hertz-ctl player` | as you, unsandboxed | Running the guarded proxy and `pw-cat`, and starting the sandbox. |
| mpv (the player) | in the sandbox | Decoding streams: the riskiest code, fully sandboxed. |
| ffmpeg (logo decoder) | in the sandbox | Decoding logos into a small PNG, fully sandboxed. |

## The sandbox

mpv and the logo decoder run in the same sandbox, which starts from
nothing (`sandbox_command()` in `hertz-ctl`):

- **Filesystem:** an empty mount namespace with `/usr` read-only, the host's
  `/bin` and `/lib` symlinks, and only the loader, TLS, font and timezone
  files from `/etc`. The root and `/dev` are read-only. The only writable
  places are a private 16 MB `/tmp` and a 1 MB `/run`, both inside the
  sandbox. There's no `/home` and nothing of your session: no D-Bus,
  Wayland, X11, PipeWire, systemd, agents, Docker or other sockets. The
  sandbox has **no path shared with the host that it can write to**.
- **Identity:** hostname `hertz`, empty environment except `PATH`,
  `HOME=/tmp` and `LANG`, and `/proc/cmdline` hidden. The helper script is
  copied in rather than bind-mounted, so no host path appears. The sandbox
  can still see your numeric user ID and the list of installed packages in
  `/usr`.
- **Namespaces:** new user, PID, network, IPC, UTS and cgroup namespaces.
  The network namespace holds only loopback. Nested user namespaces are
  disabled.
- **Privileges:** no capabilities, including the bounding set, and a new
  session (no terminal injection).
- **Syscalls:** a seccomp filter refuses io_uring, bpf, userfaultfd,
  perf_event_open, keyctl and the key syscalls, mount/pivot_root and the new
  mount API, unshare/setns, ptrace and process_vm_*, modify_ldt, module and
  kexec loading, and similar interfaces. `socket`/`socketpair` are limited
  to Unix, IPv4 and IPv6 (no vsock, RDS, AF_ALG, netlink, packet…), and
  `personality` to its default and query values. Any non-x86-64 or x32
  syscall kills the process, so the list can't be sidestepped. On other
  architectures the filter is omitted, and the rest of the sandbox still
  applies.
- **Resources:** processes and threads are capped inside the sandbox (256
  for the player, 32 for the logo decoder), counted only within its own user
  namespace. mpv gets 1.5 GB of address space. The logo decoder gets 512 MB,
  10 s of CPU, a 15 s timeout, at most 4096×4096 pixels, and a single thread.
  Player output logs are capped.
- **Fail closed:** before starting mpv, the sandbox checks that it has no
  capabilities, no `/home`, no route to the internet and an active
  socket-family filter. If bubblewrap, user namespaces, pw-cat or ffmpeg are
  missing, playback or logos simply don't happen. Nothing ever falls back to
  running unsandboxed.

**Audio.** mpv writes raw PCM to its standard output, and `pw-cat` plays it
outside the sandbox.

**Control.** mpv's control connection is a socket inherited from the player
session. The session relays it to a socket in the private runtime folder,
which the sandbox can't see. Everything mpv reports is treated as hostile:
values are type-checked, text is cleaned (control and bidi characters
removed) and length-limited, messages are capped at 1 MiB, and malformed
input is dropped. The same cleaned text goes to the panel and to MPRIS.
Output to the panel is ASCII-escaped JSON.

**Stopping.** "Off" asks mpv to quit, then ends the whole player process
group (SIGTERM, then SIGKILL) if it's still there a few seconds later, so a
player that ignores the request can't keep running.

**Media keys** (MPRIS) are published by the daemon, not by mpv. MPRIS
`OpenUri` is refused.

**Network.** The sandbox's only way to the network is the guarded proxy,
through a bridge on its private loopback. The proxy accepts plain HTTP
requests and HTTPS `CONNECT` tunnels, and a tunnel carries arbitrary TCP.
So the sandbox can open TCP connections to **public** addresses, on ports
the policy allows. It has no DNS, no UDP, and no direct connections.

## Network policy

Every outgoing connection, from the daemon (API, logos) or from the player
through the proxy, is made by `public_connection()`:

- The host is resolved, and **every** returned address must be global
  unicast. Loopback, private, link-local (including `169.254.169.254`),
  CGNAT, multicast, reserved and unspecified addresses are refused.
  IPv4-mapped (`::ffff:a.b.c.d`) and IPv4-compatible IPv6 are refused
  outright. 6to4 and Teredo must embed a public IPv4 address. NAT64
  (`64:ff9b::/96`) is allowed only when it embeds a public IPv4 address.
- The kernel's route to the address must go **through a gateway, on an
  interface that carries a default route**. The exception is a
  point-to-point default route with no gateway itself, such as a
  full-tunnel VPN or PPP. The check uses `ip route get` and fails closed.
  That refuses this machine's own addresses, everything directly on the LAN
  (including LAN devices with public IPv6 addresses) and specific routes into
  VPNs.
- The socket connects to exactly the checked address (no DNS rebinding).
- Ports on the WHATWG Fetch "bad ports" list are refused. Logos must use
  ports 80 or 443. The directory API is HTTPS-only, redirects included.
- Redirects: at most 3, each hop re-checked.
- Every logo and API request, and the pre-play address check, has a
  **total** deadline (15 s per logo, 20 s per mirror, 45 s across mirrors,
  10 s before play). It covers lookup, connect, redirects, headers and body.
  The proxy's lookup and connect have a 15 s deadline. After that it relays
  the live stream with an idle limit.

## Logos

A logo is downloaded as bytes (at most 768 KB), must look like PNG, JPEG,
GIF, WebP, ICO or BMP, and is decoded **only** by ffmpeg in the sandbox. ffmpeg
is told the input format from the sniffed type (it doesn't probe) and writes
a PNG of at most 160×160. `rebuild_png()` then parses that output strictly
(8-bit RGBA, no interlacing, valid CRCs, known chunks only, exact
decompressed size, valid filter bytes, bounded decompression) and writes a
**fresh PNG from the pixel data**. That rebuilt PNG is all that's cached and
all the shell ever decodes, so even a compromised decoder can hand Qt only a
well-formed small image written by our own code. Logos stored by versions
before 1.2 are deleted.

## Records and local files

- Every station record, whether from the directory or read back from
  favorites, the session file or the result cache, goes through
  `clean_station()`. It needs a valid UUID and an http(s) URL; text becomes
  single-line printable and length-limited.
- State and cache files are created with `umask 077`, through fresh temp
  files (`mkstemp`) and atomic renames. Logs are opened with `O_NOFOLLOW`.
- The runtime folder must be a private (0700), user-owned, non-symlink
  directory. The sandbox can write only to its own subfolder there.
- No shell is ever used; child processes get argument lists.
- The plugin doesn't change `shell.json` or any other configuration.

## What is not protected

- **The sandbox still reaches the public internet** (it's a radio). Code
  running inside it could open TCP connections to public hosts, through
  HTTP requests or CONNECT tunnels, and produce sound. It has no personal
  files or session sockets to use or send, but it can see your numeric user
  ID and which packages are installed.
- **Your own public addresses reached via the router** can't be told apart
  from the rest of the internet:
  - your router's WAN address (hairpin NAT);
  - other subnets of your ISP-delegated IPv6 prefix that the router forwards
    (only directly connected LANs are refused).

  Keep router and device admin interfaces closed to requests from the
  internet side.
- **Kernel bugs** reachable through the syscalls that remain allowed. The
  seccomp filter reduces this surface; it can't remove it.
- **The unsandboxed parts** (daemon, player session, proxy, `pw-cat`) parse
  directory JSON, HTTP headers, mpv's IPC messages and raw PCM. They are
  kept small, bounded and tested, but they aren't sandboxed.
- **Stream TLS** is verified (`--tls-verify=yes`), but a station's HTTP
  streams are, by nature, unencrypted.

## Privacy

- Browsing requests station lists from a Radio Browser mirror and, for each
  listed station, its logo from that station's own server (about 40 per
  page). Those servers see your IP address and a `HertzRadio/<version>`
  user agent.
- Playing a station connects to its stream server and reports the play to
  Radio Browser (anonymous, as Radio Browser's own apps do).
- No accounts, analytics or tracking. Favorites stay on your machine.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

| File | Covers |
|---|---|
| `tests/test_network_guard.py` | Address policy (including IPv4-mapped, NAT64 and 6to4 forms), LAN/own addresses, routing rules for LAN, VPN, full-tunnel and multipath setups, ports, HTTPS-only API, redirects, the proxy's request handling, deadlines (trickling headers and bodies, a hanging DNS lookup) |
| `tests/test_sandbox_escape.py` | Runs a probe inside the real sandbox against every socket and personal file that exists on the machine, the environment, capabilities, blocked syscalls and socket families, host identity leaks, writable paths and tmpfs limits. A control run without the sandbox confirms the probe finds the sockets |
| `tests/test_player_sandbox.py` | The real player against hostile URLs (loopback, LAN, redirect to loopback, `.m3u` and HLS playlists pointing at loopback, `tcp://`), with unsandboxed controls; public HTTP, HTTPS and HLS still play |
| `tests/test_hardening.py` | Logo transcoding (all formats, decompression bombs, corrupt input, pinned decoder, PNG rebuild), a hostile fake mpv (lone surrogates, escape sequences, wrong types, nested JSON, huge strings), killing a player that ignores quit and SIGTERM, record validation, a corrupt session file, the IPC line cap, symlink-safe writes |

Mutation checks were run for each layer: removing a protection makes its
tests fail.

## Reporting

Please open an issue at
https://github.com/PixDevsApps/omarchy-hertz-radio/issues.
