# LiveKit-spraaksessie voor Eva

Deze optionele dienst verzorgt uitsluitend audio en beurtwisseling. Iedere inhoudelijke beurt gaat naar dezelfde geauthenticeerde Eva-kern; geheugen, tools en eigenaarmandaat blijven daar. Home Assistant Cloud verzorgt de bestaande Nederlandse STT/TTS. Er is geen tweede taalmodel.

## Bouw en herkomst

Voer vanuit eva2 `python deploy/voice-session/fetch_vendor.py` uit. Het script controleert de gepinde LiveKit-serverbron en browserclient met vastgelegde checksums. De serverbron blijft v1.13.6; livekit-source/go.mod en go.sum pinnen de beveiligingsupdates x/crypto 0.55.0, x/mod 0.40.0 en gRPC 1.83.2 plus hun opgeloste afhankelijkheden. De server wordt met Go 1.26.8 gebouwd. De oorspronkelijke releasebinary had vijf HIGH/CRITICAL-meldingen en wordt niet gebruikt voor de blijvende dienst. Bouw vervolgens met `docker build -f deploy/voice-session/Dockerfile .` en `docker build -f deploy/voice-session/Dockerfile.livekit .`. De Pythondependencies staan exact vast in requirements.lock. Bewaar de image-ID, broncommit en scanuitkomst bij de oplevering. Een succesvolle bouw is geen schone kwetsbaarheidsscan.

LiveKit Agents, de server en de browserclient worden hergebruikt onder hun upstreamlicenties; Silero VAD wordt via de officiële LiveKit-plugin geladen. fetch_vendor.py bevat de exacte release-URL’s en integriteitswaarden. Distribueer upstreamlicenties en notices mee wanneer deze lokale installatie als pakket wordt verspreid.

## Configuratie

compose.yaml verwacht EVA_LIVEKIT_IMAGE en EVA_VOICE_MEDIA_IMAGE als onveranderlijke lokale image-ID, EVA_VOICE_STATE als private permanente map, en EVA_VOICE_CORE_SLOT_DIRECTORY als de actieve beheerde kernslotmap. Geheimen horen niet in git of commandoregeluitvoer. De permanente map bevat media-config.json, livekit-secret, livekit.yaml en policy/current.json. Gebruik eigenaar UID 65532, maprechten 0700 en bestandsrechten 0600.

media-config.json bevat core_url, ha_url, pipeline_id, language, stt_engine, tts_engine, tts_language, livekit_url, public_livekit_url, livekit_api_key, origin, client_js, owner_token_file, ha_token_file, livekit_secret_file, authority_file, authority_sha256, identity_file en identity_sha256. Gebruik de exacte veldnamen uit server.py bij het genereren; configuratie en SHA256-waarden moeten overeenkomen met de daadwerkelijk gemounte bestanden. Het beleid is dezelfde bestaande DashboardVoice-grant die de kern controleert. Activeer daarvoor de optionele dashboard-voice.compose.yml-overlay via de beheerde cutoverprocedure. Alleen de eerste overgang van ongeconfigureerd naar een geldige actuele eigenaargrant gebruikt expliciet --activate-dashboard-voice bij zowel preflight als execute. Bestaande grants, intrekkingen en householdkanalen blijven ongewijzigd. De operator controleert de nieuwe geauthenticeerde voice-readiness na activering maar vóór het heropenen van de gebruikersingang en rolt bij falen met gegevensbehoud terug.

De mediaserver luistert uitsluitend op 127.0.0.1:44716. De lokale LiveKit-server gebruikt 7880 voor WebSocket, 7881 voor ICE/TCP en 7882 voor ICE/UDP. Laptoptoegang gebruikt http://localhost:44716, zodat de browser microfoontoegang op localhost toestaat. Telefoontoegang vereist een afzonderlijk geverifieerde HTTPS/WebRTC-route en is hiermee nog niet opgeleverd.

## Gebruik en beheer

Open de lokale pagina, meld aan als eva-owner met het bestaande eigenaar-dashboardtoken en kies Begin gesprek. Sta microfoontoegang toe. Eindig met Einde gesprek. Deze knop sluit de mediaruimte; reeds toegelaten Eva-taken kunnen doorlopen. Vraag Eva expliciet om een taak te stoppen als ook het werk moet stoppen.

Bij een kernslotwissel: sluit de audiosessie, pas core_url en EVA_VOICE_CORE_SLOT_DIRECTORY aan, controleer de identiteit- en beleidshashes en herstart de mediadienst. Laat geen afzonderlijke proefkern met schrijfrechten aan dezelfde databases draaien. De kern-readinesscontrole moet slagen voordat een sessie opent.

Intrekken van policy/current.json sluit de audiosessie via de lopende autorisatiecontrole; de kern controleert de grant opnieuw bij het afleveren van resultaten. Verwijder de beleidsmap zelf niet zolang de core-overlay haar mount verwacht. Identiteit en beleid gebruiken directorymounts en volgen daardoor atomische vervanging. De twee bestaande secret-bestanden zijn bewust afzonderlijk gemount: na atomische tokenrotatie moet de mediadienst worden gerecreëerd zodat zij de nieuwe bestanden leest. Na herstart is opnieuw aanmelden en Begin gesprek nodig. Zonder eigenaar wordt een lege ruimte na 30 seconden opgeruimd; een sessie verloopt uiterlijk na 30 minuten.

## Acceptatie

Technische proeven gebruiken synthetische Nederlandse audio zonder ruwe audio-opslag. Werkelijke microfoon, luidspreker/echo, huisgebruik en eigenaaracceptatie moeten afzonderlijk worden vastgelegd. Een transcript bewijst geen hoorbare kwaliteit; gemeten audio-energie en de gebruikersproef horen bij de controle.
