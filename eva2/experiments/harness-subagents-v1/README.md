# Harness-subagents — kleine echte hergebruikproef

Deze map bewijst één ding: Eva hoeft geen eigen subagent-runner, contextisolatie,
request-/tokenbudget, time-out of maximaal-aantal-calls te bouwen. De exact
vastgezette Pydantic AI `2.34.0` en Harness `0.24.0` voeren de child-run uit.

Eva voegt in deze proef slechts een klein grantprofiel toe. Zij bouwt de child
zelf uit naam, model en instructie, zodat een caller geen vooraf verborgen
tools, capabilities of recursieve subagents kan meegeven. Automatische
agentbestanden, parenttools en shared capabilities staan uit. De providerloze
tests bewijzen echte delegatie, een aparte childcontext, nul childtools, stoppen
na het callbudget, vooraf weigeren van een te grote input en een coöperatieve
async time-out. NaN, oneindige waarden,
booleans, dubbele namen, te veel workers en een te ruim totaalbudget worden
geweigerd.

Dit is nog geen productpromotie. PostgreSQL-handoff, lease/crashherstel,
Kanbanprojectie, modelkosten en een echte provider-run blijven Eva-eigen
integratiewerk. Een per-child `UsageLimits`-budget wordt door Harness apart
geteld en komt niet vanzelf in het parenttotaal; Eva moet dat vóór promotie in
haar eigen receipt opnemen. De Harness-time-out kan bovendien geen blokkerende
sync-code of extern effect terugdraaien. Daarom blijft deze eerste child
toolvrij en effectvrij; echte processandbox, harde kill, foutredactie en
crashherstel horen bij de duurzame vervolgslice. De proef verandert B niet en
gebruikt geen netwerk, secret of productdatabase.
