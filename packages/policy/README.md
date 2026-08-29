# policy — deterministic risk rules

The policy engine lives **outside** the agent. The model proposes; this decides; the executor revalidates.

Rules are **data**, not code branches, so they are reviewable and testable:

```yaml
- tool: mac.open_app
  risk: R1
  requires_approval: false
  conditions: [device_is_paired_owner, bundle_id_in_allowlist]

- tool: message.send
  risk: R2
  requires_approval: true
  approval_binds: [tool, args, user, device, expires_at]
```

Every rule ships with test vectors in `tests/`. A rule with no test does not merge.

`policy_version` is stamped into every dispatched job, so an audit entry can always answer
*"which rules were in force when this ran?"*
