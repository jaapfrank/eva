# Independent review: preservation of Eva-authored program

Date: 2026-10-02  
Decision: GO for assisted preservation and cross-conversation retrieval.

The preservation driver embeds exactly the already reviewed Eva-authored
`agenda_changes.py`. Its source SHA-256 matches the repository artifact,
the original frozen source, the admitted save, the designated reference and
the later resolved content:

`23f6cf52981029b58246580f7028ac81e0d50ad0f22950f16f4bf2938f1601d8`

The inspected driver uses the installed provider bundle's existing
`save_workspace_document` and `designate_reusable_procedure` actions.
The recorded save is verified. The designation binds the content and save
receipt. A different conversation ID and message ID then perform an actual
`list_reusable_procedures` query, require the designated document among its
candidates, and call `resolve_reusable_procedure`. The driver checks exact
source-string equality before recording success. Terminal exit code is zero.

The recorded use description correctly identifies this artifact as a CLI with
`BEFORE AFTER OUTPUT` arguments. It directs later use through source resolution,
`write_programming_file` and `start_programming_process`; it does not claim
compatibility with the JSON-stdin-only `run_reusable_procedure` interface.

The first execution log shows failure while constructing the conversation turn,
before the subsequent journal admission or save. The canonical UTC correction
therefore did not repeat an admitted effect.

Evidence bindings:

- Save receipt SHA-256:
  `cf0c20a95a6e10f929c935bb04f03e1e9b5ee924ce18af32ba0083f8159e7efc`
- Preservation driver SHA-256:
  `ce32436210660f63fc3548cee5d56a8844767c590ad9fc1d7738d8e877c1a96d`
- Successful execution log SHA-256:
  `786be421b72fe7b8d7762cffb409f65ff0319de848f4e2de1a93dcdc4ca6cc54`
- Terminal record SHA-256:
  `7569930b580e9212d565df0f57729b837649815d6307aa34c5470d8ab1bb24e4`

This is coordinator-assisted adoption using explicitly labelled administrative
turns. It demonstrates exact preservation and retrieval in another conversation.
It does not demonstrate Eva independently choosing to save, discovering the
program in an ordinary new request, executing it on another task, or installing
an improvement to her own runtime. The prior independent functional check remains
separate evidence. No model request, program rerun, source fix, test run or runtime
mutation was performed by this reviewer.
