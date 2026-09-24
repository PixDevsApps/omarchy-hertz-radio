.pragma library

// Pure helpers for the Hertz Radio panel. No QML types, no state.

// Nerd Font (Material Design) glyphs used by the panel.
var ICON = {
  play: String.fromCodePoint(0xF040A),
  pause: String.fromCodePoint(0xF03E4),
  prev: String.fromCodePoint(0xF04AE),
  next: String.fromCodePoint(0xF04AD),
  stop: String.fromCodePoint(0xF04DB),
  power: String.fromCodePoint(0xF0425),
  heart: String.fromCodePoint(0xF02D1),
  heartOutline: String.fromCodePoint(0xF02D5),
  search: String.fromCodePoint(0xF0349),
  close: String.fromCodePoint(0xF0156),
  volumeHigh: String.fromCodePoint(0xF057E),
  volumeMedium: String.fromCodePoint(0xF0580),
  volumeLow: String.fromCodePoint(0xF057F),
  volumeOff: String.fromCodePoint(0xF0581),
  retry: String.fromCodePoint(0xF0450)
}

function volumeIcon(volume, muted) {
  if (muted || volume <= 0) return ICON.volumeOff
  if (volume < 34) return ICON.volumeLow
  if (volume < 67) return ICON.volumeMedium
  return ICON.volumeHigh
}

// Two letters for the artwork fallback tile: "Radio Paradise" -> "RP".
function initials(name) {
  var words = String(name || "").replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/)
  if (!words.length || !words[0]) return "Hz"
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}

// Radio Browser names often carry the stream format ("… 320k AAC", "(EU)");
// the panel shows format separately, so trim that noise from the title.
function cleanName(name) {
  var n = String(name || "")
  var cleaned = n
    .replace(/\s*[\(\[]?\b\d{2,4}\s?k(bps)?\b[\)\]]?/gi, "")
    .replace(/\s*\b(AAC\+?|MP3|OGG|OPUS|FLAC|HLS)\b/gi, "")
    .replace(/\s*[-–|:]\s*$/, "")
    .replace(/\s{2,}/g, " ")
    .trim()
  return cleaned || n
}

// "Artist - Song" -> { artist, song }. Titles without a " - " separator (or
// with an empty side) come back as { artist: "", song: title }.
function splitTitle(title) {
  var t = String(title || "").trim()
  var m = t.match(/^(.+?)\s+[-–—]\s+(.+)$/)
  if (!m) return { artist: "", song: t }
  return { artist: m[1].trim(), song: m[2].trim() }
}

function quality(station, codec) {
  if (!station) return ""
  var c = codec || station.codec || ""
  if (c === "UNKNOWN") c = ""
  var parts = []
  if (c) parts.push(c)
  if (station.bitrate > 0) parts.push(station.bitrate + "k")
  return parts.join(" ")
}

function countryName(station) {
  if (!station) return ""
  var c = String(station.country || "")
  var short = {
    "United States": "USA",
    "United Kingdom": "UK",
    "The United States Of America": "USA",
    "The United Kingdom Of Great Britain And Northern Ireland": "UK",
    "The Russian Federation": "Russia",
    "The Netherlands": "Netherlands",
    "Republic Of Korea": "South Korea"
  }
  return short[c] || c.replace(/^The /, "")
}

// "Jazz · Germany · AAC 128k" style subtitle for list rows.
function subtitle(station, hideCountry) {
  if (!station) return ""
  var parts = []
  if (station.genre) parts.push(station.genre)
  var country = hideCountry ? "" : countryName(station)
  if (country) parts.push(country)
  var q = quality(station)
  if (q) parts.push(q)
  return parts.join("  ·  ")
}

function capitalize(s) {
  s = String(s || "")
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function searchKey(genre, country, text) {
  return genre + "|" + (country || "") + "|" + String(text || "").trim().replace(/\s+/g, " ").slice(0, 80).toLowerCase()
}

// SearchableDropdown options: "All countries" first, then A–Z with counts.
function countryOptions(countries) {
  var out = [{ value: "", label: "All countries" }]
  for (var i = 0; i < countries.length; i++) {
    var c = countries[i]
    out.push({ value: c.code, label: c.name, description: c.count.toLocaleString() + (c.count === 1 ? " station" : " stations") })
  }
  return out
}

// Blend two colors: t = 0 -> a, t = 1 -> b. Alpha is forced opaque.
function mix(a, b, t) {
  return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t, 1)
}

function luminance(c) {
  return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b
}
