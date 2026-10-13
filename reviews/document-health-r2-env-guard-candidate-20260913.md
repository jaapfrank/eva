# Healthguard: volgorde van omgevingsvariabelen

De eerste installatiepoging bracht beide nieuwe documenthealthchecks op healthy, maar de runtimeguard weigerde alleen gewijzigde Env-lijstvolgorde. De terugval zette de oude images terug; dezelfde guardfout leverde ook daar verified:false. Dat oorspronkelijke bewijs is niet overschreven. Een afzonderlijke read-only vergelijking op unieke variabelenaam en exacte waarde verifieerde het werkelijke herstel, met alle overige guards ongewijzigd.

R2 hergebruikt de key/valuecanonicalisatie van de eerdere R6coreguard en weigert aanvullend dubbele of ongeldige namen vóór dictionaryvorming. Zowel installatie als rollback accepteren alleen volgordeverandering; gewijzigde waarden en gelijke/conflicterende duplicaten falen. Twintig checks slagen normaal en met Python-O. Dezelfde twee images/probes blijven behouden.

De nieuwe private stage en exacte seal staan in de companionJSON. Nieuwe containerbaseline is vastgelegd; oude GO/attemptmarkers zijn niet gekopieerd. Freshguard is groen, uitvoering zonder nieuwe GO blokkeert vóór een attempt. Geen nieuwe installatie, coreherstart, model- of effectproef uitgevoerd.
