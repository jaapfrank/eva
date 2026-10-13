# Independent review: durable application process recovery

Decision: GO for the bounded test-only change. Coordinator reviewed the authored test and executed it independently on the existing packaged core image `2320da954ea6686e320e53b8364e811808b93c400d664fb416dacb1ac04ffa2d` with a unique disposable PostgreSQL16.13 database. One test passed in18.72seconds. No product code or production configuration changed; no installation is required for this test.

Source SHA256: `fc773c1afd8fcc5b0cc22090cc2921c4f33505cf393b30dc85c480fa2148f647`. Execution log SHA256: `dd6a3a40494c193b1cda57bb02cfb5d876d178efea45eb6135ce2c1f1a1eb8fa`.

The test starts separate application-service and worker processes against real stores. It kills the claim process after a durable return claim and before acknowledgement. A fresh process checks the claim is not offered again while the original session is still valid; a later process separately checks expiry. Result hash, stored memory source bytes/retained receipt, owner workboard/list and foreign-owner rejection are asserted. The controlled provider counter remains exactly one after the second worker run.

This closes a missing process-loss persistence boundary, not the full combined installation recovery requirement. The memory retention provider is simulated; semantic recall/correction is not exercised. Historical fixtures seed authority and pin PostgreSQL16.13/schema4e0007; this is not production PostgreSQL18.6 composition proof. Application services run in separate OS processes, but the HTTP core, whole production worker container, browser/gateway address changes, microphone, host reboot and physical acceptance are outside this test. Existing browser evidence is preserved, not rerun or promoted to combined proof.

The exact disposable database was removed after successful teardown; the shared test PostgreSQL service and production services were retained. No paid model call or external message was sent.
