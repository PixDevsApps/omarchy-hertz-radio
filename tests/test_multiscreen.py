"""Exercise two real panels sharing one service in an isolated Quickshell."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = Path(os.environ.get('OMARCHY_PATH', '/usr/share/omarchy')) / 'shell'

FAKE_HELPER = r'''
import json, os, sys
trace = open(os.environ['HERTZ_TEST_TRACE'], 'a', buffering=1)
trace.write(json.dumps({'start': os.getpid()})+'\n')
station = {'uuid': '00000000-0000-0000-0000-000000000001', 'name': 'Test station',
           'url': 'https://example.com/radio', 'country': '', 'codec': '', 'bitrate': 0}
state = {'type': 'state', 'station': station, 'playing': False, 'paused': True,
         'volume': 70, 'muted': False}
def emit(value):
    print(json.dumps(value), flush=True)
emit({'type': 'genres', 'items': ['All', 'Jazz']})
emit(state)
for line in sys.stdin:
    line = line.strip()
    trace.write(json.dumps({'command': line, 'pid': os.getpid()})+'\n')
    command, _, arg = line.partition(' ')
    if command == 'exit':
        break
    if command == 'volume':
        state['volume'] = int(arg)
    elif command == 'toggle':
        state['playing'] = not state['playing']
        state['paused'] = not state['playing']
    elif command == 'fav':
        emit({'type': 'favorites', 'items': [station]})
    elif command == 'search':
        q = json.loads(arg)
        key = q['genre']+'|'+q['country']+'|'+' '.join(q['text'].split()).lower()[:80]
        emit({'type': 'stations', 'key': key, 'page': q['page'], 'items': [station], 'more': False})
    emit(state)
'''

QML = r'''
import QtQuick
import Quickshell
import "Radio" as Radio
import "services" as Host
import qs.Ui as Ui

ShellRoot {
  id: test
  property var first: null
  property var second: null
  property var controller: null
  property int phase: 0
  property int ticks: 0
  QtObject {
    id: host
    property var services: ({})
  }
  Host.PluginShellApi {
    id: facade
    pluginId: "io.github.pixdevsapps.hertz-radio"
    _serviceLookup: function(id) { return host.services[id] || null }
  }
  Ui.PluginBarApi {
    id: barApi
    pluginId: "test-radio-bar"
    moduleName: facade.pluginId
    shell: facade
  }
  Component { id: panelComponent; Radio.Panel { manageIpc: false } }
  Component { id: serviceComponent; Radio.Service { ctlPath: Quickshell.env("HERTZ_TEST_HELPER") } }

  function check(ok, message) { if (!ok) throw new Error(message) }
  function installService() {
    controller = serviceComponent.createObject(test)
    check(controller !== null, "service creation")
    var services = {}
    services[facade.pluginId] = controller
    host.services = services
  }
  function sameState() {
    check(first.radio === second.radio, "panels must share the same service")
    check(first.volume === second.volume, "volume must match")
    check(first.playing === second.playing, "playback must match")
    check(first.station.uuid === second.station.uuid, "station must match")
  }

  Timer {
    interval: 120
    running: true
    repeat: true
    onTriggered: {
      try {
        if (++test.ticks > 160) throw new Error("test timed out")
        switch (test.phase) {
        case 0:
          test.first = panelComponent.createObject(test, {bar: barApi})
          test.second = panelComponent.createObject(test, {bar: barApi})
          check(first && second, "panel creation")
          check(first.radio === null && second.radio === null, "late service setup")
          installService()
          test.phase++
          break
        case 1:
          if (!controller.connected || !first.station) return
          sameState()
          first.setVolume(42)
          second.playPause()
          test.phase++
          break
        case 2:
          if (second.volume !== 42 || !first.playing) return
          sameState()
          first.playPause()
          second.setVolume(13)
          first.setSearchText("discarded query")
          second.setSearchText("shared query")
          second.toggleFavorite(second.station.uuid)
          test.phase++
          break
        case 3:
          if (first.playing || first.volume !== 13 || !second.stations.length || !first.favorites.length) return
          sameState()
          check(first.searchText === "shared query" && second.searchText === first.searchText, "shared search")
          check(first.stations === second.stations && first.favorites === second.favorites, "shared lists")
          first.cursor = 0
          second.cursor = -1
          check(first.cursor !== second.cursor, "independent keyboard cursor")
          first.destroy()
          test.first = null
          second.setVolume(19)
          test.phase++
          break
        case 4:
          if (second.volume !== 19) return
          check(second.connected, "service survives removal of first screen")
          test.first = panelComponent.createObject(test, {bar: barApi})
          sameState()
          controller.send("exit")
          test.phase++
          break
        case 5:
          if (controller.connected) return
          test.phase++
          break
        case 6:
          if (!controller.connected || first.volume !== 70) return
          sameState()
          first.setVolume(23)
          test.phase++
          break
        case 7:
          if (second.volume !== 23) return
          host.services = ({})
          controller.destroy()
          controller = null
          test.phase++
          break
        case 8:
          check(first.radio === null && second.radio === null, "service removal invalidates both panels")
          installService()
          test.phase++
          break
        case 9:
          if (!controller.connected || !first.station) return
          sameState()
          second.setVolume(31)
          test.phase++
          break
        case 10:
          if (first.volume !== 31) return
          sameState()
          console.log("HERTZ_MULTISCREEN_PASS")
          Qt.quit()
        }
      } catch (error) {
        console.error("HERTZ_MULTISCREEN_FAIL: " + error.stack)
        Qt.quit()
      }
    }
  }
}
'''


@unittest.skipUnless(shutil.which('quickshell') and (SHELL / 'Ui').is_dir() and os.environ.get('WAYLAND_DISPLAY'),
                     'needs Quickshell, Omarchy QML components and a Wayland session')
class SharedServiceTest(unittest.TestCase):
    def test_two_panels_share_state_and_survive_restarts(self):
        with tempfile.TemporaryDirectory(prefix='hertz-shared-') as tmp:
            stage = Path(tmp)
            for name in ('Ui', 'Commons', 'services'):
                (stage / name).symlink_to(SHELL / name, target_is_directory=True)
            helper = stage / 'fake_helper.py'
            helper.write_text(FAKE_HELPER)
            trace = stage / 'commands.jsonl'
            (stage / 'Radio').symlink_to(ROOT, target_is_directory=True)
            qml = QML
            (stage / 'shell.qml').write_text(qml)
            env = dict(os.environ, QT_QPA_PLATFORM='wayland', QT_QUICK_BACKEND='software',
                       HERTZ_TEST_HELPER=str(helper), HERTZ_TEST_TRACE=str(trace),
                       XDG_DATA_HOME=str(stage / 'data'), XDG_CACHE_HOME=str(stage / 'cache'))
            run = subprocess.run(['quickshell', '-p', str(stage), '--no-color'], env=env,
                                 capture_output=True, text=True, timeout=30)
            output = run.stdout + run.stderr
            if os.environ.get('HERTZ_TEST_LOG'):
                Path(os.environ['HERTZ_TEST_LOG']).write_text(output)
            self.assertEqual(run.returncode, 0, output)
            self.assertIn('HERTZ_MULTISCREEN_PASS', output)
            self.assertNotIn('HERTZ_MULTISCREEN_FAIL', output)
            for error in ('TypeError:', 'ReferenceError:', 'Unable to assign', 'Binding loop detected'):
                self.assertNotIn(error, output)
            rows = [json.loads(line) for line in trace.read_text().splitlines()]
            starts = [r['start'] for r in rows if 'start' in r]
            self.assertEqual(len(starts), 3, 'one helper, one crash restart, one service replacement')
            searches = [json.loads(r['command'].split(' ', 1)[1]) for r in rows
                        if r.get('command', '').startswith('search ')]
            self.assertEqual(len(searches), 2, 'one debounced search and one refresh after helper restart')
            self.assertTrue(all(q['text'] == 'shared query' for q in searches))
            for pid in starts:
                self.assertFalse(Path(f'/proc/{pid}').exists(), 'helper leaked after service destruction')


if __name__ == '__main__':
    unittest.main()
