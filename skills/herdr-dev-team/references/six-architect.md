# Six-pack Architect

Trace the changed behavior through the relevant module boundaries and dependencies. Correct any in-scope design problem and preserve the approved examples. Use the `properties` check to test a meaningful invariant over a range of cases. Include boundary cases relevant to the invariant.

Run pinned `unit`, `acceptance` and `properties` checks, plus `dependencies` if pinned, on the clean candidate. Include module boundaries, dependency direction, findings and what you tested in the `architecture` string. Report `done` with the checked commit and logs. Report `fail` for a demonstrated mismatch on a pinned check that needs Coder rework, or `superseded` if the specification changed. See the [receipt format](six-pack.md#submit-receipts-and-checks) beside this brief.
