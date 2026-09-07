import 'dart:async';
import 'dart:convert';

import 'package:crypto/crypto.dart';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'canonical.dart';
import 'keys.dart';
import 'mac_hands.dart';

/// The phone as an execution node — the Mac helper's protocol, in Dart.
///
/// One outbound WebSocket. Every job arrives signed by the server and is checked here
/// for signature, expiry, nonce replay and this phone's own allowlist before anything
/// happens; every result goes back signed by the phone's key. What the phone can do
/// is four typed verbs, and none of them sends anything without a thumb:
///
/// * `phone.open_url`        — hand a URL to the OS (https, mailto, tel, whatsapp…)
/// * `phone.whatsapp_draft`  — open WhatsApp with the text filled in; you tap Send
/// * `phone.open_app`        — launch an allowlisted app
/// * `phone.notify`          — show a message in the app
/// * `phone.open_settings`   — open one settings panel (wifi, bluetooth, display…)
/// * `phone.call`            — open the dialer with the number filled in; you press call
/// * `phone.sms_draft`       — open the SMS app with number and text filled in
/// * `phone.open_deeplink`   — hand an app link to the OS (maps, spotify, upi…)
/// * `phone.call`            — place the call (dialer fallback without the permission)
/// * `phone.ring`            — ring loudly to find the phone
/// * `phone.locate`          — read GPS and send you a maps link
/// * `phone.whatsapp_send`   — auto-send (needs the opt-in Accessibility Service)
class PhoneNode {
  PhoneNode({
    required this.launchUrl,
    required this.launchApp,
    required this.notify,
    Future<bool> Function(String intentAction)? launchIntent,
    Future<List<int>?> Function()? captureImage,
    Future<String?> Function(String kind, String filename, List<int> bytes)? uploadArtifact,
    Future<String?> Function()? readClipboard,
    Future<void> Function(String text)? writeClipboard,
    Future<bool> Function(String number)? placeCall,
    Future<bool> Function()? ring,
    Future<Map<String, dynamic>?> Function()? locate,
    Future<bool> Function(String phone, String text)? whatsappSend,
    Future<Map<String, dynamic>> Function()? systemInfo,
    Future<bool> Function(bool on)? torch,
    Future<bool> Function(String command)? media,
    this.macHands,
    WebSocketChannel Function(Uri)? connect,
  })  : launchIntent = launchIntent ?? ((_) async => false),
        placeCall = placeCall ?? ((_) async => false),
        ring = ring ?? (() async => false),
        locate = locate ?? (() async => null),
        whatsappSend = whatsappSend ?? ((_, __) async => false),
        systemInfo = systemInfo ?? (() async => <String, dynamic>{}),
        torch = torch ?? ((_) async => false),
        media = media ?? ((_) async => false),
        captureImage = captureImage ?? (() async => null),
        uploadArtifact = uploadArtifact ?? ((_, __, ___) async => null),
        readClipboard = readClipboard ?? (() async => null),
        writeClipboard = writeClipboard ?? ((_) async {}),
        _connect = connect ?? WebSocketChannel.connect;

  /// Platform hooks, injected so the protocol is testable without a phone.
  final Future<bool> Function(Uri url) launchUrl;
  final Future<bool> Function(String package) launchApp;
  final Future<void> Function(String title, String body) notify;
  final Future<bool> Function(String intentAction) launchIntent;
  /// Place an actual call (ACTION_CALL, needs the call permission), fall back to dialer.
  final Future<bool> Function(String number) placeCall;
  /// Ring loudly even in silent mode, to find the device.
  final Future<bool> Function() ring;
  /// Current location, or null if permission was refused: {lat, lng, accuracy_m}.
  final Future<Map<String, dynamic>?> Function() locate;
  /// Send a WhatsApp message on its own — only if the Accessibility Service is enabled.
  final Future<bool> Function(String phone, String text) whatsappSend;
  /// Battery %, charging, free storage, network type.
  final Future<Map<String, dynamic>> Function() systemInfo;
  /// The flashlight.
  final Future<bool> Function(bool on) torch;
  final Future<bool> Function(String command) media;
  /// One photo, taken by the user through the system camera — never a live feed.
  final Future<List<int>?> Function() captureImage;
  final Future<String?> Function(String kind, String filename, List<int> bytes) uploadArtifact;
  final Future<String?> Function() readClipboard;
  final Future<void> Function(String text) writeClipboard;
  /// Present when this app runs on a Mac: the Mac verbs are answered here.
  final MacHands? macHands;
  final WebSocketChannel Function(Uri) _connect;

