import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import "Model.js" as Model

// Hertz Radio: bar icon + popup player with an expandable station browser.
//
// Everything outside the UI goes through ./hertz-ctl, a small long-running
// helper that talks to Radio Browser, caches artwork and drives mpv over its
// IPC socket (mpv-mpris then exposes playback to media keys). The panel only
// renders the JSON snapshots it streams and sends one-line commands back.
Panel {
  id: root
  moduleName: "io.github.pixdevsapps.hertz-radio"
  ipcTarget: "hertz-radio"

  // ---------------------------------------------------------------- state

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
  property int cursor: -1

  property bool browsing: false
  property bool volumeDragging: false

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color accent: Color.accent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color surface: {
    var c = Color.popups.background
    return Qt.rgba(c.r, c.g, c.b, 1)
  }
  // Artwork "paper": a step off the panel surface, toward the foreground.
  readonly property color paper: Model.mix(surface, fg, 0.07)
  readonly property var favoriteIds: {
    var ids = {}
    for (var i = 0; i < favorites.length; i++) ids[favorites[i].uuid] = true
    return ids
  }
  readonly property string currentKey: Model.searchKey(genre, country, searchText)
  readonly property var countryOptions: Model.countryOptions(countries)
  readonly property string countryLabel: {
    for (var i = 0; i < countries.length; i++) if (countries[i].code === country) return countries[i].name
    return country
  }
  readonly property bool live: playing && !buffering

  // ---------------------------------------------------------------- backend

  readonly property string ctlPath: decodeURIComponent(String(Qt.resolvedUrl("hertz-ctl")).replace(/^file:\/\//, ""))

  function send(line) {
    if (backend.running) backend.write(line + "\n")
  }

  function search(pageNumber) {
    page = pageNumber || 0
    loading = true
    listError = ""
    fetched = true
    send("search " + JSON.stringify({ genre: genre, country: country, text: searchText, page: page }))
  }

  // Infinite scroll: fetch the next page once the list nears its end.
  function loadMore() {
    if (more && !loading && listError === "" && genre !== "Favorites" && stations.length > 0)
      search(page + 1)
  }

  function selectCountry(code) {
    if (country === code && fetched) return
    country = code
    cursor = -1
    stationList.positionViewAtBeginning()
    search(0)
  }

  function selectGenre(name) {
    if (genre === name && fetched) return
    genre = name
    cursor = -1
    stationList.positionViewAtBeginning()
    search(0)
  }

  function play(uuid) { send("play " + uuid) }
  function playPause() { send("toggle") }
  function next() { send("next") }
  function prev() { send("prev") }
  function stop() { send("stop") }
  function toggleFavorite(uuid) { if (uuid) send("fav " + uuid) }
  function setVolume(v) {
    volume = Math.max(0, Math.min(100, Math.round(v)))
    send("volume " + volume)
  }

  function artFor(uuid) { return art[uuid] || "" }

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
      if (!volumeDragging) volume = msg.volume
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

  onBrowsingChanged: {
    if (browsing && countries.length === 0) send("countries")
    if (browsing) Qt.callLater(revealGenre)
  }
  onGenreChanged: Qt.callLater(revealGenre)
  onGenresChanged: Qt.callLater(revealGenre)

  // Keep the selected genre chip in view (e.g. a remembered genre after a restart).
  function revealGenre() {
    var i = genres.indexOf(genre)
    if (i >= 0 && genreList.count > i) genreList.positionViewAtIndex(i, ListView.Contain)
  }

  onOpenedChanged: {
    if (opened && !fetched) search(0)
    if (!opened) {
      cursor = -1
      searchField.focus = false
    }
  }

  // ---------------------------------------------------------------- bar

  // Now-playing text to the right of the icon. Set "showTitle": false or
  // "maxTitleWidth": <px> on this widget's entry in shell.json to change it.
  readonly property bool vertical: bar ? bar.vertical : false
  readonly property string barTitle: {
    if (!station || !playing || playError) return ""
    return nowTitle || Model.cleanName(station.name)
  }
  // ICY titles are "Artist - Song": the artist is drawn bold, the song regular.
  readonly property var barParts: Model.splitTitle(nowTitle ? barTitle : "")
  readonly property bool showBarTitle: setting("showTitle", true) && !vertical && barTitle !== ""
  readonly property color titleColor: paused ? Qt.darker(barForeground, 1.6) : barForeground
  readonly property real maxTitleWidth: Style.space(setting("maxTitleWidth", 150))

  implicitWidth: button.implicitWidth + (showBarTitle ? titleClip.width + Style.space(4) : 0)
  // No open-panel underline under this widget. The bar only draws a positive
  // hint, rounded to whole pixels, so a sub-pixel hint collapses the mark to 0.
  readonly property real openPanelIndicatorWidth: 0.4
  implicitHeight: button.implicitHeight

  Item {
    id: titleClip
    visible: root.showBarTitle
    anchors.right: parent.right
    anchors.rightMargin: Style.space(4)
    anchors.verticalCenter: parent.verticalCenter
    width: Math.min(root.maxTitleWidth, titleLine.implicitWidth)
    height: titleLine.implicitHeight
    clip: true

    Behavior on width {
      enabled: root.bar ? root.bar.foregroundAnimationEnabled !== false : true
      NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
    }

    // Ticker: the title and a copy of it side by side. Long titles pause on
    // the artist, glide left through the song and wrap back to the artist.
    Row {
      id: ticker
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(36)

      readonly property real overflow: Math.max(0, titleLine.implicitWidth - titleClip.width)
      readonly property real loopDistance: titleLine.implicitWidth + spacing
      readonly property bool shouldRun: overflow > 0 && titleClip.visible && root.playing && !root.opened

      function sync() {
        if (shouldRun) {
          if (!marquee.running) { x = 0; marquee.start() }
        } else {
          marquee.stop()
          x = 0
        }
      }
      onShouldRunChanged: sync()
      Component.onCompleted: Qt.callLater(sync)
      onLoopDistanceChanged: { marquee.stop(); x = 0; Qt.callLater(sync) }

      TitleLine { id: titleLine }
      TitleLine { visible: ticker.overflow > 0 }

      SequentialAnimation {
        id: marquee
        loops: Animation.Infinite
        PauseAnimation { duration: 2500 }
        NumberAnimation {
          target: ticker; property: "x"; from: 0; to: -ticker.loopDistance
          duration: Math.max(2000, ticker.loopDistance * 1000 / 38)   // ~38 px/s
        }
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton
      onClicked: function(mouse) {
        if (mouse.button === Qt.MiddleButton) root.playPause()
        else if (mouse.button === Qt.RightButton) root.stop()
        else root.toggle()
      }
      onWheel: function(wheel) { root.setVolume(root.volume + (wheel.angleDelta.y > 0 ? 5 : -5)) }
      onEntered: if (root.bar && root.bar.showTooltip) root.bar.showTooltip(root, button.tooltipText)
      onExited: if (root.bar && root.bar.hideTooltip) root.bar.hideTooltip(root)
    }
  }

  BarIconButton {
    id: button
    anchors.left: parent.left
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    bar: root.bar
    tooltipText: {
      if (!root.station) return "Hertz Radio"
      var name = Model.cleanName(root.station.name)
      if (root.playError) return name + " · " + root.playError
      if (!root.playing && !root.paused) return name + " · off"
      if (root.paused) return name + " · paused"
      return root.nowTitle ? name + " · " + root.nowTitle : name
    }
    iconComponent: Component {
      WaveGlyph {
        foreground: root.bar ? root.bar.barForeground : Color.foreground
        playing: root.live
      }
    }
    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.playPause()
      else if (b === Qt.RightButton) root.stop()
      else root.toggle()
    }
    onWheelMoved: function(delta) { root.setVolume(root.volume + (delta > 0 ? 5 : -5)) }
  }

  // ---------------------------------------------------------------- panel

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keys
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      blocked: searchField.activeFocus || countryPicker.popupOpen
      property bool enterPressed: false

      onCloseRequested: {
        if (root.searchText !== "") { searchField.text = "" }
        else root.close()
      }
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (dy !== 0) {
          if (!root.browsing) { root.browsing = true; return }
          var n = root.stations.length
          if (!n) return
          root.cursor = Math.max(0, Math.min(n - 1, root.cursor + dy))
          stationList.positionViewAtIndex(root.cursor, ListView.Contain)
          if (root.cursor >= n - 4) root.loadMore()
        } else if (dx !== 0 && root.browsing && root.genres.length) {
          var i = root.genres.indexOf(root.genre)
          var g = root.genres[Math.max(0, Math.min(root.genres.length - 1, i + dx))]
          root.selectGenre(g)
          genreList.positionViewAtIndex(root.genres.indexOf(g), ListView.Contain)
        }
      }
      onReturnRequested: enterPressed = true
      onActivateRequested: {
        var enter = enterPressed
        enterPressed = false
        if (enter && root.browsing && root.cursor >= 0 && root.cursor < root.stations.length)
          root.play(root.stations[root.cursor].uuid)
        else root.playPause()
      }
      onTextKey: function(t) {
        if (t === "/") { root.browsing = true; searchField.forceActiveFocus() }
        else if (t === "c" || t === "C") { root.browsing = true; countryPicker.open() }
        else if (t === "b" || t === "B") root.browsing = !root.browsing
        else if (t === "f" || t === "F") {
          if (root.browsing && root.cursor >= 0 && root.cursor < root.stations.length)
            root.toggleFavorite(root.stations[root.cursor].uuid)
          else if (root.station) root.toggleFavorite(root.station.uuid)
        }
        else if (t === "n" || t === "N") root.next()
        else if (t === "p" || t === "P") root.prev()
        else if (t === "s" || t === "S") root.stop()
        else if (t === "m" || t === "M") root.send("mute")
        else if (t === "+" || t === "=") root.setVolume(root.volume + 5)
        else if (t === "-" || t === "_") root.setVolume(root.volume - 5)
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(14)

        // ---------------- now playing
        Item {
          id: hero
          width: parent.width
          implicitHeight: Math.max(heroArt.height, heroText.implicitHeight)

          StationArt {
            id: heroArt
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: Style.space(64)
            height: width
            visible: !!root.station
            source: root.station ? root.artFor(root.station.uuid) : ""
            name: root.station ? root.station.name : ""
            ink: root.playing || root.paused ? root.accent : root.fg
            paper: root.paper
          }

          Rectangle {
            anchors.fill: heroArt
            visible: !root.station
            radius: Math.min(Style.cornerRadius, width / 4)
            color: root.paper
            WaveGlyph {
              anchors.centerIn: parent
              width: parent.width * 0.42
              height: width
              foreground: root.fg
              dim: true
            }
          }

          // Turn the radio off: closes the stream and the player entirely.
          Button {
            id: powerButton
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.topMargin: -Style.space(4)
            visible: root.playing || root.paused
            iconText: Model.ICON.power
            foreground: Qt.darker(root.fg, 1.3)
            fontFamily: root.fontFamily
            iconSize: Style.font.icon
            horizontalPadding: Style.space(5)
            verticalPadding: Style.space(3)
            tooltipText: "Turn off radio (S)"
            onClicked: root.stop()
          }

          Column {
            id: heroText
            anchors.left: heroArt.right
            anchors.leftMargin: Style.space(14)
            anchors.right: powerButton.visible ? powerButton.left : parent.right
            anchors.rightMargin: powerButton.visible ? Style.space(6) : 0
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(3)

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: root.station ? Model.cleanName(root.station.name) : "Hertz Radio"
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
            }

            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: {
                if (!root.station) return "Pick a station to start listening"
                if (root.playError) return root.playError
                if (root.buffering) return "Tuning in…"
                if (root.nowTitle) return root.nowTitle
                if (root.playing) return "Live"
                return root.paused ? "Paused · stream disconnected" : "Off"
              }
              color: root.playError ? Color.urgent : Qt.darker(root.fg, root.nowTitle ? 1.1 : 1.35)
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              maximumLineCount: 2
              wrapMode: Text.WordWrap
              elide: Text.ElideRight
            }

            Row {
              spacing: Style.space(6)
              visible: !!root.station

              Rectangle {
                id: liveDot
                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(5, Style.space(6))
                height: width
                radius: width / 2
                color: root.playing ? (root.playError ? Color.urgent : root.accent) : Util.alpha(root.fg, 0.25)
                SequentialAnimation on opacity {
                  running: root.buffering
                  loops: Animation.Infinite
                  NumberAnimation { to: 0.25; duration: 500; easing.type: Easing.InOutSine }
                  NumberAnimation { to: 1; duration: 500; easing.type: Easing.InOutSine }
                  onRunningChanged: if (!running) liveDot.opacity = 1
                }
              }

              Text {
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: {
                  var parts = [root.playing ? (root.buffering ? "TUNING" : "LIVE") : root.paused ? "PAUSED" : "OFF"]
                  var q = Model.quality(root.station, root.codec)
                  if (q) parts.push(q)
                  var c = Model.countryName(root.station)
                  if (c) parts.push(c.toUpperCase())
                  return parts.join("  ·  ")
                }
                color: Qt.darker(root.fg, 1.4)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.0
              }
            }
          }
        }

        // ---------------- transport
        Item {
          width: parent.width
          implicitHeight: Math.max(transport.implicitHeight, volumeRow.implicitHeight)

          Row {
            id: transport
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(4)

            Button {
              iconText: Model.ICON.prev
              foreground: root.fg
              fontFamily: root.fontFamily
              iconSize: Style.font.iconLarge
              enabled: root.hasNext
              opacity: enabled ? 1 : 0.35
              tooltipText: "Previous station (P)"
              onClicked: root.prev()
            }

            Button {
              iconText: root.playError ? Model.ICON.retry : (root.playing ? Model.ICON.pause : Model.ICON.play)
              bordered: true
              selected: root.playing
              foreground: root.fg
              fontFamily: root.fontFamily
              iconSize: Style.font.iconLarge
              horizontalPadding: Style.space(14)
              enabled: !!root.station || root.stations.length > 0
              tooltipText: root.playError ? "Try again" : root.playing ? "Pause (Space)" : "Play (Space)"
              onClicked: root.playPause()
            }

            Button {
              iconText: Model.ICON.next
              foreground: root.fg
              fontFamily: root.fontFamily
              iconSize: Style.font.iconLarge
              enabled: root.hasNext
              opacity: enabled ? 1 : 0.35
              tooltipText: "Next station (N)"
              onClicked: root.next()
            }

            Button {
              iconText: root.favorite ? Model.ICON.heart : Model.ICON.heartOutline
              foreground: root.favorite ? root.accent : root.fg
              fontFamily: root.fontFamily
              iconSize: Style.font.iconLarge
              visible: !!root.station
              tooltipText: root.favorite ? "Remove from favorites (F)" : "Add to favorites (F)"
              onClicked: root.toggleFavorite(root.station.uuid)
            }
          }

          Row {
            id: volumeRow
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(6)

            Button {
              anchors.verticalCenter: parent.verticalCenter
              iconText: Model.volumeIcon(root.volume, root.muted)
              foreground: root.muted ? Qt.darker(root.fg, 1.5) : root.fg
              fontFamily: root.fontFamily
              iconSize: Style.font.icon
              horizontalPadding: Style.space(4)
              tooltipText: (root.muted ? "Unmute" : "Mute") + " (M)"
              onClicked: root.send("mute")
            }

            PanelSlider {
              id: volumeSlider
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(96)
              bar: root.bar
              minimum: 0
              maximum: 100
              step: 1
              integer: true
              value: root.volume
              opacity: root.muted ? 0.45 : 1
              onDraggingChanged: root.volumeDragging = dragging
              onMoved: function(v) { root.setVolume(v) }
              onRightClicked: root.send("mute")
            }

            Text {
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(28)
              horizontalAlignment: Text.AlignRight
              textFormat: Text.PlainText
              text: Math.round(volumeSlider.dragging ? volumeSlider.liveValue : root.volume)
              color: Qt.darker(root.fg, 1.4)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }
          }
        }

        PanelSeparator { foreground: root.fg }

        // ---------------- browse header
        Item {
          width: parent.width
          implicitHeight: stationsHeader.implicitHeight + Style.space(4)

          Row {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(8)

            PanelSectionHeader {
              id: stationsHeader
              text: "STATIONS"
              foreground: root.fg
              fontFamily: root.fontFamily
            }

            Text {
              anchors.baseline: stationsHeader.baseline
              visible: !root.browsing
              textFormat: Text.PlainText
              text: {
                var parts = []
                if (root.genre !== "All") parts.push(root.genre)
                if (root.country) parts.push(root.countryLabel)
                return parts.join("  ·  ")
              }
              color: Qt.darker(root.fg, 1.6)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Text {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: root.browsing ? "Hide ▴" : "Browse ▾"
            color: browseMouse.containsMouse ? root.fg : Qt.darker(root.fg, 1.4)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          MouseArea {
            id: browseMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.browsing = !root.browsing
          }
        }

        // ---------------- browser (expands in place)
        Item {
          id: browser
          width: parent.width
          clip: true
          implicitHeight: root.browsing ? browserColumn.implicitHeight : 0
          visible: implicitHeight > 0
          opacity: root.browsing ? 1 : 0
          Behavior on implicitHeight { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
          Behavior on opacity { NumberAnimation { duration: 160 } }

          Column {
            id: browserColumn
            width: parent.width
            spacing: Style.space(10)

            Item {
              width: parent.width
              height: searchField.height

            TextField {
              id: searchField
              anchors.left: parent.left
              anchors.right: countryPicker.left
              anchors.rightMargin: Style.space(8)
              foreground: root.fg
              font.family: root.fontFamily
              placeholderText: Model.ICON.search + "  Search stations"
              leftPadding: Style.spacing.controlPaddingX
              rightPadding: clearSearch.visible ? clearSearch.width + Style.space(10) : Style.spacing.controlPaddingX
              onTextChanged: {
                root.searchText = text
                root.cursor = -1
                searchDebounce.restart()
              }
              Keys.onEscapePressed: function(event) {
                if (text !== "") text = ""
                else keys.forceActiveFocus()
                event.accepted = true
              }
              Keys.onDownPressed: function(event) {
                keys.forceActiveFocus()
                if (root.stations.length) root.cursor = 0
                event.accepted = true
              }
              Keys.onReturnPressed: function(event) {
                searchDebounce.stop()
                root.search(0)
                keys.forceActiveFocus()
                if (root.stations.length) root.cursor = 0
                event.accepted = true
              }

              Text {
                id: clearSearch
                visible: searchField.text !== ""
                anchors.right: parent.right
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: Model.ICON.close
                color: clearMouse.containsMouse ? root.fg : Qt.darker(root.fg, 1.5)
                font.family: root.fontFamily
                font.pixelSize: Style.font.icon
                MouseArea {
                  id: clearMouse
                  anchors.fill: parent
                  anchors.margins: -Style.space(4)
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: searchField.text = ""
                }
              }
            }

            // Country filter; combines with the genre chips and the search text.
            SearchableDropdown {
              id: countryPicker
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(150)
              showLabel: false
              rowHeight: searchField.height
              value: root.country
              options: root.countryOptions
              triggerLabel: "All countries"
              placeholderText: "Search countries"
              emptyText: root.countries.length ? "No matching country" : "Loading countries…"
              foreground: root.fg
              accent: root.accent
              fontFamily: root.fontFamily
              onChanged: function(value) {
                root.selectCountry(value)
                keys.forceActiveFocus()
              }
            }
            }

            ListView {
              id: genreList
              width: parent.width
              height: Style.spacing.controlHeight
              orientation: ListView.Horizontal
              spacing: Style.space(6)
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              model: root.genres

              delegate: Button {
                required property var modelData
                height: genreList.height
                text: modelData
                iconText: modelData === "Favorites" ? Model.ICON.heart : ""
                bordered: true
                selected: root.genre === modelData
                foreground: root.fg
                fontFamily: root.fontFamily
                fontSize: Style.font.bodySmall
                iconSize: Style.font.bodySmall
                verticalPadding: Style.space(3)
                onClicked: root.selectGenre(modelData)
              }

              // Vertical wheel scrolls the chip strip sideways.
              MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.NoButton
                onWheel: function(wheel) {
                  var d = wheel.angleDelta.y !== 0 ? wheel.angleDelta.y : wheel.angleDelta.x
                  var maxX = Math.max(0, genreList.contentWidth - genreList.width)
                  genreList.contentX = Math.max(0, Math.min(maxX, genreList.contentX - d))
                }
              }
            }

            Item {
              width: parent.width
              readonly property real rowHeight: Style.space(46)
              readonly property int visibleRows: 7
              height: root.stations.length
                ? Math.min(root.stations.length + (root.more || root.loading ? 1 : 0), visibleRows) * rowHeight
                : Style.space(88)

              ListView {
                id: stationList
                anchors.fill: parent
                clip: true
                visible: root.stations.length > 0
                boundsBehavior: Flickable.StopAtBounds
                model: root.stations
                readonly property real prefetchZone: Style.space(46) * 3
                function checkEnd() {
                  if (contentHeight > height && contentY + height >= contentHeight - prefetchZone) root.loadMore()
                }
                onContentYChanged: checkEnd()
                onContentHeightChanged: checkEnd()
                ScrollBar.vertical: ScrollBar { policy: stationList.contentHeight > stationList.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }

                delegate: StationRow {
                  required property var modelData
                  required property int index
                  width: stationList.width - Style.space(6)
                  station: modelData
                  artPath: root.artFor(modelData.uuid)
                  current: !!root.station && root.station.uuid === modelData.uuid && (root.playing || root.paused)
                  playing: current && root.live
                  favorite: !!root.favoriteIds[modelData.uuid]
                  hasCursor: root.cursor === index
                  hideCountry: root.country !== "" 
                  foreground: root.fg
                  accent: root.accent
                  paper: root.paper
                  fontFamily: root.fontFamily
                  onActivated: { root.cursor = index; root.play(modelData.uuid) }
                  onFavoriteToggled: root.toggleFavorite(modelData.uuid)
                }

                // Footer: a breathing waveform while the next page loads, a
                // retry link if it failed, and nothing once the list ends.
                footer: Item {
                  width: stationList.width
                  readonly property bool failed: root.listError !== "" && root.stations.length > 0
                  height: (root.more || failed) && root.genre !== "Favorites" ? Style.space(46) : 0
                  visible: height > 0

                  WaveGlyph {
                    anchors.centerIn: parent
                    visible: !parent.failed
                    width: Style.space(18)
                    height: width
                    foreground: root.fg
                    playing: root.loading
                    dim: true
                  }

                  Text {
                    anchors.centerIn: parent
                    visible: parent.failed
                    textFormat: Text.PlainText
                    text: "Couldn’t load more  ·  Retry"
                    color: retryMouse.containsMouse ? root.fg : Qt.darker(root.fg, 1.45)
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    MouseArea {
                      id: retryMouse
                      anchors.fill: parent
                      anchors.margins: -Style.space(6)
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.search(root.page + 1)
                    }
                  }
                }
              }

              // Empty / loading / offline states
              Column {
                anchors.centerIn: parent
                width: parent.width
                spacing: Style.space(6)
                visible: root.stations.length === 0

                WaveGlyph {
                  anchors.horizontalCenter: parent.horizontalCenter
                  width: Style.space(22)
                  height: width
                  foreground: root.fg
                  playing: root.loading
                  dim: true
                }

                Text {
                  width: parent.width
                  horizontalAlignment: Text.AlignHCenter
                  wrapMode: Text.WordWrap
                  textFormat: Text.PlainText
                  text: {
                    if (root.loading) return "Tuning the dial…"
                    if (root.listError) return root.listError
                    if (root.genre === "Favorites" && root.favorites.length === 0) return "No favorites yet. Tap the heart on a station to keep it here."
                    if (root.searchText) return "No stations match “" + root.searchText + "”" + (root.country ? " in " + root.countryLabel : "")
                    if (root.country) return "No " + (root.genre === "All" ? "" : root.genre + " ") + "stations in " + root.countryLabel
                    return "No stations found"
                  }
                  color: root.listError ? Color.urgent : Qt.darker(root.fg, 1.45)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }

          }
        }
      }
    }
  }

  // One copy of the bar title: **Artist** – Song, or the plain text when the
  // station sends no artist.
  component TitleLine: Row {
    Text {
      visible: text !== ""
      textFormat: Text.PlainText
      text: root.barParts.artist
      color: root.titleColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      font.bold: true
      Behavior on color { ColorAnimation { duration: 160 } }
    }
    Text {
      textFormat: Text.PlainText
      text: root.barParts.artist ? " – " + root.barParts.song : root.barTitle
      color: root.titleColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      Behavior on color { ColorAnimation { duration: 160 } }
    }
  }
}
