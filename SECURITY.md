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
  files from `/etc`. There's no `/home`, a private empty `/tmp` and `/run`, and
  nothing of your session: no D-Bus, Wayland, X11, PipeWire, systemd, agents,
  Docker or other sockets. The player's only writable path is a folder that
  holds nothing but its control socket.
- **Namespaces:** new user, PID, network, IPC, UTS and cgroup namespaces.
  The network namespace holds only loopback. Nested user namespaces are
  disabled.
- **Privileges:** no capabilities, including the bounding set, and a new
  session (no terminal injection).
- **Environment:** empty except `PATH`, `HOME=/tmp` and `LANG`.
- **Syscalls:** a seccomp filter refuses io_uring, bpf, userfaultfd,
  perf_event_open, keyctl and key syscalls, mount/pivot_root and the new
  mount API, unshare/setns, ptrace and process_vm_*, module and kexec
  loading, and similar interfaces. Any non-x86-64 or x32 syscall kills the
  process, so the list can't be sidestepped. On other architectures the
  filter is omitted, and the rest of the sandbox still applies.
- **Resources:** inside the sandbox, processes and threads are capped
  (256 for the player, 32 for the logo decoder), counted only within the
  sandbox's own user namespace. mpv gets 1.5 GB of address space. The logo
  decoder gets 512 MB, 10 s of CPU, a 15 s timeout, at most 4096×4096 pixels,
  and a single thread. Player output logs are capped.
- **Fail closed:** before starting mpv, the sandbox checks that it has no
  capabilities, no `/home` and no route to the internet. If bubblewrap, user
  namespaces, pw-cat or ffmpeg are missing, playback or logos simply don't
  happen. Nothing ever falls back to running unsandboxed.

**How audio and control get out.** mpv writes raw PCM to its standard
output, and `pw-cat` plays it outside the sandbox. mpv is controlled over its
JSON IPC socket; the daemon reads its messages with a 1 MiB line cap and
treats their contents as untrusted. Media keys (MPRIS) are published by the
daemon, not by mpv. MPRIS `OpenUri` is refused.

**How the network gets out.** The sandbox's only way to reach the network
is the guarded proxy (below), reached through a bridge on its private
loopback. HTTP and HTTPS work; DNS, raw TCP, UDP and RTSP have no route.

## Network policy

Every outgoing connection, from the daemon (API, logos) or from the player
through the proxy, is made by `public_connection()`:

- The host is resolved, and **every** returned address must be global unicast.
  Loopback, private, link-local (including `169.254.169.254`), CGNAT,
  multicast, reserved and unspecified addresses are refused, as are IPv6
  forms that embed a non-public IPv4 address.
- The address must be reached **through a gateway, on an interface that
  carries a default route** (`ip route get`, failing closed). That refuses
  this machine's own addresses, everything directly on the LAN (including
  LAN devices with public IPv6 addresses) and specific routes into VPNs.
- The socket connects to exactly the checked address (no DNS rebinding).
- Ports on the WHATWG Fetch "bad ports" list are refused. Logos must use
  ports 80 or 443. The directory API is HTTPS-only, redirects included.
- Redirects: at most 3, each hop re-checked.
- Every logo and API request has a **total** deadline (15 s per logo, 20 s
  per mirror, 45 s across mirrors), covering lookup, connect, redirects,
  headers and body. The proxy's lookup and connect have a 15 s deadline.
  After that it relays the live stream with an idle limit.

## Logos

A logo is downloaded as bytes (at most 768 KB), must look like PNG, JPEG,
GIF, WebP, ICO or BMP, and is decoded **only** by ffmpeg in the sandbox,
which writes a PNG of at most 160×160. That PNG is checked and is all that's
cached and all the shell ever decodes. Logos stored by versions before 1.2
are deleted.

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

- **The sandbox still plays audio and reaches the public internet** (it's a
  radio). Code running inside it could make HTTP(S) requests to public hosts
  and produce sound. It has no personal data to send.
- **Hairpin NAT:** your router's public (WAN) address can't be told apart
  from any other public address, so a station could make the player request
  it. Many routers answer that from the inside. Keep router admin interfaces
  closed to the WAN side.
- **Kernel bugs** reachable through the syscalls that remain allowed. The
  seccomp filter reduces this surface; it can't remove it.
- **The unsandboxed parts** (daemon, proxy, `pw-cat`) parse directory JSON,
  HTTP headers, mpv's IPC messages and raw PCM. They are kept small, bounded
  and tested, but they aren't sandboxed.
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
| `tests/test_network_guard.py` | Address policy, LAN/own addresses, ports, HTTPS-only API, redirects, the proxy's request handling, deadlines (trickling headers and bodies, a hanging DNS lookup) |
| `tests/test_sandbox_escape.py` | Runs a probe inside the real sandbox against every socket and personal file that exists on the machine, the environment, capabilities and blocked syscalls. A control run without the sandbox confirms the probe finds them |
| `tests/test_player_sandbox.py` | The real player against hostile URLs (loopback, LAN, redirect to loopback, `.m3u` and HLS playlists pointing at loopback, `tcp://`), with unsandboxed controls; public HTTP, HTTPS and HLS still play |
| `tests/test_hardening.py` | Logo transcoding (all formats, decompression bombs, corrupt input), record validation, a corrupt session file, the IPC line cap, symlink-safe writes |

Mutation checks were run for each layer: removing a protection makes its
tests fail.

## Reporting

Please open an issue at
https://github.com/PixDevsApps/omarchy-hertz-radio/issues.
