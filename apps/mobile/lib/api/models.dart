/// Types mirroring packages/contracts/openapi/jarvis.json.
///
/// Hand-written rather than generated: the surface is small, and a generated client
/// would pull a Java toolchain into a build that otherwise needs none.
library;

class Goal {
  const Goal({
    required this.id,
    required this.title,
    required this.priority,
    required this.status,
    required this.version,
    this.outcome,
    this.deadline,
    this.timezone,
  });

  final String id;
  final String title;
  final String? outcome;
  final DateTime? deadline;
  final String? timezone;
  final int priority;
  final String status;
  final int version;

  factory Goal.fromJson(Map<String, dynamic> json) => Goal(
        id: json['id'] as String,
        title: json['title'] as String,
        outcome: json['outcome'] as String?,
        deadline: json['deadline'] == null
            ? null
            : DateTime.parse(json['deadline'] as String).toLocal(),
        timezone: json['timezone'] as String?,
        priority: json['priority'] as int,
        status: json['status'] as String,
        version: json['version'] as int,
      );
}

class Task {
  const Task({
    required this.id,
    required this.title,
    required this.status,
    required this.priority,
    required this.isOptional,
    required this.version,
    this.goalId,
    this.dueAt,
    this.estimateMinutes,
    this.remainingMinutes,
    this.evidenceSpan,
  });

  final String id;
  final String? goalId;
  final String title;
  final String status;
  final DateTime? dueAt;
  final int? estimateMinutes;
  final int? remainingMinutes;
  final int priority;
  final bool isOptional;

  /// The exact source substring a deadline was read from. This is what makes
  /// "why do you believe this?" answerable in the UI.
  final String? evidenceSpan;
  final int version;

  factory Task.fromJson(Map<String, dynamic> json) => Task(
        id: json['id'] as String,
        goalId: json['goal_id'] as String?,
        title: json['title'] as String,
        status: json['status'] as String,
        dueAt: json['due_at'] == null
            ? null
            : DateTime.parse(json['due_at'] as String).toLocal(),
        estimateMinutes: json['estimate_minutes'] as int?,
        remainingMinutes: json['remaining_minutes'] as int?,
        priority: json['priority'] as int,
        isOptional: json['is_optional'] as bool? ?? false,
        evidenceSpan: json['evidence_span'] as String?,
        version: json['version'] as int,
      );
}

class RecoveryOption {
  const RecoveryOption({
    required this.key,
    required this.title,
    required this.detail,
    required this.probabilityAfter,
    required this.minutesSaved,
    required this.tasksAffected,
    required this.requiresApproval,
  });

  final String key;
  final String title;
  final String detail;
  final double probabilityAfter;
  final double minutesSaved;
  final List<String> tasksAffected;

  /// True when the option depends on somebody else agreeing. Surfaced in the UI
  /// because "drop two optional tasks" and "ask for an extension" are different
  /// kinds of decision.
  final bool requiresApproval;

  factory RecoveryOption.fromJson(Map<String, dynamic> json) => RecoveryOption(
        key: json['key'] as String,
        title: json['title'] as String,
        detail: json['detail'] as String,
        probabilityAfter: (json['probability_after'] as num).toDouble(),
        minutesSaved: (json['minutes_saved'] as num).toDouble(),
        tasksAffected:
            (json['tasks_affected'] as List<dynamic>).cast<String>(),
        requiresApproval: json['requires_approval'] as bool,
      );
}

class Prediction {
  const Prediction({
    required this.goalId,
    required this.severity,
    required this.probability,
    required this.availableMinutes,
    required this.p80RemainingMinutes,
    required this.explanation,
    required this.options,
  });

  final String goalId;
  final String severity;
  final double probability;
  final double availableMinutes;
  final double p80RemainingMinutes;
  final String explanation;
  final List<RecoveryOption> options;

  bool get needsAttention => severity == 'at_risk' || severity == 'critical';

  factory Prediction.fromJson(Map<String, dynamic> json) => Prediction(
        goalId: json['goal_id'] as String,
        severity: json['severity'] as String,
        probability: (json['probability'] as num).toDouble(),
        availableMinutes: (json['available_minutes'] as num).toDouble(),
        p80RemainingMinutes:
            (json['p80_remaining_minutes'] as num).toDouble(),
        explanation: json['explanation'] as String,
        options: (json['options'] as List<dynamic>)
            .map((o) => RecoveryOption.fromJson(o as Map<String, dynamic>))
            .toList(),
      );
}

class Approval {
  const Approval({
    required this.id,
    required this.actionId,
    required this.expiresAt,
    required this.requiresLocalConfirmation,
    required this.locallyConfirmed,
    this.decision,
  });

  final String id;
  final String actionId;
  final String? decision;
  final DateTime expiresAt;
  final bool requiresLocalConfirmation;
  final bool locallyConfirmed;

  bool get isPending => decision == null;

  factory Approval.fromJson(Map<String, dynamic> json) => Approval(
        id: json['id'] as String,
        actionId: json['action_id'] as String,
        decision: json['decision'] as String?,
        expiresAt: DateTime.parse(json['expires_at'] as String).toLocal(),
        requiresLocalConfirmation:
            json['requires_local_confirmation'] as bool,
        locallyConfirmed: json['locally_confirmed'] as bool,
      );
}

class DeviceInfo {
  const DeviceInfo({
    required this.id,
    required this.name,
    required this.platform,
    required this.fingerprint,
    required this.paired,
    required this.revoked,
    required this.online,
    required this.allowedBundleIds,
    this.lastSeenAt,
  });

  final String id;
  final String name;
  final String platform;
  final String fingerprint;
  final bool paired;
  final bool revoked;
  final bool online;
  final DateTime? lastSeenAt;
  final List<String> allowedBundleIds;

  factory DeviceInfo.fromJson(Map<String, dynamic> json) => DeviceInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        platform: json['platform'] as String,
        fingerprint: json['fingerprint'] as String,
        paired: json['paired'] as bool,
        revoked: json['revoked'] as bool,
        online: json['online'] as bool,
        lastSeenAt: json['last_seen_at'] == null
            ? null
            : DateTime.parse(json['last_seen_at'] as String).toLocal(),
        allowedBundleIds:
            (json['allowed_bundle_ids'] as List<dynamic>).cast<String>(),
      );
}

/// An RFC 9457 problem document. Carries the correlation id, so a user can report
/// a failure by a value that finds it in the logs.
class ProblemException implements Exception {
  const ProblemException({
    required this.status,
    required this.title,
    this.detail,
    this.correlationId,
  });

  final int status;
  final String title;
  final String? detail;
  final String? correlationId;

  factory ProblemException.fromJson(int status, Map<String, dynamic> json) =>
      ProblemException(
        status: json['status'] as int? ?? status,
        title: json['title'] as String? ?? 'Request failed',
        detail: json['detail'] as String?,
        correlationId: json['correlation_id'] as String?,
      );

  @override
  String toString() => detail ?? title;
}
