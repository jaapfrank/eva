# Schone Eva2-runtime als één proefstack

Deze proef draaide de drie schone kandidaten samen:

- de kleinere Eva2-kern;
- de API-only Hindsight-image;
- PostgreSQL 18.6 met pgvector 0.8.6, eenmaal voor Eva en eenmaal voor Hindsight.

Beide databases begonnen leeg. Eva installeerde haar volledige schema, werd
gereed, voerde een echte Codex-beurt uit en liet Hindsight het persoonsgebonden
testwoord terugvinden. Daarna zijn beide databases, Hindsight en Eva gestopt en
opnieuw gestart. De geheugenopvraag én de tweede echte modelbeurt bleven groen.

Er waren geen hostpoorten. Het tijdelijke databasewachtwoordbestand is na de
proef verwijderd. De containers en synthetische volumes zijn gestopt maar voor
controle behouden.

Dit is nog niet managed B. Het huidige verzegelde B-manifest gebruikt één
operationele database en Hindsight met ingebouwde opslag. De veilige
productvariant moet daarom eerst een negende, aparte geheugen-database krijgen.
