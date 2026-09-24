import QtQuick
import qs.Ui
import qs.Commons
import "Model.js" as Model

// One station in the browser list: tinted logo, name, a quiet subtitle and a
// heart. The playing station gets the accent ink and a live waveform marker.
Item {
  id: row

  property var station: ({})
  property string artPath: ""
  property bool playing: false
  property bool current: false        // selected station, even when paused
  property bool favorite: false
  property bool hasCursor: false
  property bool hideCountry: false    // the list is already filtered to one country
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color paper: Color.background
  property string fontFamily: Style.font.family

  signal activated()
  signal favoriteToggled()
  signal hovered()

  readonly property bool hot: mouse.containsMouse || heartMouse.containsMouse || hasCursor
  readonly property color ink: current ? accent : hot ? Model.mix(foreground, accent, 0.35) : foreground

  implicitHeight: Style.space(46)

  BorderSurface {
    anchors.fill: parent
    radius: Style.cornerRadius
    color: mouse.pressed ? Style.pressedFillFor(row.foreground, row.accent)
      : row.hot ? Style.hoverFillFor(row.foreground, row.accent)
      : row.current ? Style.normalFillFor(row.foreground, row.accent)
      : "transparent"
    borderSpec: row.hasCursor ? Border.controlSpec("hover-cursor", row.foreground, row.accent)
      : Border.none()
    Behavior on color { ColorAnimation { duration: 110 } }
  }

  StationArt {
    id: art
    anchors.left: parent.left
    anchors.leftMargin: Style.space(6)
    anchors.verticalCenter: parent.verticalCenter
    width: Style.space(34)
    height: width
    source: row.artPath
    name: row.station.name || ""
    ink: row.ink
    paper: row.paper
  }

  Column {
    anchors.left: art.right
    anchors.leftMargin: Style.space(10)
    anchors.right: trailing.left
    anchors.rightMargin: Style.space(8)
    anchors.verticalCenter: parent.verticalCenter
    spacing: Style.space(2)

    Text {
      width: parent.width
      textFormat: Text.PlainText
      text: Model.cleanName(row.station.name)
      color: row.current ? Style.selectedStateColor(row.foreground, row.accent) : row.foreground
      font.family: row.fontFamily
      font.pixelSize: Style.font.body
      font.bold: row.current
      elide: Text.ElideRight
    }

    Text {
      width: parent.width
      textFormat: Text.PlainText
      text: Model.subtitle(row.station, row.hideCountry)
      color: Qt.darker(row.foreground, 1.45)
      font.family: row.fontFamily
      font.pixelSize: Style.font.caption
      elide: Text.ElideRight
      visible: text !== ""
    }
  }

  Row {
    id: trailing
    anchors.right: parent.right
    anchors.rightMargin: Style.space(8)
    anchors.verticalCenter: parent.verticalCenter
    spacing: Style.space(10)

    WaveGlyph {
      visible: row.current
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(12)
      height: Style.space(12)
      foreground: row.accent
      playing: row.playing
      dim: !row.playing
    }

    Text {
      id: heart
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: row.favorite ? Model.ICON.heart : Model.ICON.heartOutline
      color: row.favorite ? row.accent : heartMouse.containsMouse ? row.foreground : Qt.darker(row.foreground, 1.5)
      opacity: row.favorite || row.hot ? 1 : 0
      font.family: row.fontFamily
      font.pixelSize: Style.font.icon
      Behavior on opacity { NumberAnimation { duration: 120 } }
      Behavior on color { ColorAnimation { duration: 120 } }

      MouseArea {
        id: heartMouse
        anchors.fill: parent
        anchors.margins: -Style.space(6)
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: row.favoriteToggled()
      }
    }
  }

  MouseArea {
    id: mouse
    anchors.fill: parent
    anchors.rightMargin: trailing.width + Style.space(14)
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: row.activated()
    onContainsMouseChanged: if (containsMouse) row.hovered()
  }
}
