# Native document relocation — independent source review

23 September 2026. Reviewer: `document_private_identity`; implementation: `document_relocation`.

GO for the bounded original-v2 to relocated-v3 source change. The reviewer independently inspected the stable source; did not rerun tests. The builder ran 46 cases successfully; the coordinator verified the retained TAP log and its hash. Exact source hashes are in `../evidence/product/native-document-relocation-20260923.json`.

The existing native journal parser remains authoritative. Original prefix and receipts are preserved, physical identities are translated only under host authority, and uncertain P-only publication with an existing effect is refused. Missing authority cannot initialize a journal. Observing does not publish or repair.

No live GO: packaged gateway/catalog identity, private owner authorization, verified export bytes and exact package-bound restore activation are not proved. `logical_catalog_identity` is metadata, not independent proof of catalog equality. V1 and repeated relocation are intentionally refused.
