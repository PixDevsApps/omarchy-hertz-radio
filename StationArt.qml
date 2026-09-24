import QtQuick
import qs.Commons
import "Model.js" as Model

// Station logo drawn in the active theme's colours: a rounded "paper" tile
// with the artwork run through a duotone shader (black -> ink, white ->
// paper). Without artwork, or on the software renderer, the tile shows the
// station's initials in the same two colours instead.
Item {
  id: root

  property string source: ""
  property string name: ""
  property color ink: Color.foreground
  property color paper: Color.background
  property real radius: Style.cornerRadius
  property real inset: Math.round(width * 0.08)
  property bool tinted: true

  readonly property bool accelerated: GraphicsInfo.api !== GraphicsInfo.Software
    && GraphicsInfo.api !== GraphicsInfo.Unknown
  readonly property bool ready: image.status === Image.Ready

  implicitWidth: 40
  implicitHeight: 40

  Rectangle {
    id: tile
    anchors.fill: parent
    radius: Math.min(root.radius, width / 4)
    color: root.paper
    border.width: 1
    border.color: Util.alpha(root.ink, 0.08)
    Behavior on color { ColorAnimation { duration: 160 } }
  }

  Text {
    anchors.centerIn: parent
    visible: !root.ready
    textFormat: Text.PlainText
    text: Model.initials(root.name)
    color: root.ink
    opacity: 0.85
    font.family: Style.font.family
    font.pixelSize: Math.max(8, Math.round(root.height * 0.34))
    font.bold: true
    font.letterSpacing: 0.5
  }

  Image {
    id: image
    anchors.fill: parent
    anchors.margins: root.inset
    source: root.source ? Util.fileUrl(root.source) : ""
    // Bound the decoded texture, including unexpectedly large cached logos.
    sourceSize: Qt.size(160, 160)
    fillMode: Image.PreserveAspectFit
    asynchronous: true
    smooth: true
    mipmap: true
    cache: true
    visible: ready && !effect.active
    opacity: ready ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 180 } }
  }

  Loader {
    id: effect
    anchors.centerIn: image
    width: image.paintedWidth
    height: image.paintedHeight
    active: root.tinted && root.accelerated && root.ready
    sourceComponent: ShaderEffect {
      property var source: image
      property color ink: root.ink
      property color paper: root.paper
      property real strength: 1.0
      fragmentShader: Qt.resolvedUrl("shaders/station-tint.frag.qsb")
      Behavior on ink { ColorAnimation { duration: 160 } }
    }
  }
}
