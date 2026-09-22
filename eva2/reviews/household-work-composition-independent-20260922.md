# Independent source review: household work composition

Date: 2026-09-22. Reviewer: coordinator, separate from implementation task.
Candidate 204ed63b; integrated source 3933589b. Source GO, installation pending.

Reviewed the audience-scoped provider, explicit core/worker authority composition, API session conversion, media envelope/acknowledgment handling and native speaker lifecycle. Existing private provider remains selected for private turns. Household actions require the recorded server-issued session and ingress. Closure/revocation cannot silently remove a reply guard. Polling creates no user turn. The shared media agent keeps result audio outside model history and rechecks authority; speaker cleanup disables physical output before closing the core session.

Independent focused validation: 7 PostgreSQL/configuration tests passed in 5.18 seconds; 4 household media tests passed in 2.89 seconds. The PostgreSQL database was disposable and removed after the run. Transport, research and speech use deterministic fixtures. No model calls, Telegram messages or production writes. Broader builder tests remain attributed to its handoff, not claimed as independently rerun.

No blocking source finding within this bounded change. This does not prove physical Voice PE behavior or overall product acceptance. Default remains off until explicit current household policy/configuration is mounted in both core and worker. Installation must package matching actual core/worker release hashes and update the native speaker's shared agent, preserve installed private receipt behavior, and retain exact prior images/configuration for rollback. No live household-work activation occurred in this review.
