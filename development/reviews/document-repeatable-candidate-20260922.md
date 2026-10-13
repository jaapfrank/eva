# Repeated document projects — source and isolated integration review

Status: GO for the bounded source candidate; not installation approval or owner acceptance.

The native adapter author reviewed the core-v2 changes independently. Legacy claims retain their old pool and tool binding. Dynamic claims use deterministic task identity and atomic journal registration. Scoped paging, private owner checks and the existing stop/recovery route remain in place. No blocker found. Worker selection and health still scan historical claims; no constant-cost claim.

The coordinator reviewed the native resolver: the old broker and tool arguments remain unchanged; explicit version-2 binding pins the resolver hash. A held private root descriptor selects a project child, checks ownership, permissions and inode identity, and validates its durable marker. Observe does not create, sync or repair. Partial initialization fails closed. This assumes a trusted provider UID; restoration must preserve or deliberately rebind inode identity.

The core author independently reviewed the coordinator's gateway/native integration changes. GO: one legacy provider/claim remains v1 before pool upgrade, three new claims use the v2 provider. The real gateway/native path covers legacy crash/recovery, dynamic stop, provider restart and two additional completions. Default test flags retain the legacy two-slot fixture.

Evidence: [candidate record](../evidence/living-core/document-repeatable-candidate-20260922.json). Integration uses a source-mounted core worker, not a fully packaged release. Stopped dynamic files are checked before restart, not again after later completions; legacy files are rechecked. No deployment, new owner acceptance or general performance guarantee follows from this review.

Before installation: retain legacy providers, register and pin the new provider, drain foreground requests before the action configuration hash changes, capture a coherent gateway/core/worker rollback, and verify live hashes, health and old artifacts. An old worker cannot process newly admitted dynamic claims; rollback after admission must preserve those claims and their provider and reconcile work explicitly.