  static const phoneActions = {
    'phone.open_url',
    'phone.whatsapp_draft',
    'phone.open_app',
    'phone.notify',
    'phone.open_settings',
    'phone.call',
    'phone.sms_draft',
    'phone.open_deeplink',
    'phone.camera',
    'phone.clipboard_read',
    'phone.clipboard_write',
    'phone.media',
  };

  /// What this node answers: the phone verbs, plus the Mac verbs when it is a Mac.
  Set<String> get allowedActions =>
      macHands == null ? phoneActions : {...phoneActions, ...MacHands.verbs};
  static const allowedSchemes = {'https', 'http', 'mailto', 'tel', 'whatsapp', 'maps'};
  /// App links the phone will hand to the OS. Checked here as well as at the server.
  static const deeplinkSchemes = {
    ...allowedSchemes, 'geo', 'spotify', 'youtube', 'upi', 'sms',
  };
  /// Settings panels by name → the Android intent that opens exactly that panel.
  static const settingsPanels = {
    'wifi': 'android.settings.WIFI_SETTINGS',
    'bluetooth': 'android.settings.BLUETOOTH_SETTINGS',
    'display': 'android.settings.DISPLAY_SETTINGS',
    'sound': 'android.settings.SOUND_SETTINGS',
    'battery': 'android.settings.BATTERY_SAVER_SETTINGS',
    'location': 'android.settings.LOCATION_SOURCE_SETTINGS',
    'dnd': 'android.settings.ZEN_MODE_SETTINGS',
    'airplane': 'android.settings.AIRPLANE_MODE_SETTINGS',
    'apps': 'android.settings.APPLICATION_SETTINGS',
  };
  static const defaultPackages = [
    'com.whatsapp',
    'com.android.chrome',
    'com.google.android.gm',
    'com.google.android.apps.maps',
    // Mac bundle ids, for when this app is the Mac's hand.
    'com.google.Chrome',
    'com.apple.Safari',
    'net.whatsapp.WhatsApp',
    'com.apple.Notes',
    'com.apple.Music',
    'com.spotify.client',
    'com.apple.finder',
    'com.microsoft.VSCode',
  ];

  DeviceKey? _key;
  String? deviceId;
  String? serverPublicPem;
  Set<String> allowedPackages = defaultPackages.toSet();
  bool stopped = false;
  bool connected = false;
  String? lastError;
  final List<String> _nonces = [];
  final List<Map<String, dynamic>> log = [];

  WebSocketChannel? _channel;
  Timer? _heartbeat;
  Timer? _reconnect;
  String? _lastWsBase;
  String? _lastToken;
  /// Come back after a drop without a tap. Off during tests and after Disconnect.
  bool autoReconnect = true;
  final _changes = StreamController<void>.broadcast();

  /// Fires after any state change; the UI adapter turns it into rebuilds.
  Stream<void> get changes => _changes.stream;

  void notifyListeners() {
    if (!_changes.isClosed) _changes.add(null);
  }

  void dispose() {
    disconnect();
    _changes.close();
  }

  bool get paired => deviceId != null && _key != null && serverPublicPem != null;

  // ── identity ─────────────────────────────────────────────────────────
  /// Restore from storage (scalar hex, device id, server key) or start fresh.
  void restore({String? scalarHex, String? deviceId, String? serverPublicPem}) {
    if (scalarHex != null) _key = DeviceKey.fromScalarHex(scalarHex);
    this.deviceId = deviceId;
    this.serverPublicPem = serverPublicPem;
    notifyListeners();
  }

