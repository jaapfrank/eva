# Onafhankelijke review — meerdere Telegramaccounts per persoon

Checkpoint: `38f4f6c`
Uitgevoerd: 24 augustus 2026
Reviewer: onafhankelijke implementation review
Uitkomst: **groen**

## Gecontroleerd

- Eén `principal_id` mag aan meerdere unieke Telegram-gebruikers gekoppeld
  zijn.
- Eén Telegram-gebruiker blijft globaal uniek en kan niet aan twee personen
  worden gekoppeld.
- Autorisatie blijft de combinatie van gebruiker én toegelaten chat vereisen;
  een tweede account van dezelfde persoon krijgt geen toegang tot de chat van
  het eerste account.

Zeven gerichte application/loaderchecks, compile en Ruff zijn groen. Dit is
codebewijs; de koppelchallenge, groepsconsent en de echte multi-account
Telegramproef zijn nog niet uitgevoerd.
