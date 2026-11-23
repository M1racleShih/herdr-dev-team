# Six-pack Hardener

Strengthen weak coverage of the approved behavior. Run pinned `unit` and `acceptance` on the clean candidate. In a disposable checkout of that exact commit, introduce one meaningful code defect while preserving the approved JSON and tests. Rerun the identical `unit` command and identify which test detects the defect.

Save the full original and mutant outputs and a replayable Git patch in the private store. The `mutation` row records the checked commit before mutation, exit code, log, patch, qualified unittest test ID and assertion. Report `done` only with evidence that the named test passed on the original and failed at the intended assertion with the pinned exit code. If a pinned check demonstrates a problem requiring Coder rework, report `fail`. Report `superseded` if the specification changed. See the [mutation receipt format](six-pack.md#hardener-mutation) beside this brief.
