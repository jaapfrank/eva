"""Application-layer domain helpers, presets, and port for the 1-click Eva appliance wizard."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterator, Literal, Mapping, Protocol

from eva.application.soul_markdown import SoulMarkdownError, soul_markdown_to_draft
from eva.contracts.canonical_json import canonical_json_bytes
from eva.contracts.identity import IdentityPack
from eva.contracts.runtime_budget import RuntimeResourceBudget


HardwareTier = Literal["compact", "standard", "performance"]
ProactivityLevel = Literal["quiet", "balanced", "proactive"]


@dataclass(frozen=True, slots=True)
class CharacterPreset:
    preset_id: str
    label: str
    tagline: str
    temperament: tuple[str, ...]
    humour: str
    voice_qualities: tuple[str, ...]
    proactivity: ProactivityLevel
    description_template: str


CHARACTER_PRESETS: tuple[CharacterPreset, ...] = (
    CharacterPreset(
        preset_id="warm_huisgenoot",
        label="Warm & betrokken huisgenoot",
        tagline="Vriendelijk, attent en denkt rustig met het huishouden mee.",
        temperament=("warm", "attent", "betrokken", "rustig"),
        humour="lichte, warme huisgenoothumor zonder sarcasme",
        voice_qualities=("natuurlijk", "vriendelijk", "helder", "warm"),
        proactivity="balanced",
        description_template="Je bent een warme, nuchtere en betrokken digitale huisgenoot.",
    ),
    CharacterPreset(
        preset_id="nuchter_direct",
        label="Nuchter & to-the-point",
        tagline="Kort, praktisch en concreet zonder omhaal.",
        temperament=("nuchter", "helder", "praktisch", "betrouwbaar"),
        humour="droge, subtiele humor wanneer dat echt past",
        voice_qualities=("beknopt", "concreet", "rustig", "direct"),
        proactivity="quiet",
        description_template="Je bent een nuchtere, praktische en betrouwbare digitale huisgenoot.",
    ),
    CharacterPreset(
        preset_id="creatief_nieuwsgierig",
        label="Creatief & nieuwsgierig",
        tagline="Denkt vooruit, komt met frisse ideeën en blijft nieuwsgierig.",
        temperament=("nieuwsgierig", "creatief", "energiek", "open"),
        humour="speelse, gevatte opmerkingen met gevoel voor timing",
        voice_qualities=("levendig", "inspirerend", "vlot", "nieuwsgierig"),
        proactivity="proactive",
        description_template="Je bent een creatieve, nieuwsgierige en ondernemende digitale huisgenoot.",
    ),
    CharacterPreset(
        preset_id="kalm_zorgvuldig",
        label="Rustig & zorgvuldig",
        tagline="Brengt rust en overzicht, en controleert liever twee keer dan één keer fout.",
        temperament=("kalm", "zorgvuldig", "geduldig", "overzichtelijk"),
        humour="milde, vriendelijke glimlach zonder drukte",
        voice_qualities=("rustig", "gestructureerd", "zacht", "zorgvuldig"),
        proactivity="quiet",
        description_template="Je bent een kalme, zorgvuldige en overzichtelijke digitale huisgenoot.",
    ),
)

_PRESET_BY_ID: dict[str, CharacterPreset] = {item.preset_id: item for item in CHARACTER_PRESETS}


@dataclass(frozen=True, slots=True)
class ModuleCatalogItem:
    module_id: str
    title: str
    summary: str
    ram_hint: str
    has_connection_test: bool


MODULE_CATALOG: tuple[ModuleCatalogItem, ...] = (
    ModuleCatalogItem(
        module_id="telegram",
        title="Telegram",
        summary="Privé en in je gezin met Eva chatten via je eigen Telegram-bot en een veilige eenmalige koppel-link.",
        ram_hint="~15 MB extra",
        has_connection_test=True,
    ),
    ModuleCatalogItem(
        module_id="smart_home",
        title="Slim Huis & Sensoren",
        summary="Verbind je lokale smarthome-omgeving (zoals lampen, klimaat en sensoren) met keuze tussen alleen meekijken of ook bedienen.",
        ram_hint="~10 MB extra",
        has_connection_test=True,
    ),
    ModuleCatalogItem(
        module_id="voice",
        title="Spraak & Huiskamerspeakers",
        summary="Tweerichtings spraak via telefoon, browser en lokale kamerspeakers.",
        ram_hint="~80 MB extra",
        has_connection_test=False,
    ),
    ModuleCatalogItem(
        module_id="holostage",
        title="HoloStage & Schermen",
        summary="Eva tonen op een tablet, telefoon of tv-scherm in huis op je lokale netwerk.",
        ram_hint="~30 MB extra",
        has_connection_test=False,
    ),
    ModuleCatalogItem(
        module_id="digital_work",
        title="Zelfstandig Werk & Veilige Browser",
        summary="Laat Eva zelfstandig webtaken, onderzoek en documentwerk uitvoeren in een geïsoleerde werkomgeving.",
        ram_hint="~400 MB wanneer actief",
        has_connection_test=False,
    ),
    ModuleCatalogItem(
        module_id="mail",
        title="E-mail & Postvak",
        summary="Geef Eva een eigen afgebakend postvak om updates te ontvangen en berichten te versturen.",
        ram_hint="~5 MB extra",
        has_connection_test=True,
    ),
)


@dataclass(frozen=True, slots=True)
class HardwareTierProfile:
    tier: HardwareTier
    label: str
    summary: str
    postgres_shared_buffers_mb: int
    hindsight_threads: int
    recall_context_tokens: int
    retrieval_nodes: int
    max_input_tokens: int
    max_output_tokens: int
    profile_context_tokens: int
    memory_records_per_person: int
    memory_bytes_per_person: int
    memory_records_per_installation: int
    memory_bytes_per_installation: int
    background_reflections_per_person_day: int
    background_reflections_per_installation_day: int


HARDWARE_TIERS: dict[HardwareTier, HardwareTierProfile] = {
    "compact": HardwareTierProfile(
        tier="compact",
        label="Compact (4–8 GB RAM / laptop of Pi)",
        summary="Zuinig ingesteld voor systemen tot 8 GB RAM zodat Windows of het host-OS vlot blijft.",
        postgres_shared_buffers_mb=128,
        hindsight_threads=2,
        recall_context_tokens=2048,
        retrieval_nodes=40,
        max_input_tokens=52000,
        max_output_tokens=1200,
        profile_context_tokens=512,
        memory_records_per_person=3000,
        memory_bytes_per_person=31_457_280,
        memory_records_per_installation=30000,
        memory_bytes_per_installation=314_572_800,
        background_reflections_per_person_day=6,
        background_reflections_per_installation_day=20,
    ),
    "standard": HardwareTierProfile(
        tier="standard",
        label="Standaard Thuisserver (16 GB RAM, bijv. HP Pro Mini)",
        summary="Gebalanceerd voor 24/7 thuisgebruik met ruime geheugencontext en snelle respons.",
        postgres_shared_buffers_mb=256,
        hindsight_threads=4,
        recall_context_tokens=3000,
        retrieval_nodes=60,
        max_input_tokens=60000,
        max_output_tokens=1200,
        profile_context_tokens=800,
        memory_records_per_person=5000,
        memory_bytes_per_person=52_428_800,
        memory_records_per_installation=50000,
        memory_bytes_per_installation=524_288_000,
        background_reflections_per_person_day=10,
        background_reflections_per_installation_day=40,
    ),
    "performance": HardwareTierProfile(
        tier="performance",
        label="Krachtig (32+ GB RAM)",
        summary="Maximale geheugencapaciteit, snellere parallelle verwerking en ruime reflectielimieten.",
        postgres_shared_buffers_mb=512,
        hindsight_threads=6,
        recall_context_tokens=4000,
        retrieval_nodes=80,
        max_input_tokens=65536,
        max_output_tokens=1200,
        profile_context_tokens=1000,
        memory_records_per_person=10000,
        memory_bytes_per_person=104_857_600,
        memory_records_per_installation=100000,
        memory_bytes_per_installation=1_073_741_824,
        background_reflections_per_person_day=16,
        background_reflections_per_installation_day=80,
    ),
}


def recommend_hardware_tier(ram_gib: float, cpu_count: int) -> HardwareTier:
    """Pick a safe default profile for the detected RAM and CPU count."""
    if ram_gib >= 28.0 and cpu_count >= 8:
        return "performance"
    if ram_gib >= 12.0 and cpu_count >= 4:
        return "standard"
    return "compact"


def build_budget_for_tier(
    tier: HardwareTier,
    *,
    tenant_id: str,
    installation_id: str,
    monthly_spend_eur: float = 0.0,
) -> RuntimeResourceBudget:
    """Translate a friendly hardware tier + monthly Euro cap into a closed RuntimeResourceBudget."""
    profile = HARDWARE_TIERS[tier]
    spend_cents = max(0, min(1_000_000, round(float(monthly_spend_eur) * 100)))
    budget_fields: dict[str, object] = {
        "background_reflections_per_installation_day": profile.background_reflections_per_installation_day,
        "background_reflections_per_person_day": profile.background_reflections_per_person_day,
        "contract": "EvaInstallerBudget/v2",
        "external_monthly_spend_cap_eur_cents": spend_cents,
        "max_input_tokens": profile.max_input_tokens,
        "max_output_tokens": profile.max_output_tokens,
        "memory_bytes_per_group": profile.memory_bytes_per_person,
        "memory_bytes_per_installation": profile.memory_bytes_per_installation,
        "memory_bytes_per_person": profile.memory_bytes_per_person,
        "memory_bytes_per_project": profile.memory_bytes_per_person,
        "memory_records_per_group": profile.memory_records_per_person,
        "memory_records_per_installation": profile.memory_records_per_installation,
        "memory_records_per_person": profile.memory_records_per_person,
        "memory_records_per_project": profile.memory_records_per_person,
        "profile_context_tokens": profile.profile_context_tokens,
        "recall_context_tokens": profile.recall_context_tokens,
        "retention_days": 365,
        "retrieval_nodes": profile.retrieval_nodes,
        "unknown_cost_policy": "block_autonomous_spend",
    }
    source_sha = hashlib.sha256(canonical_json_bytes(budget_fields)).hexdigest()
    return RuntimeResourceBudget(
        tenant_id=tenant_id,
        installation_id=installation_id,
        source_budget_sha256=source_sha,
        recall_context_tokens=profile.recall_context_tokens,
        retrieval_nodes=profile.retrieval_nodes,
        max_input_tokens=profile.max_input_tokens,
        max_output_tokens=profile.max_output_tokens,
        profile_context_tokens=profile.profile_context_tokens,
        memory_records_per_person=profile.memory_records_per_person,
        memory_bytes_per_person=profile.memory_bytes_per_person,
        memory_records_per_group=profile.memory_records_per_person,
        memory_bytes_per_group=profile.memory_bytes_per_person,
        memory_records_per_project=profile.memory_records_per_person,
        memory_bytes_per_project=profile.memory_bytes_per_person,
        memory_records_per_installation=profile.memory_records_per_installation,
        memory_bytes_per_installation=profile.memory_bytes_per_installation,
        retention_days=365,
        background_reflections_per_person_day=profile.background_reflections_per_person_day,
        background_reflections_per_installation_day=profile.background_reflections_per_installation_day,
        external_monthly_spend_cap_eur_cents=spend_cents,
    )


def get_character_preset(preset_id: str) -> CharacterPreset:
    preset = _PRESET_BY_ID.get(preset_id)
    if preset is None:
        raise ValueError("onbekend karakterprofiel")
    return preset


def _clean_comma_items(items: list[str] | tuple[str, ...], *, label: str) -> list[str]:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        raise SoulMarkdownError(f"{label} mag niet leeg zijn")
    for item in cleaned:
        if "\n" in item or "\r" in item or item.startswith("#"):
            raise SoulMarkdownError(f"{label} bevat ongeldige tekens")
    return cleaned


def build_character_soul_markdown(
    *,
    display_name: str,
    pronouns: list[str] | tuple[str, ...] = ("zij", "haar"),
    language: str = "nl-NL",
    humour: str,
    temperament: list[str] | tuple[str, ...],
    voice_qualities: list[str] | tuple[str, ...],
    base_pack: IdentityPack | None = None,
) -> str:
    """Format clean character settings into validated SOUL.md markdown."""
    name = str(display_name).strip()
    if not name or "\n" in name or "\r" in name or name.startswith("#"):
        raise SoulMarkdownError("Naam is ongeldig")
    lang = str(language).strip()
    if not lang or "\n" in lang or "\r" in lang:
        raise SoulMarkdownError("Taal is ongeldig")
    humour_text = str(humour).strip()
    if not humour_text or "\n" in humour_text or "\r" in humour_text or humour_text.startswith("#"):
        raise SoulMarkdownError("Humor-omschrijving is ongeldig")
    pronoun_list = _clean_comma_items(pronouns, label="Voornaamwoorden")
    temperament_list = _clean_comma_items(temperament, label="Temperament")
    voice_list = _clean_comma_items(voice_qualities, label="Stem")

    markdown = "\n".join(
        (
            f"# {name}",
            "",
            "## Naam",
            name,
            "",
            "## Voornaamwoorden",
            ", ".join(pronoun_list),
            "",
            "## Taal",
            lang,
            "",
            "## Humor",
            humour_text,
            "",
            "## Temperament",
            ", ".join(temperament_list),
            "",
            "## Stem",
            ", ".join(voice_list),
            "",
        )
    )
    if base_pack is not None:
        # Validate shape via existing contract; ignore unchanged-diff check here.
        try:
            soul_markdown_to_draft(markdown, draft_id="character-validate", base=base_pack)
        except SoulMarkdownError as error:
            if "verandert de huidige identiteit niet" not in str(error):
                raise
    return markdown


def render_character_preview(
    *,
    display_name: str,
    owner_name: str = "Jaap",
    preset_id: str = "warm_huisgenoot",
    proactivity: ProactivityLevel = "balanced",
    temperament: list[str] | tuple[str, ...] = (),
    humour: str = "",
) -> dict[str, str]:
    """Generate a concrete Dutch sample dialogue reflecting the chosen personality."""
    owner = owner_name.strip() or "jij"
    eva = display_name.strip() or "Eva"
    preset = _PRESET_BY_ID.get(preset_id, CHARACTER_PRESETS[0])
    temp_lower = {item.strip().casefold() for item in temperament}

    if preset_id == "nuchter_direct" or "direct" in temp_lower or "nuchter" in temp_lower:
        reply = (
            f"Hoi {owner}. Ik zet drie snelle opties voor vanavond op een rij: "
            "pasta pesto (15 min), stamppot of iets laten bezorgen. Zeg maar welke het wordt, dan houd ik het daarop."
        )
    elif preset_id == "creatief_nieuwsgierig" or "creatief" in temp_lower:
        reply = (
            f"Ha {owner}! Drukke dag? Ik zag dat je gisteren zin had in iets Aziatisch — "
            "zullen we voor een snelle teriyaki-bowl gaan, of heb je vandaag meer trek in comfortfood?"
        )
    elif preset_id == "kalm_zorgvuldig" or "kalm" in temp_lower:
        reply = (
            f"Dag {owner}, rustig aan. Voor vanavond heb ik twee overzichtelijke opties klaarstaan die weinig tijd kosten: "
            "een snelle roerbak of soep met brood. Laat maar weten waar je hoofd naar staat."
        )
    else:
        reply = (
            f"Hé {owner}, klinkt als een volle dag! Zullen we vanavond iets makkelijks doen zoals een snelle pasta, "
            "of wil je dat ik vast check wat er nog op de boodschappenlijst staat?"
        )

    if proactivity == "quiet":
        initiative_note = "Stille houding: wacht rustig tot je iets vraagt; onderbreekt niet ongevraagd."
    elif proactivity == "proactive":
        initiative_note = "Actieve houding: seint uit zichzelf als een deadline, apparaat of gezinsafspraak aandacht vraagt."
    else:
        initiative_note = "Gebalanceerde houding: denkt mee bij duidelijke aanleiding en blijft verder rustig op de achtergrond."

    return {
        "user_prompt": f"Hoi {eva}, drukke dag vandaag — kun je even meekijken met het avondeten?",
        "assistant_reply": reply,
        "initiative_note": initiative_note,
        "humour_summary": humour.strip() or preset.humour,
    }


class ApplianceSetupPort(Protocol):
    """Contract between the FastAPI wizard UI and the host-side appliance service."""

    def client_permitted(self, remote_host: str | None) -> bool: ...

    def is_paired_session(self, session_cookie: str | None) -> bool: ...

    def exchange_pairing_code(self, code: str, *, client_key: str) -> str: ...

    def exchange_loopback_session(self, *, remote_host: str | None) -> str: ...

    def status_snapshot(self) -> dict[str, Any]: ...

    def save_profile_and_hardware(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def save_character(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def rollback_character(self) -> dict[str, Any]: ...

    def export_soul_markdown(self) -> str: ...

    def preview_character(self, payload: Mapping[str, Any]) -> dict[str, str]: ...

    def start_codex_login(self) -> dict[str, Any]: ...

    def poll_codex_login(self) -> dict[str, Any]: ...

    def save_and_test_api_model(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def save_model_budget(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def save_module(self, module_id: str, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def test_module(self, module_id: str, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def apply_installation(self) -> dict[str, Any]: ...

    def export_portable_bundle(self) -> dict[str, Any]: ...

    def import_portable_bundle(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def public_ping(self) -> dict[str, Any]: ...

    def contact_hub_snapshot(self, *, base_origin: str | None = None) -> dict[str, Any]: ...

    def create_device_pairing(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]: ...

    def revoke_device_pairing(self, grant_id: str) -> dict[str, Any]: ...

    def start_telegram_magic_link(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def poll_telegram_magic_link(self) -> dict[str, Any]: ...

    def remove_telegram_contact(self, telegram_user_id: int) -> dict[str, Any]: ...

    def test_live_contact(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def living_stage_snapshot(self, *, base_origin: str | None = None) -> dict[str, Any]: ...

    def handle_living_turn(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]: ...

    def undo_living_change(self, change_id: str) -> dict[str, Any]: ...

    def remove_household_rule(self, rule_index: int) -> dict[str, Any]: ...

    def set_guest_mode(self, *, enabled: bool) -> dict[str, Any]: ...

    def scan_lan_devices(self) -> dict[str, Any]: ...

    def set_lan_device_consent(self, device_id: str, *, approved: bool) -> dict[str, Any]: ...

    def create_member_welcome(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]: ...

    def get_member_welcome(self, token: str) -> dict[str, Any]: ...

    def complete_member_welcome(
        self, token: str, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]: ...

    def synthesize_living_speech(self, text: str) -> tuple[bytes, str] | None: ...

    def enroll_living_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def check_living_voice(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def set_onboarding_wizard_state(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def connect_smart_home(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def connect_universal_service(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def remove_universal_service(self, service_id: str) -> dict[str, Any]: ...

    def delete_custom_capability(self, capability_id: str) -> dict[str, Any]: ...

    def save_realtime_voice_preference(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def start_realtime_webrtc_session(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]: ...

    def stop_realtime_voice_session(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def stream_living_turn_ndjson(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> Any: ...

    async def bridge_gemini_live_websocket(
        self, websocket: Any, *, model: str, voice: str, base_origin: str | None = None
    ) -> None: ...

    def ensure_lan_tls_cert(self) -> tuple[Path, Path]: ...

    def get_lan_ca_cert_pem(self) -> str: ...

    def save_main_display_config(
        self, payload: Mapping[str, Any], *, base_origin: str | None = None
    ) -> dict[str, Any]: ...

    def wake_main_display(self) -> dict[str, Any]: ...

    def handle_vision_look(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def proxy_dashboard_request(
        self,
        *,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]: ...

    def open_dashboard_stream(
        self,
        *,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        body: bytes,
        owner_authenticated: bool = True,
    ) -> tuple[int, dict[str, str], bytes | Iterator[bytes]]: ...


__all__ = [
    "ApplianceSetupPort",
    "CHARACTER_PRESETS",
    "CharacterPreset",
    "HARDWARE_TIERS",
    "HardwareTier",
    "HardwareTierProfile",
    "MODULE_CATALOG",
    "ModuleCatalogItem",
    "ProactivityLevel",
    "build_budget_for_tier",
    "build_character_soul_markdown",
    "get_character_preset",
    "recommend_hardware_tier",
    "render_character_preview",
]
