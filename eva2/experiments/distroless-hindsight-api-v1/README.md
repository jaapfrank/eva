# Hindsight API met een kleine, schone database

Deze proef haalt de ongebruikte Node.js-control-plane uit Hindsight en draait
alleen de API die Eva nodig heeft. De API gebruikt een aparte PostgreSQL 18.6-
database met pgvector 0.8.6.

De Hindsight-API is opgebouwd uit de vastgezette upstream-image. Drie
Pythonpakketten met bekende hoge kwetsbaarheden zijn vanuit vooraf gedownloade,
hashgecontroleerde wheels bijgewerkt. De uiteindelijke API-image en de
PostgreSQL/pgvector-image scannen beide op 0 kritisch en 0 hoog.

De praktijkproef schreef via Eva's bestaande `HindsightMemoryEngine` een
synthetische, persoonsgebonden herinnering. Eva vond haar terug vóór en na een
herstart van zowel Hindsight als PostgreSQL.

Een tweede pgvector-build gebruikte daarna een exact vastgezette lokale
builderimage en een gehashte bronarchive. Docker draaide met `--network none`.
De extensionbestanden waren bytegelijk aan de eerste build en de nieuwe image
werkte opnieuw met PostgreSQL 18.6 en pgvector 0.8.6.

Dit is nog een kandidaat en geen release. De lokale builderimage heeft nog
geen off-host back-up en kan nog niet vanuit een complete set bewaarde losse
APK-bestanden worden gereconstrueerd. De live Eva1- en `/eva2`-routes zijn niet
gewijzigd.
