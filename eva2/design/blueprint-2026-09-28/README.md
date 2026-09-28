# Complete Eva2 implementation blueprint

28 September 2026 · design proposal · no new runtime release.

Start with the Dutch [owner plan](../../EVA2_BOUWPLAN.md).
This directory contains the detailed specifications for that one plan:

1. [Architecture and shared contracts](architecture.md).
2. [Platform decision, pins and replacement seams](platform-decision.md).
3. [Learning, initiative and optional technical research](learning-and-initiative.md).
4. [Bounded delivery slices and completion criteria](delivery-plan.md).

The owner explicitly clarified that this assignment concerns the complete Eva2
family product, not a business edition. Current progress and assignments remain
in [WERKBORD](../../WERKBORD.md) and the existing execution-workboard JSON.
Specifications do not change accepted/live statuses. The historical research is
[here](../../research/2026-09-27-system-choice/README.md).

Design inputs read: current owner decisions and workboard; the documentation and
dependency-policy entry sections; the prior platform, building-block and actual
Eva1/Eva2 baseline research; the technical-differentiation proposal; EV-003's current
protocol. Platform research additionally checked targeted composition signatures,
locks and official interface documentation. This is not a new complete-repository
documentation-read claim. Known limitations retain the source coverage of those reports.

Ownership for this design pass: root owns architecture, delivery specification,
owner plan and primary documents; platform_research_20260927 owns platform decision;
baseline_research_20260927 owns learning and initiative. Base e94f3845. One bundled
independent review covers cross-document consistency. Review outcome is recorded
in the canonical workboard's completeEvaBlueprint entry and summarized below at completion.

Review completed: platform and baseline researchers independently checked the root
architecture/delivery specification; the platform reviewer also checked the learning
specification. Root reviewed both authored components. Resolved findings: B0 is a
source/stub gate, live comparison belongs to C1/L1; a local product gap cannot justify
a second planner. No remaining blocking design finding was reported. Compatibility,
performance, hardware behavior and innovation remain unproven until their named trials.
