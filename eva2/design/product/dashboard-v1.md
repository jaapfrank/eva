# Eva Dashboard v1

Eva krijgt een beheerd webdashboard. Het is een **control plane**, geen tweede
agent: alle mutaties gaan via Eva's bestaande policy, append-only eventledger,
versies en waar nodig providerreadback. De interface bevat nooit ruwe secrets.

## V1-schermen

| Scherm | Wat de beheerder kan zien of doen | Veiligheidsgrens |
| --- | --- | --- |
| Overzicht | Actieve A/B-image, health, laatste gesprekken/acties, open blokkades, receipts en release-gates. | Alleen lezen; een groene UI-status is nooit een vervanging voor een receipt. |
| Identiteit | Naam, korte omschrijving, karakter- en antwoordstijl als nieuwe, versieerbare IdentityPack-draft bekijken, wijzigen, diffen en publiceren. | Publicatie maakt een nieuwe pack-hash; oude packs en episodes blijven herleidbaar. Geen vrije systeemprompt-editor. |
| Personen en geheugen | Geverifieerde personen, kanaalbindings, persoonlijke/gedeelde scopes, consent en recall-ledger bekijken; consent intrekken. | Geen privéherinnering van de ene persoon in het scherm van een andere; scope en toestemming worden server-side afgedwongen. |
| Telegram | B-tokenstatus als referentie, geverifieerde DM/groepsroutes, koppelchallenges, deliveryreceipts en teststatus beheren. | Tokens zijn alleen file-secret-referenties; koppelen vereist de wederzijdse private challenge. |
| Skills en capabilities | Gepinde catalogus, uitgever, versie, digest, permissies, reviewstatus en receipts bekijken; alleen een quarantaine-import voorstellen. | Geen automatische installatie of scripts; code blijft in sidecar/quarantaine en Eva houdt policy/ledger/eindantwoord. |
| Instellingen en connectors | Niet-geheime instellingen, configuratie-diffs, connectorstatus en testresultaat bekijken of als draft wijzigen. | Secretwaarden zijn write-only file references; muterende connectoracties volgen prepare → execute → readback. |

## Kleine opleverslices

1. **D01 — alleen-lezen overzicht:** API + eenvoudige webpagina met B-image,
   gates, receipts en open blokkades; geen mutaties.
2. **D02 — identiteit als versie:** draft/diff/publish voor IdentityPack met
   hash, audit-event en rollback naar een eerder pack.
3. **D03 — personen en Telegram:** pas na de connectorneutrale identiteitslaag;
   bindings, groepsroutes, consent en koppelchallenge.
4. **D04 — skillcatalogus:** gepinde, read-only catalogus en
   quarantine-review; nog geen installatie.
5. **D05 — settings en gecontroleerde acties:** secret-referenties,
   connector-drafts en expliciete approval/readback, alleen wanneer de
   file-secret- en managed-B-grens groen is.

Elke slice krijgt gerichte tests, een onafhankelijke review en een Git-checkpoint.
De dashboardroute draait uitsluitend in B totdat haar autorisatie- en
privacyproeven groen zijn.
