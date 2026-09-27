"""Pause should discard pending PCM, and old IPC events must stay old."""

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_helper():
    loader = importlib.machinery.SourceFileLoader('hertz_pause', str(ROOT / 'hertz-ctl'))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


hz = load_helper()


class SessionEventsTest(unittest.TestCase):
    def test_event_waiting_for_daemon_lock_is_checked_after_reconnect(self):
        daemon = hz.Daemon.__new__(hz.Daemon)
        daemon.lock = threading.RLock()
        daemon.mpv = hz.Mpv(lambda _: None)
        daemon.mpv.generation = 1
        seen = []
        daemon._on_mpv = seen.append
        entered = threading.Event()

        def old_reader():
            entered.set()
            daemon.on_mpv({'event': 'hertz-disconnected', '_hertz_generation': 1})

        with daemon.lock:
            thread = threading.Thread(target=old_reader)
            thread.start()
            self.assertTrue(entered.wait(1))
            daemon.mpv.generation = 2
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen, [])
        daemon.on_mpv({'event': 'start-file', '_hertz_generation': 2})
        self.assertEqual(len(seen), 1)

    def test_reader_cannot_forge_its_session_and_cannot_detach_new_socket(self):
        class Reader:
            messages = [b'{"event":"start-file","_hertz_generation":999}\n', b'']

            def recv(self, _):
                return self.messages.pop(0)

            def close(self):
                pass

        seen = []
        mpv = hz.Mpv(seen.append)
        current = object()
        mpv.sock = current
        mpv.generation = 2
        mpv._reader(Reader(), 1)
        self.assertIs(mpv.sock, current)
        self.assertEqual([event['_hertz_generation'] for event in seen], [1, 1])

    def test_old_killer_leaves_new_session_record_alone(self):
        old = {'pid': 1234, 'start': '10'}
        new = {'pid': 5678, 'start': '20'}
        with patch.object(hz, 'read_json', return_value=new), \
             patch.object(hz, 'process_start', return_value=None), \
             patch.object(hz.os, 'unlink') as unlink:
            hz.Mpv.kill_player(info=old)
        unlink.assert_not_called()


class PlaybackStateTest(unittest.TestCase):
    def make_daemon(self, starts=True):
        daemon = hz.Daemon.__new__(hz.Daemon)
        daemon.mpv = Mock()
        daemon.mpv.alive = True
        daemon.mpv.start.return_value = starts
        daemon.volume = 37
        daemon.muted = True
        daemon.mode, daemon.loaded = 'playing', True
        daemon.idle, daemon.buffering = False, False
        daemon.pause_timer = None
        daemon.station = {'uuid': '00000000-0000-0000-0000-000000000001',
                          'name': 'First station', 'url': 'https://example.com/first'}
        daemon.queue = [daemon.station]
        daemon.title, daemon.codec, daemon.samplerate = 'Old title', 'AAC', 48000
        daemon.error = ''
        daemon.clicked = daemon.station['uuid']
        daemon.favorites = []
        daemon.art, daemon.mpris = Mock(), Mock()
        daemon.save, daemon.save_session = Mock(), Mock()
        return daemon

    def test_replacement_keeps_volume_and_applies_mute_before_audio_loads(self):
        for muted in (True, False):
            with self.subTest(muted=muted):
                daemon = self.make_daemon()
                daemon.muted = muted
                with patch.object(hz, 'public_stream', return_value=True), \
                     patch.object(hz, 'emit'):
                    daemon.play_station(daemon.station)
                daemon.mpv.start.assert_called_once_with(37)
                commands = [c.args for c in daemon.mpv.command.call_args_list]
                mute = ('set_property', 'mute', muted)
                self.assertIn(mute, commands)
                load_index = next(i for i, c in enumerate(commands) if c[0] == 'loadfile')
                self.assertLess(commands.index(mute), load_index)
                self.assertEqual(daemon.mode, 'playing')

    def test_failed_replacement_publishes_stopped_state_and_keeps_selection(self):
        daemon = self.make_daemon(starts=False)
        selected = {'uuid': '00000000-0000-0000-0000-000000000002',
                    'name': 'Second station', 'url': 'https://example.com/second'}
        with patch.object(hz, 'public_stream', return_value=True), \
             patch.object(hz, 'emit') as emit:
            daemon.play_station(selected, [selected])
        daemon.mpv.terminate.assert_called_once_with(wait=True, grace=0.0)
        self.assertEqual(daemon.mode, 'off')
        self.assertFalse(daemon.loaded)
        self.assertEqual(daemon.station['uuid'], selected['uuid'])
        self.assertEqual(daemon.queue, [selected])
        self.assertEqual(daemon.title, '')
        self.assertTrue(daemon.error)
        daemon.save_session.assert_called_once()
        state = emit.call_args.args[0]
        self.assertFalse(state['playing'])
        self.assertFalse(state['buffering'])
        self.assertTrue(state['error'])
        self.assertFalse(daemon.mpris.update.call_args.args[0]['playing'])


