# Gerichte RPC-eigenaarcorrectie ter onafhankelijke review

Niet gedeployed. De patch verandert alleen `_execute_rpc_tools_call` in de exact gepinde gateway-`main.py`: behoud de geverifieerde email bij admin/null-teamscope; behoud de bestaande non-admin/null → public-only-normalisatie. Geen wijzigingen aan visibility, teamtoewijzing, RBAC, tokens, JWT-verificatie, catalogus of native provider.

De bestaande servicepolicy blijft beslissen. Zij geeft een DB-bevestigde admin toegang tot diens eigen private tools, niet die van een andere eigenaar. Het voorheen wissen van de email maakte die afweging onmogelijk. De cancellation-owner/-teamtoewijzing wordt vóór deze normalisatie bepaald en blijft ongewijzigd.

`preserve-rpc-owner-context.pin.json` bindt oorspronkelijke en kandidaat-mainbron, image en de werkelijk gemounte toolservicepolicy. `preserve-rpc-owner-context.patch` is de volledige wijziging. De daadwerkelijke handlerfunctie wordt voor de geïsoleerde proef uit beide gebonden bestanden geladen; er is geen herimplementatie van haar normalisatie.

De proef gebruikt de echte `get_rpc_filter_context`, echte gatewayhandler en daadwerkelijke `_check_tool_access` met een tijdelijke SQLite-database en echte admininitialisatie. Vijftien gevallen per versie slagen. Alleen admin/eigen-private verandert van weigeren naar toestaan; andere private tools, anonymous/missing/ongeldige email, public-only admin, non-admin/null, verkeerd team, stale session-admin en vervalste parameters blijven geweigerd. Public en toegestaan team blijven werken. Een ongeverifieerde bearerheader zonder vertrouwde requeststate verleent geen private rechten. Cancellation-binding en unregister zijn per geval gecontroleerd.

De requeststate voor reeds geverifieerde tokens is een fixture; deze proef voert geen nieuwe HTTP/JWT-signatuurverificatietest uit. Zij test wel de echte autorisatiecontextketen, inclusief een verse DB-check voor de ingetrokken session-admin. De upstream-aanroep is vervangen door een teller na de echte toegangsbeslissing: nul netwerk-/documenteffecten. De container heeft netwerk `none`, read-only bronmounts en uitsluitend tijdelijke fixturedata.

Een mogelijke installatie moet de huidige bootstrapguard én endpointpolicy behouden, de exacte oude bronhash verifiëren en uitsluitend de gepinde mainbron toevoegen. Die installatie is hier niet geautoriseerd of uitgevoerd. Er volgt geen automatische herhaling van de mislukte documentproef en geen herstel/reset van de bestaande doelstaat. De gewone proef en eventuele hervatting blijven aparte open punten.