  DeviceKey ensureKey() => _key ??= DeviceKey.generate();
  String? get scalarHex => _key?.scalarHex;
  String get publicPem => ensureKey().publicPem;

  /// The pairing handshake, given the server round-trips as callbacks — the HTTP
  /// client already knows how to authenticate; this only knows how to sign.
  Future<void> pair({
    required Future<Map<String, dynamic>> Function(Map<String, dynamic> body) begin,
    required Future<Map<String, dynamic>> Function(Map<String, dynamic> body) complete,
    required Future<String> Function() serverKey,
    String name = 'My phone',
    String platform = 'android',
  }) async {
    final key = ensureKey();
    final started = await begin({
      'name': name,
      'platform': platform,
      'public_key_pem': key.publicPem,
      'allowed_bundle_ids': allowedPackages.toList(),
      'capabilities': allowedActions.toList(),
    });
    final challenge = started['challenge'] as String;
    final device = await complete({
      'challenge': challenge,
      'signature': key.sign(utf8.encode(challenge)),
    });
    deviceId = device['id'] as String;
    serverPublicPem = await serverKey();
    notifyListeners();
  }

  // ── the socket ───────────────────────────────────────────────────────
  Future<void> connect({required String wsBase, required String accessToken}) async {
    if (!paired) throw StateError('pair the phone first');
    _lastWsBase = wsBase;
    _lastToken = accessToken;
    _reconnect?.cancel();
    _closeSocket();
    final uri = Uri.parse('$wsBase/v1/devices/ws').replace(queryParameters: {
      'token': accessToken,
      'device_id': deviceId!,
    });
    final channel = _connect(uri);
    _channel = channel;
    connected = true;
    lastError = null;
    notifyListeners();
    _heartbeat = Timer.periodic(const Duration(seconds: 30), (_) {
      channel.sink.add(jsonEncode({'type': 'device.heartbeat'}));
    });
    channel.stream.listen(
      (raw) => handleMessage(jsonDecode(raw as String) as Map<String, dynamic>),
      onError: (Object e) {
        lastError = '$e';
        _dropped();
      },
      onDone: _dropped,
    );
  }

  /// The socket went away underneath us: report it and try again shortly.
  void _dropped() {
    _closeSocket();
    if (autoReconnect && !stopped && _lastWsBase != null && _lastToken != null) {
      _reconnect?.cancel();
      _reconnect = Timer(const Duration(seconds: 5), () {
        if (!connected && paired) {
          connect(wsBase: _lastWsBase!, accessToken: _lastToken!).catchError((_) {});
        }
      });
    }
  }

  /// The button: stay disconnected until Connect is pressed again.
  void disconnect() {
    _reconnect?.cancel();
    _reconnect = null;
    _lastWsBase = null;
    _closeSocket();
  }

  void _closeSocket() {
    _heartbeat?.cancel();
    _heartbeat = null;
    _channel?.sink.close();
    _channel = null;
    if (connected) {
      connected = false;
      notifyListeners();
    }
  }

  /// The STOP button: outranks a valid signature, exactly as on the Mac.
  void stop() {
    stopped = true;
    notifyListeners();
  }

  void resume() {
    stopped = false;
    notifyListeners();
  }

  // ── jobs ─────────────────────────────────────────────────────────────
  Future<void> handleMessage(Map<String, dynamic> message) async {
    if (message['type'] != 'job.dispatch') return;
    _send({'type': 'job.ack', 'job_id': message['job_id']});
    final result = await execute(message);
    _send(result);
  }

