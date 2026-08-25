# Harness-subagents — kleine echte hergebruikproef

Deze map bewijst één ding: Eva hoeft geen eigen subagent-runner, contextisolatie,
request-/tokenbudget, time-out of maximaal-aantal-calls te bouwen. De exact
vastgezette Pydantic AI `2.34.0` en Harness `0.24.0` voeren de child-run uit.

Eva voegt in deze proef slechts een klein grantprofiel toe. Dat profiel zet
automatische agentbestanden, overerving van parenttools en recursieve delegatie
uit. De providerloze tests bewijzen echte delegatie, een aparte childcontext en
stoppen na het toegestane aantal calls.

Dit is nog geen productpromotie. PostgreSQL-handoff, lease/crashherstel,
Kanbanprojectie, modelkosten en een echte provider-run blijven Eva-eigen
integratiewerk. De proef verandert B niet en gebruikt geen netwerk, secret of
productdatabase.
