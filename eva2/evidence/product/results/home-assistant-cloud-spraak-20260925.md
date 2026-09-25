# Home Assistant Cloud-spraakassistenten

- **Speech-to-text (STT):** Home Assistant Cloud ontvangt de opgenomen spraak en zet die op Nabu Casa-servers om naar tekst.  
- **Text-to-speech (TTS):** Het antwoord van Home Assistant wordt in de cloud omgezet naar gesproken audio. Deze cloud-STT- en TTS-engines zijn onderdeel van Home Assistant Cloud. ([home-assistant.io](https://www.home-assistant.io/voice_control/voice_remote_cloud_assistant/?utm_source=openai))
- **Lokale verwerking:** Het bepalen wat de opdracht betekent en het uitvoeren ervan—bijvoorbeeld lampen bedienen, deuren vergrendelen of automatiseringen starten—blijft op de eigen Home Assistant-installatie. Het apparaat/microfoon neemt spraak op en speelt het antwoord af; de documentatie beschrijft niet dat het apparaat zelf cloud-STT of cloud-TTS uitvoert. ([home-assistant.io](https://www.home-assistant.io/voice_control/voice_remote_cloud_assistant/?utm_source=openai))
- **Gegevens naar de cloud:** De expliciete documentatieclaim is dat **alleen audio** naar de cloud wordt gestuurd voor verwerking. Home Assistant vermeldt daarnaast dat Cloud deze spraakgegevens niet opslaat of gebruikt om modellen te trainen. De geraadpleegde documentatie geeft geen verdere technische specificatie over bewaartermijnen of logging. ([home-assistant.io](https://www.home-assistant.io/voice_control/voice_remote_cloud_assistant/?utm_source=openai))
- **Lokaal alternatief:** Met lokale STT (Speech-to-Phrase of Whisper) en lokale TTS (Piper) verlaat gesproken spraak het huis niet; ook de omzetting en het antwoord blijven lokaal. ([home-assistant.io](https://www.home-assistant.io/voice_control/voice_remote_local_assistant?utm_source=openai))

**Conclusie (afgeleid advies):** Cloud verwerkt vooral de zware spraakomzetting; Home Assistant blijft lokaal verantwoordelijk voor de betekenis en bediening. Kies lokale verwerking als het verlaten van audio naar externe servers absoluut ongewenst is.

**Officiële bronnen:**  
[Cloud-spraakassistent](https://www.home-assistant.io/voice_control/voice_remote_cloud_assistant/) · [Lokale spraakassistent](https://www.home-assistant.io/voice_control/voice_remote_local_assistant) · [Home Assistant Cloud](https://www.home-assistant.io/cloud)

## Bronnen

- [Set up a voice assistant with Home Assistant Cloud - Home Assistant](https://www.home-assistant.io/voice_control/voice_remote_cloud_assistant/?utm_source=openai)
- [Set up a fully local voice assistant - Home Assistant](https://www.home-assistant.io/voice_control/voice_remote_local_assistant?utm_source=openai)