  /// Admit, run, sign. Returns the wire form of a `job.result`.
  Future<Map<String, dynamic>> execute(Map<String, dynamic> job) async {
    final jobId = job['job_id'] as String;
    final rejection = admit(job);
    final Map<String, dynamic> result;
    if (rejection != null) {
      result = {
        'job_id': jobId,
        'status': 'rejected',
        'observed': <String, dynamic>{},
        'error': rejection.$2,
        'reject_reason': rejection.$1,
      };
    } else {
      Map<String, dynamic> observed;
      var status = 'completed';
      String? error;
      try {
        observed = await _run(
          job['action'] as String,
          (job['args'] as Map).cast<String, dynamic>(),
        );
      } catch (e) {
        observed = {};
        status = 'failed';
        error = '$e';
      }
      result = {
        'job_id': jobId,
        'status': status,
        'observed': observed,
        'error': error,
        'reject_reason': null,
      };
    }
    final signature = ensureKey().sign(canonicalBytes(result));
    log.insert(0, {...result, 'at': DateTime.now().toIso8601String()});
    if (log.length > 50) log.removeLast();
    notifyListeners();
    return {'type': 'job.result', ...result, 'signature': signature};
  }

  /// `(reject_reason, detail)` or null when the job may run. Same order as the Mac
  /// helper's guard: cheapest and most decisive first.
  (String, String)? admit(Map<String, dynamic> job) {
    if (stopped) return ('stopped', 'phone node is stopped');
    final payload = canonicalBytes({
      'job_id': job['job_id'],
      'action': job['action'],
      'args': job['args'],
      'risk': job['risk'],
      'nonce': job['nonce'],
      'issued_at': job['issued_at'],
      'expires_at': job['expires_at'],
      'policy_version': job['policy_version'],
      'device_id': job['device_id'],
    });
    final pem = serverPublicPem;
    final signature = (job['signature'] as String?) ?? '';
    if (pem == null || !DeviceKey.verify(pem, payload, signature)) {
      return ('bad_signature', 'job signature did not verify');
    }
    final expires = DateTime.tryParse(job['expires_at'] as String? ?? '');
    if (expires == null || !expires.isAfter(DateTime.now().toUtc())) {
      return ('expired', 'job expired at ${job['expires_at']}');
    }
    final nonce = job['nonce'] as String;
    if (_nonces.contains(nonce)) {
      return ('replayed_nonce', 'this job has already been seen');
    }
    final action = job['action'] as String;
    if (!allowedActions.contains(action)) {
      return ('unknown_action', '$action is not enabled on this phone');
    }
    final args = (job['args'] as Map).cast<String, dynamic>();
    if (action == 'phone.open_url' || action == 'mac.open_url') {
      final scheme = Uri.tryParse(args['url'] as String? ?? '')?.scheme.toLowerCase();
      if (scheme == null || !allowedSchemes.contains(scheme)) {
        return ('not_allowlisted', 'scheme $scheme is not enabled on this device');
      }
    }
    if ((action == 'phone.open_app' || action == 'mac.open_app') &&
        !allowedPackages.contains(args['bundle_id'])) {
      return ('not_allowlisted', "${args['bundle_id']} is not on this device's allowlist");
    }
    if (action == 'mac.set_setting' &&
        !MacHands.settings.contains('${args['key']}'.toLowerCase())) {
      return ('not_allowlisted', "${args['key']} is not a setting this Mac exposes");
    }
    if (action == 'phone.open_deeplink') {
      final scheme = Uri.tryParse(args['url'] as String? ?? '')?.scheme.toLowerCase();
      if (scheme == null || !deeplinkSchemes.contains(scheme)) {
        return ('not_allowlisted', 'scheme $scheme is not enabled on this phone');
      }
    }
    if (action == 'phone.open_settings' && !settingsPanels.containsKey(args['panel'])) {
      return ('not_allowlisted', "${args['panel']} is not a panel this phone opens");
    }
    _nonces.add(nonce);
    if (_nonces.length > 2048) _nonces.removeAt(0);
    return null;
  }

