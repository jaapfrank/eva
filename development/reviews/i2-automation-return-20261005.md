# I2.2 — automation result delivery review

Reviewer: `/root/agentzero_cm_review`. Base: `b9b6220f` plus the scoped delivery changes.

**GO for this installed callback correction.** The original delivery binding still controls the destination. Automation sessions have a separate persistent identity, cannot become an initiative speaker, and legacy sessions retain speaker delivery. Publication remains offered, with ambiguous sends retained rather than replayed.

Review found that a conversation ID can be reused on different speakers. The event and documented consumer matching now include `satellite_id`; focused tests cover both speakers with the same conversation ID. No remaining source blocker was found within this correction.

71 focused tests passed: 55 HA, 15 core and 1 isolated PostgreSQL test. Actual core/worker and HA source hashes were read back; the installer package verifies against 8 image pins and 1,225 registered source files.

The installed synthetic HA-read task succeeded and returned the correct 210 through the event in 23.741 seconds (receipt at 8.899 seconds). The stored result hash matches the event; the exact saved GET receipt confirms HTTP 200 and the value. Only the temporary synthetic state was removed afterward. No physical device action was requested.

The first arithmetic probe failed because it had no permitted useful tool action, while this household executor requires tool evidence. Its failure is retained. The admission gap for tool-free household work is not solved or counted complete. Nor does this prove the complete I2 observation automation or comparative superiority.

Evidence: `eva2/evidence/product/i2-automation-return-installed-20261005.json`.
