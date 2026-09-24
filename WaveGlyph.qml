import QtQuick
import qs.Commons

// Five rounded bars, like a slice of a waveform. Static when idle; when
// `playing` the bars breathe gently. Scales from the 16 px bar icon to the
// small "now playing" marker in the station list.
Item {
  id: root

  property color foreground: Color.foreground
  property bool playing: false
  property bool dim: false
  property int bars: 5
  property real phase: 0

  readonly property var shape: [0.38, 0.72, 1.0, 0.62, 0.34]
  readonly property real barWidth: Math.max(1.5, Math.round(width / (bars * 1.9) * 2) / 2)
  readonly property real gap: bars > 1 ? (width - barWidth * bars) / (bars - 1) : 0

  implicitWidth: 16
  implicitHeight: 16
  opacity: dim ? 0.5 : 1

  NumberAnimation on phase {
    running: root.playing && root.visible
    from: 0
    to: Math.PI * 2
    duration: 1600
    loops: Animation.Infinite
  }

  Repeater {
    model: root.bars

    Rectangle {
      required property int index
      readonly property real base: root.shape[index % root.shape.length]
      readonly property real wave: root.playing
        ? 0.45 + 0.55 * Math.abs(Math.sin(root.phase + index * 1.3))
        : 1
      width: root.barWidth
      height: Math.max(root.barWidth, Math.round(root.height * 0.86 * base * wave))
      x: Math.round(index * (root.barWidth + root.gap))
      anchors.verticalCenter: parent.verticalCenter
      radius: width / 2
      color: root.foreground
      antialiasing: true
    }
  }
}