  Future<Map<String, dynamic>> _run(String action, Map<String, dynamic> args) async {
    if (action.startsWith('mac.')) {
      final hands = macHands;
      if (hands == null) throw StateError('not a Mac');
      return hands.handle(action, args);
    }
    switch (action) {
      case 'phone.open_url':
        final url = args['url'] as String;
        return {'opened': await launchUrl(Uri.parse(url)), 'opened_url': url};
      case 'phone.whatsapp_draft':
        final digits = (args['phone'] as String).replaceAll(RegExp(r'\D'), '');
        final url = Uri(
          scheme: 'whatsapp',
          host: 'send',
          queryParameters: {'phone': digits, 'text': args['text'] as String},
        );
        return {'opened': await launchUrl(url), 'opened_url': url.toString()};
      case 'phone.open_app':
        final launched = await launchApp(args['bundle_id'] as String);
        return {'is_running': launched, 'package': args['bundle_id']};
      case 'phone.notify':
        await notify(args['title'] as String, args['body'] as String);
        return {'status': 200};
      case 'phone.open_settings':
        final panel = args['panel'] as String;
        final opened = await launchIntent(settingsPanels[panel]!);
        return {'opened': opened, 'opened_url': 'settings:$panel'};
      case 'phone.call':
        // Places the call outright when the permission is granted; otherwise the OS
        // dialer opens with the number and the observation says so.
        final number = _digits(args['number'] as String);
        final placed = await placeCall(number);
        if (placed) return {'opened': true, 'opened_url': 'tel:$number', 'placed': true};
        final url = Uri(scheme: 'tel', path: number);
        return {'opened': await launchUrl(url), 'opened_url': url.toString(), 'placed': false};
      case 'phone.ring':
        return {'status': await ring() ? 200 : 500};
      case 'phone.system_info':
        return {'status': 200, ...await systemInfo()};
      case 'phone.media':
        return {'status': await media(args['command'] as String? ?? 'playpause') ? 200 : 500};
      case 'phone.torch':
        final on = args['on'] as bool? ?? true;
        return {'status': await torch(on) ? 200 : 500, 'on': on};
      case 'phone.locate':
        final where = await locate();
        if (where == null) return {'status': 403, 'error': 'location permission refused'};
        final lat = where['lat'], lng = where['lng'];
        return {
          'status': 200,
          'lat': lat,
          'lng': lng,
          'accuracy_m': where['accuracy_m'],
          'maps_url': 'https://maps.google.com/?q=$lat,$lng',
        };
      case 'phone.whatsapp_send':
        final digits = _digits(args['phone'] as String);
        final sent = await whatsappSend(digits, args['text'] as String);
        // The Accessibility Service taps Send; the observation is whether it did.
        return {'opened': true, 'pressed_key': sent, 'opened_url': 'whatsapp:send'};
      case 'phone.sms_draft':
        final url = Uri(
          scheme: 'sms',
          path: _digits(args['number'] as String),
          queryParameters: {'body': args['text'] as String},
        );
        return {'opened': await launchUrl(url), 'opened_url': url.toString()};
      case 'phone.open_deeplink':
        final url = args['url'] as String;
        return {'opened': await launchUrl(Uri.parse(url)), 'opened_url': url};
      case 'phone.clipboard_read':
        final text = await readClipboard();
        return {'status': 200, 'text': (text ?? '').substring(0, (text ?? '').length.clamp(0, 4000))};
      case 'phone.clipboard_write':
        await writeClipboard(args['text'] as String);
        return {'status': 200};
      case 'phone.camera':
        final bytes = await captureImage();
        if (bytes == null) return {'permission': 'denied', 'digest': null};
        final digest = 'sha256:${sha256.convert(bytes)}';
        final artifactId = await uploadArtifact('capture', 'photo.jpg', bytes);
        return {'permission': 'granted', 'digest': digest, 'artifact_id': artifactId};
      default:
        throw StateError('unhandled action $action');
    }
  }

  void _send(Map<String, dynamic> message) => _channel?.sink.add(jsonEncode(message));

  /// Keep a leading + and the digits; everything else in a number is decoration.
  static String _digits(String number) {
    final trimmed = number.trim();
    final digits = trimmed.replaceAll(RegExp(r'\D'), '');
    return trimmed.startsWith('+') ? '+$digits' : digits;
  }
}
