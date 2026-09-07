import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';

/// The Mac app as the Mac's hand (PLAN.md 11.1). The same signed-job protocol the phone
/// node speaks, with the common Mac verbs done by this app through `open`, `osascript`,
/// `screencapture`, `pbpaste`, `networksetup` — each an argv list, never a shell string.
/// The Python helper stays for accessibility-heavy verbs (read_ui, press_button, typing).
typedef Runner = Future<ProcessResult> Function(String executable, List<String> args,
    {String? stdin});

Future<ProcessResult> realRunner(String executable, List<String> args, {String? stdin}) async {
  final process = await Process.start(executable, args);
  if (stdin != null) {
    process.stdin.write(stdin);
  }
  await process.stdin.close();
  final out = await process.stdout.transform(utf8.decoder).join();
  final err = await process.stderr.transform(utf8.decoder).join();
  final code = await process.exitCode;
  return ProcessResult(process.pid, code, out, err);
}

class MacHands {
  MacHands({
    required this.run,
    required this.uploadArtifact,
    this.tempDir = '/tmp',
  });

  final Runner run;
  final Future<String?> Function(String kind, String filename, List<int> bytes) uploadArtifact;
  final String tempDir;

  static const verbs = {
    'mac.capture_screen',
    'mac.describe_screen',
    'mac.open_url',
    'mac.open_app',
    'mac.notify',
    'mac.say',
    'mac.set_volume',
    'mac.lock_screen',
    'mac.clipboard_read',
    'mac.clipboard_write',
    'mac.media',
    'mac.set_setting',
    'mac.system_info',
    'mac.whatsapp_send',
    'mac.ring',
  };
  static const settings = {'wifi', 'bluetooth', 'dark_mode', 'do_not_disturb', 'brightness'};
  static const mediaApps = {'Music', 'Spotify'};
  static const mediaCommands = {'play', 'pause', 'playpause', 'next track', 'previous track'};

  Future<Map<String, dynamic>> handle(String action, Map<String, dynamic> args) async {
    switch (action) {
      case 'mac.capture_screen':
      case 'mac.describe_screen':
        return _capture();
      case 'mac.open_url':
        final url = args['url'] as String;
        final r = await run('/usr/bin/open', [url]);
        return {'opened': r.exitCode == 0, 'opened_url': url};
      case 'mac.open_app':
        final bundle = args['bundle_id'] as String;
        final r = await run('/usr/bin/open', ['-b', bundle]);
        return {'is_running': r.exitCode == 0, 'frontmost_bundle_id': bundle};
      case 'mac.notify':
        final r = await _osascript(
            'display notification ${_q(args['body'] as String)} with title ${_q(args['title'] as String)}');
        return {'status': r.exitCode == 0 ? 200 : 500};
      case 'mac.say':
        final r = await run('/usr/bin/say', [(args['text'] as String).substring(0, (args['text'] as String).length.clamp(0, 600))]);
        return {'status': r.exitCode == 0 ? 200 : 500};
      case 'mac.set_volume':
        final level = ((args['level'] as num).toInt()).clamp(0, 100);
        final r = await _osascript('set volume output volume $level');
        return {'status': r.exitCode == 0 ? 200 : 500, 'level': level};
      case 'mac.lock_screen':
        final r = await _osascript(
            'tell application "System Events" to keystroke "q" using {command down, control down}');
        return {'status': r.exitCode == 0 ? 200 : 500};
      case 'mac.clipboard_read':
        final r = await run('/usr/bin/pbpaste', []);
        return {'status': r.exitCode == 0 ? 200 : 500, 'text': (r.stdout as String).substring(0, (r.stdout as String).length.clamp(0, 4000))};
      case 'mac.clipboard_write':
        final r = await run('/usr/bin/pbcopy', [], stdin: args['text'] as String);
        return {'status': r.exitCode == 0 ? 200 : 500};
      case 'mac.media':
        final app = args['app'] as String;
        final command = args['command'] as String;
        if (!mediaApps.contains(app) || !mediaCommands.contains(command)) {
          return {'status': 400, 'error': 'media verb not allowlisted'};
        }
        final r = await _osascript('tell application "$app" to $command');
        return {'status': r.exitCode == 0 ? 200 : 500};
      case 'mac.set_setting':
        return _setSetting((args['key'] as String).toLowerCase(), '${args['value']}'.toLowerCase());
      case 'mac.system_info':
        return _systemInfo();
      case 'mac.ring':
        // Full volume, then a spoken chime a few times, so a lost Mac answers back.
        await _osascript('set volume output volume 90');
        for (var i = 0; i < 3; i++) {
          await run('/usr/bin/afplay', ['/System/Library/Sounds/Ping.aiff']);
        }
        await run('/usr/bin/say', ['Here I am. Jarvis is ringing this Mac.']);
        return {'status': 200};
      case 'mac.whatsapp_send':
        // Open the chat with the text filled in, give WhatsApp a moment to come
        // forward, then press Return — only if WhatsApp is the frontmost app.
        final digits = (args['phone'] as String).replaceAll(RegExp(r'\D'), '');
        final url = Uri(scheme: 'whatsapp', host: 'send',
            queryParameters: {'phone': digits, 'text': args['text'] as String}).toString();
        final opened = await run('/usr/bin/open', [url]);
        await Future<void>.delayed(const Duration(milliseconds: 1800));
        final front = await _osascript(
            'tell application "System Events" to get bundle identifier of first process whose frontmost is true');
        final frontmost = (front.stdout as String).trim();
        var pressed = false;
        if (frontmost == 'net.whatsapp.WhatsApp') {
          final r = await _osascript('tell application "System Events" to keystroke return');
          pressed = r.exitCode == 0;
        }
        return {
          'opened': opened.exitCode == 0,
          'opened_url': url,
          'frontmost_bundle_id': frontmost,
          'pressed_key': pressed,
        };
      default:
        throw StateError('unhandled mac action $action');
    }
  }