@unittest.skipUnless(shutil.which('mpv') and shutil.which('bwrap'), 'needs mpv and bubblewrap')
class PauseLatencyTest(unittest.TestCase):
    def test_pause_cuts_pcm_and_can_start_a_new_session(self):
        # The real sandboxed mpv writes a synthetic tone. This replacement for
        # pw-cat consumes PCM at playback speed but never opens an audio device.
        with tempfile.TemporaryDirectory(prefix='hz-pause-') as tmp:
            root = Path(tmp)
            trace = root / 'trace.jsonl'
            consumer = root / 'pw-cat'
            consumer.write_text('''#!/usr/bin/python3
import json, os, sys, time
with open(os.environ['HERTZ_PAUSE_TRACE'], 'w', buffering=1) as trace:
    while True:
        chunk = sys.stdin.buffer.read(1920)
        if not chunk:
            break
        trace.write(json.dumps({'time': time.monotonic(), 'nonzero': any(chunk)})+'\\n')
        time.sleep(len(chunk) / (48000 * 4))
''')
            consumer.chmod(0o700)
            env = dict(PATH=tmp + os.pathsep + os.environ['PATH'],
                       HERTZ_RADIO_RUNTIME=str(root / 'run'),
                       XDG_DATA_HOME=str(root / 'data'), XDG_CACHE_HOME=str(root / 'cache'),
                       HERTZ_PAUSE_TRACE=str(trace))
            with patch.dict(os.environ, env):
                module = load_helper()
                player = module.Mpv(lambda _: None)
                daemon = module.Daemon.__new__(module.Daemon)
                daemon.mpv = player
                daemon.pause_timer = None
                daemon.station = {'name': 'Synthetic test station'}
                daemon.save_session = lambda: None
                daemon.publish = lambda: None
                try:
                    for cycle in range(2):
                        with self.subTest(cycle=cycle):
                            trace.unlink(missing_ok=True)
                            self.assertTrue(player.start(50))
                            player.command('loadfile', 'av://lavfi:sine=frequency=440:sample_rate=48000', 'replace')
                            deadline = time.monotonic() + 8
                            while not trace.exists() or trace.stat().st_size < 100:
                                self.assertLess(time.monotonic(), deadline, 'no synthetic audio')
                                time.sleep(0.02)
                            time.sleep(2.37)
                            daemon.mode, daemon.loaded = 'playing', True
                            started = time.monotonic()
                            daemon.pause()
                            elapsed = time.monotonic() - started
                            time.sleep(0.35)
                            rows = [json.loads(line) for line in trace.read_text().splitlines()]
                            tail = max((r['time'] - started for r in rows if r['nonzero']), default=0)
                            self.assertLess(tail, 0.3, 'old PCM keeps playing after Pause')
                            self.assertLess(elapsed, 1.0, 'Pause waits for queued audio to drain')
                            self.assertFalse(player.alive)
                            self.assertFalse(daemon.loaded)
                            self.assertEqual(daemon.mode, 'paused')
                            self.assertEqual(daemon.station['name'], 'Synthetic test station')
                finally:
                    player.kill_player()


if __name__ == '__main__':
    unittest.main()
