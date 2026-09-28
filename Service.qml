import QtQuick
import Quickshell.Io
import "Model.js" as Model

// One controller for all bar instances. The shell owns this service, so a
// monitor appearing or disappearing cannot create another daemon.
Item {
  id: root

  property bool connected: false
  property var station: null
  property bool playing: false
  property bool paused: false
  property bool buffering: false
  property string nowTitle: ""
  property string codec: ""
  property int volume: 70
  property bool muted: false
  property bool favorite: false
  property bool hasNext: false
  property string playError: ""

  property var genres: []
  property string genre: "All"
  property string searchText: ""
  property string country: ""          // ISO code, "" = all countries
  property var countries: []
  property var stations: []
  property var favorites: []
  property var art: ({})
  property int page: 0
  property bool more: false
  property bool loading: false
  property string listError: ""
  property bool fetched: false

  readonly property string currentKey: Model.searchKey(genre, country, searchText)
  property string ctlPath: decodeURIComponent(String(Qt.resolvedUrl("hertz-ctl")).replace(/^file:\/\//, ""))

  function send(line) {
    if (backend.running) backend.write(line + "\n")
  }

  function search(pageNumber) {
    searchDebounce.stop()
    page = pageNumber || 0
    loading = true
    listError = ""
    fetched = true
    send("search " + JSON.stringify({ genre: genre, country: country, text: searchText, page: page }))
  }

  function loadMore() {
    if (more && !loading && listError === "" && genre !== "Favorites" && stations.length > 0)
      search(page + 1)
  }

  function selectCountry(code) {
    if (country === code && fetched) return
    country = code
    search(0)
  }

  function selectGenre(name) {
    if (genre === name && fetched) return
    genre = name
    search(0)
  }

  function setSearchText(text) {
    if (searchText === text) return
    searchText = text
    searchDebounce.restart()
  }

  function setVolume(value) {
    volume = Math.max(0, Math.min(100, Math.round(value)))
    send("volume " + volume)
  }

  function handle(line) {
    var msg
    try { msg = JSON.parse(line) } catch (e) { return }
    if (msg.type === "state") {
      station = msg.station || null
      playing = !!msg.playing
      paused = !!msg.paused
      buffering = !!msg.buffering
      nowTitle = msg.title || ""
      codec = msg.codec || ""
      volume = msg.volume
      muted = !!msg.muted
      favorite = !!msg.favorite
      hasNext = !!msg.hasNext
      playError = msg.error || ""
    } else if (msg.type === "stations") {
      if (msg.key !== currentKey) return
      if (msg.page > 0) {
        // Rankings can shift between pages; never show a station twice.
        var have = {}
        for (var i = 0; i < stations.length; i++) have[stations[i].uuid] = true
        stations = stations.concat(msg.items.filter(function(s) { return !have[s.uuid] }))
      } else {
        stations = msg.items
      }
      more = !!msg.more
      if (!msg.cached) loading = false
      listError = ""
    } else if (msg.type === "loading") {
      if (msg.key === currentKey) loading = true
    } else if (msg.type === "stations-failed") {
      if (msg.key !== currentKey) return
      loading = false
      listError = msg.message || "Radio directory unreachable"
      if (msg.page > 0) page = msg.page - 1   // so a retry asks for the same page
    } else if (msg.type === "favorites") {
      favorites = msg.items || []
      if (genre === "Favorites" && fetched) search(0)
    } else if (msg.type === "art") {
      var next = Object.assign({}, art)
      next[msg.uuid] = msg.path
      art = next
    } else if (msg.type === "genres") {
      var list = msg.items || []
      var i = list.indexOf("All")
      genres = (i >= 0 ? ["All", "Favorites"].concat(list.slice(0, i), list.slice(i + 1)) : ["Favorites"].concat(list))
      if (msg.current && genres.indexOf(msg.current) >= 0 && !fetched) genre = msg.current
      if (!fetched) country = msg.country || ""
    } else if (msg.type === "countries") {
      countries = msg.items || []
    } else if (msg.type === "error") {
      playError = msg.message || ""
    }
  }

  Process {
    id: backend
    command: ["python3", root.ctlPath, "daemon"]
    running: true
    stdinEnabled: true
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(data) { root.handle(data) }
    }
    stderr: SplitParser {
      onRead: function(data) { console.warn("hertz-ctl:", data) }
    }
    onRunningChanged: {
      root.connected = running
      if (running && root.fetched) Qt.callLater(function() { root.search(0) })
    }
    onExited: function(code) {
      root.connected = false
      restartTimer.start()
    }
  }

  Timer {
    id: restartTimer
    interval: 3000
    onTriggered: backend.running = true
  }

  Timer {
    id: searchDebounce
    interval: 380
    onTriggered: root.search(0)
  }

}