  Future<Map<String, dynamic>> _capture() async {
    final path = '$tempDir/jarvis-${DateTime.now().millisecondsSinceEpoch}.png';
    final r = await run('/usr/sbin/screencapture', ['-x', path]);
    if (r.exitCode != 0) return {'permission': 'screen_recording_denied', 'digest': null};
    final file = File(path);
    if (!await file.exists()) return {'permission': 'granted', 'digest': null};
    final bytes = await file.readAsBytes();
    await file.delete();
    final digest = 'sha256:${sha256.convert(bytes)}';
    final artifactId = await uploadArtifact('screenshot', 'screen.png', bytes);
    return {'permission': 'granted', 'digest': digest, 'artifact_id': artifactId};
  }

  Future<Map<String, dynamic>> _setSetting(String key, String value) async {
    if (!settings.contains(key)) return {'status': 400, 'error': '$key is not a setting this Mac exposes'};
    final on = const {'on', 'true', '1'}.contains(value);
    String? state;
    switch (key) {
      case 'wifi':
        await run('/usr/sbin/networksetup', ['-setairportpower', 'en0', on ? 'on' : 'off']);
        final r = await run('/usr/sbin/networksetup', ['-getairportpower', 'en0']);
        if (r.exitCode == 0) state = (r.stdout as String).trim().toLowerCase().endsWith('on') ? 'on' : 'off';
      case 'dark_mode':
        await _osascript('tell application "System Events" to tell appearance preferences to set dark mode to ${on ? 'true' : 'false'}');
        final r = await _osascript('tell application "System Events" to tell appearance preferences to get dark mode');
        if (r.exitCode == 0) state = (r.stdout as String).trim() == 'true' ? 'on' : 'off';
      case 'bluetooth':
        final r0 = await run('/opt/homebrew/bin/blueutil', ['-p', on ? '1' : '0']);
        if (r0.exitCode == 0) {
          final r = await run('/opt/homebrew/bin/blueutil', ['-p']);
          if (r.exitCode == 0) state = (r.stdout as String).trim() == '1' ? 'on' : 'off';
        }
      case 'do_not_disturb':
        final r = await run('/usr/bin/shortcuts', ['run', on ? 'Jarvis DND On' : 'Jarvis DND Off']);
        if (r.exitCode == 0) state = on ? 'on' : 'off';
      case 'brightness':
        final level = double.tryParse(value);
        if (level != null) {
          final r0 = await run('/opt/homebrew/bin/brightness', ['${(level.clamp(0, 100)) / 100}']);
          if (r0.exitCode == 0) state = '${level.clamp(0, 100).round()}';
        }
    }
    if (state == null) return {'status': 501, 'key': key, 'value': value, 'error': 'not available here'};
    return {'status': 200, 'key': key, 'value': value, 'state': state};
  }

  Future<Map<String, dynamic>> _systemInfo() async {
    final info = <String, dynamic>{'status': 200};
    final batt = await run('/usr/bin/pmset', ['-g', 'batt']);
    final pct = RegExp(r'(\d+)%').firstMatch(batt.stdout as String);
    if (batt.exitCode == 0 && pct != null) {
      info['battery_percent'] = int.parse(pct.group(1)!);
      info['charging'] = !(batt.stdout as String).toLowerCase().contains('discharging');
    }
    final load = await run('/usr/sbin/sysctl', ['-n', 'vm.loadavg']);
    final l1 = RegExp(r'([\d.]+)').firstMatch(load.stdout as String);
    if (load.exitCode == 0 && l1 != null) info['load_1m'] = double.parse(l1.group(1)!);
    final df = await run('/bin/df', ['-g', '/']);
    final lines = (df.stdout as String).split('\n');
    if (df.exitCode == 0 && lines.length > 1) {
      final parts = lines[1].split(RegExp(r'\s+'));
      if (parts.length > 3 && int.tryParse(parts[3]) != null) info['disk_free_gb'] = int.parse(parts[3]);
    }
    return info;
  }

  Future<ProcessResult> _osascript(String script) => run('/usr/bin/osascript', ['-e', script]);

  static String _q(String text) => '"${text.replaceAll('\\', '\\\\').replaceAll('"', '\\"')}"';
}
