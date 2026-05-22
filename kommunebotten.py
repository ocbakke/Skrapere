import html
import json
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable
from urllib.parse import urljoin

from google import genai
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# --- KONFIGURASJON ---
API_KEY = os.environ.get("GEMINI_API_KEY")
EPOST_AVSENDER = os.environ.get("EPOST_BRUKER")
EPOST_PASSORD = os.environ.get("EPOST_PASSORD")
EPOST_MOTTAKER = os.environ.get("EPOST_MOTTAKER_KOMMUNE")

URL_TIL_LISTEN = "https://sarpsborg.pj.360online.com/"
SEEN_FILE = "sette_dokumenter.txt"
MAKS_SIDER = int(os.environ.get("MAKS_SIDER", "20"))
MAKS_LAGREDE_IDS = int(os.environ.get("MAKS_LAGREDE_IDS", "5000"))
AI_BATCH_SIZE = int(os.environ.get("AI_BATCH_SIZE", "30"))
AI_MIN_SCORE = int(os.environ.get("AI_MIN_SCORE", "7"))
MAKS_FUNN_I_EPOST = int(os.environ.get("MAKS_FUNN_I_EPOST", "25"))
GEMINI_MODELL = os.environ.get("GEMINI_MODELL", "gemini-3-flash-preview")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

DOKUMENTNUMMER_RE = re.compile(r"\b\d{2}/\d{5}-\d+\b")
GEMINI_MODELLER = list(
    dict.fromkeys(
        [
            GEMINI_MODELL,
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
        ]
    )
)

print(f"DEBUG: API-nøkkel funnet: {'JA' if API_KEY else 'NEI'}")

try:
    client = genai.Client(api_key=API_KEY) if API_KEY else None
except Exception as e:
    print(f"FEIL: Klarte ikke starte AI-klienten. ({e})")
    client = None


@dataclass(frozen=True)
class Journalpost:
    uid: str
    dokumentnummer: str
    dokumentnavn: str
    sak: str
    part_rolle: str
    part: str
    journaldato: str
    url: str

    @property
    def stabil_id(self) -> str:
        return self.dokumentnummer or self.uid

    def til_ai(self) -> dict:
        return {
            "id": self.stabil_id,
            "dokumentnummer": self.dokumentnummer,
            "dokumentnavn": self.dokumentnavn,
            "sak": self.sak,
            "part_rolle": self.part_rolle,
            "part": self.part,
            "journaldato": self.journaldato,
            "url": self.url,
        }


HARDT_KJEDELIGE_ORD = [
    "arbeidsavtale",
    "attest",
    "avslag søknad",
    "avslag, ikke videre",
    "barnehagemappe",
    "cv",
    "elevmappe",
    "elevpermisjon",
    "ekstratømming",
    "ferdigattest",
    "feilsortert avfall",
    "igangsetting",
    "ledsagerbevis",
    "motorferdsel",
    "parkeringstillatelse",
    "parkeringsgebyr",
    "personalmappe",
    "sanitær",
    "skjenkebevilling",
    "startlån",
    "svar på søknad",
    "sykefraværsoppfølging",
    "tjenestebevis",
    "tilleggslån",
    "vigsel",
]

REKRUTTERING_ORD = [
    "ansettelse",
    "ansettelsesprosess",
    "arbeidssøker",
    "barne- og ungdomsarbeider",
    "fagarbeider",
    "helsefagarbeider",
    "intervju",
    "ledig stilling",
    "miljøterapeut",
    "sommervikar",
    "sommerjobb",
    "st. ref.",
    "student",
    "sykepleier",
    "tilsetting",
    "vernepleier",
    "vikar",
    "vikariat",
]

LEDERSTILLING_ORD = [
    "avdelingsleder",
    "barnehagestyrer",
    "kommunalsjef",
    "kommunedirektør",
    "rektor",
    "seksjonsleder",
    "teamleder",
    "virksomhetsleder",
    "direktør",
    "sjef",
]

STERKE_NYHETSORD = [
    "arbeidstilsynet",
    "avvik",
    "bekymringsmelding",
    "erstatning",
    "forurens",
    "habilitet",
    "klage",
    "konkurs",
    "kritikkverdig",
    "lovbrudd",
    "mislighold",
    "pålegg",
    "politianmeld",
    "statsforvalter",
    "tilsyn",
    "trussel",
    "tvangsmulkt",
    "ulovlig",
    "varsel",
    "innsigelse",
    "bekymring",
    
]


def normaliser_tekst(tekst: str) -> str:
    return re.sub(r"\s+", " ", tekst or "").strip()


def inneholder_noe(tekst: str, ordliste: Iterable[str]) -> bool:
    tekst = tekst.lower()
    return any(ordet in tekst for ordet in ordliste)


def les_synlige_eller_skjulte_felter(driver, element) -> str:
    tekst = driver.execute_script("return arguments[0].textContent || '';", element)
    return normaliser_tekst(tekst)


def hent_seen_ids() -> list[str]:
    if not os.path.exists(SEEN_FILE):
        return []

    ids = []
    med_sett = set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        for linje in f:
            linje = linje.strip()
            if not linje:
                continue

            match = DOKUMENTNUMMER_RE.search(linje)
            stabil_id = match.group(0) if match else linje
            if stabil_id not in med_sett:
                ids.append(stabil_id)
                med_sett.add(stabil_id)
    return ids


def lagre_seen_ids(gamle_ids: list[str], nye_ids: list[str]) -> None:
    samlet = []
    sett_ids = set()
    for stabil_id in gamle_ids + nye_ids:
        if stabil_id and stabil_id not in sett_ids:
            samlet.append(stabil_id)
            sett_ids.add(stabil_id)

    samlet = samlet[-MAKS_LAGREDE_IDS:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for stabil_id in samlet:
            f.write(stabil_id + "\n")


def klassifiser_forfilter(post: Journalpost) -> tuple[bool, str]:
    tekst = " ".join([post.dokumentnavn, post.sak, post.part]).lower()

    if inneholder_noe(tekst, HARDT_KJEDELIGE_ORD):
        return False, "standard/privat sak"

    er_rekruttering = inneholder_noe(tekst, REKRUTTERING_ORD)
    er_lederstilling = inneholder_noe(tekst, LEDERSTILLING_ORD)
    har_sterkt_nyhetsord = inneholder_noe(tekst, STERKE_NYHETSORD)

    if er_rekruttering and not er_lederstilling and not har_sterkt_nyhetsord:
        return False, "rekruttering/personalsak"

    if post.sak.lower().startswith("innsyn - byggesaker") and not har_sterkt_nyhetsord:
        return False, "rutineinnsyn byggesak"

    return True, "til AI-vurdering"


def finn_journalposter(driver) -> list[Journalpost]:
    poster = []
    rader = driver.find_elements(By.CSS_SELECTOR, "li[id^='documentrow_']")

    for rad in rader:
        try:
            uid = (rad.get_attribute("id") or "").replace("documentrow_", "")

            try:
                tittel_element = rad.find_element(By.CSS_SELECTOR, ".document-card-title")
                dokumentnavn = les_synlige_eller_skjulte_felter(driver, tittel_element)
            except NoSuchElementException:
                dokumentnavn = ""

            detaljer = {}
            for detalj in rad.find_elements(By.CSS_SELECTOR, ".detailsList"):
                try:
                    nøkkel = les_synlige_eller_skjulte_felter(
                        driver,
                        detalj.find_element(By.CSS_SELECTOR, ".documentDetailHeader"),
                    )
                    verdi = les_synlige_eller_skjulte_felter(
                        driver,
                        detalj.find_element(By.CSS_SELECTOR, ".documentDetailContent"),
                    )
                except NoSuchElementException:
                    continue
                if nøkkel:
                    detaljer[nøkkel] = verdi

            dokumentnummer = detaljer.get("Dokumentnummer", "")
            if not dokumentnummer:
                continue

            part_rolle = "Fra" if "Fra" in detaljer else "Til" if "Til" in detaljer else ""
            part = detaljer.get(part_rolle, "")

            try:
                href = rad.find_element(By.CSS_SELECTOR, ".accordionTitle a").get_attribute("href")
            except NoSuchElementException:
                href = ""

            poster.append(
                Journalpost(
                    uid=uid,
                    dokumentnummer=dokumentnummer,
                    dokumentnavn=dokumentnavn,
                    sak=detaljer.get("Sak", ""),
                    part_rolle=part_rolle,
                    part=part,
                    journaldato=detaljer.get("Journaldato", ""),
                    url=urljoin(URL_TIL_LISTEN, href),
                )
            )
        except Exception as e:
            print(f"DEBUG: Hoppet over en journalpost som ikke lot seg parse. ({e})")

    return poster


def vent_paa_journalposter(driver) -> None:
    WebDriverWait(driver, 20).until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "li[id^='documentrow_']"))
    )


def gaa_til_side(driver, side: int) -> bool:
    if side == 1:
        return True

    try:
        gammel_første_rad = driver.find_elements(By.CSS_SELECTOR, "li[id^='documentrow_']")
        gammel_første_rad = gammel_første_rad[0] if gammel_første_rad else None

        xpath = f"//a[normalize-space()='{side}'] | //button[normalize-space()='{side}']"
        knapp = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        driver.execute_script("arguments[0].click();", knapp)

        if gammel_første_rad:
            WebDriverWait(driver, 10).until(EC.staleness_of(gammel_første_rad))
        vent_paa_journalposter(driver)
        return True
    except TimeoutException:
        print(f"DEBUG: Fant ikke side {side}. Stopper paginering.")
        return False
    except Exception as e:
        print(f"DEBUG: Klarte ikke gå til side {side}. ({e})")
        return False


def del_i_batcher(liste: list[Journalpost], størrelse: int) -> Iterable[list[Journalpost]]:
    for i in range(0, len(liste), størrelse):
        yield liste[i : i + størrelse]


def hent_json_liste_fra_ai_tekst(tekst: str) -> list[dict]:
    if not tekst:
        return []

    tekst = tekst.strip()
    tekst = re.sub(r"^```(?:json)?", "", tekst).strip()
    tekst = re.sub(r"```$", "", tekst).strip()

    start = tekst.find("[")
    slutt = tekst.rfind("]")
    if start == -1 or slutt == -1 or slutt <= start:
        return []

    try:
        data = json.loads(tekst[start : slutt + 1])
    except json.JSONDecodeError as e:
        print(f"DEBUG: AI svarte ikke med gyldig JSON. ({e})")
        return []

    return data if isinstance(data, list) else []


def analyser_batch_med_gemini(liste_med_saker: list[Journalpost]) -> tuple[list[dict], bool]:
    if not client or not liste_med_saker:
        return [], bool(client)

    saker_json = json.dumps([sak.til_ai() for sak in liste_med_saker], ensure_ascii=False)
    prompt = f"""Du er en erfaren nyhetsjournalist i lokalavisen Sarpsborg Arbeiderblad.
Vurder nye journalposter fra Sarpsborg kommune. Bruk bare informasjonen i feltene, og ikke finn på detaljer.

Returner KUN en gyldig JSON-liste. Hvis ingen saker er gode nok, returner [].

Ta bare med saker som har tydelig lokal nyhetsverdi, score 7-10:
- konflikt, klage, lovbrudd, tvangsmulkt, pålegg, trusler eller varsel
- tilsyn, alvorlig kritikk, avvik eller henvendelser fra Statsforvalter/Arbeidstilsynet
- habilitet, politiske stridstemaer eller mulig svikt i kommunale tjenester
- konkurs, erstatningskrav, store pengesummer, kjøp/salg/avtaler med vesentlig offentlig interesse
- plan-, bygg-, miljø- og eiendomssaker bare når de virker prinsipielle, konfliktfylte eller store

Ikke ta med:
- ansettelser, jobbsøknader, arbeidsavtaler, sommervikarer, vikariater, helsefagarbeidere, sykepleiere, miljøterapeuter eller andre ordinære personalsaker
- elevmapper, barnehagemapper, startlån, parkering, vigsel, skjenkebevilling, ferdigattest og andre rene rutinesaker
- saker der eneste begrunnelse er at navn eller innhold er avskjermet
- forklaringer om at en sak ikke er nyhetsverdig

JSON-format:
[
  {{
    "id": "eksakt id fra input",
    "score": 8,
    "kategori": "kort kategori",
    "begrunnelse": "maks 25 ord, konkret og redaksjonell"
  }}
]

Journalposter:
{saker_json}"""

    siste_feil = None
    response = None
    brukt_modell = None
    for modell in GEMINI_MODELLER:
        try:
            response = client.models.generate_content(model=modell, contents=prompt)
            brukt_modell = modell
            break
        except Exception as e:
            siste_feil = e
            print(f"AI-feil med modell {modell}: {e}")

    if response is None:
        print(f"AI-feil: Ingen Gemini-modeller fungerte. Siste feil: {siste_feil}")
        return [], False

    try:
        data = hent_json_liste_fra_ai_tekst(response.text or "")
        poster_per_id = {post.stabil_id: post for post in liste_med_saker}
        funn = []

        for treff in data:
            if not isinstance(treff, dict):
                continue
            stabil_id = str(treff.get("id", "")).strip()
            post = poster_per_id.get(stabil_id)
            if not post:
                continue

            try:
                score = int(treff.get("score", 0))
            except (TypeError, ValueError):
                score = 0

            if score < AI_MIN_SCORE:
                continue

            funn.append(
                {
                    "post": post,
                    "score": score,
                    "kategori": normaliser_tekst(str(treff.get("kategori", "")))[:80],
                    "begrunnelse": normaliser_tekst(str(treff.get("begrunnelse", "")))[:240],
                }
            )

        print(
            f"DEBUG: AI vurderte {len(liste_med_saker)} saker med {brukt_modell} "
            f"og fant {len(funn)} treff."
        )
        return funn, True
    except Exception as e:
        print(f"AI-feil: {e}")
        return [], False


def safe(value: str) -> str:
    return html.escape(value or "", quote=True)


def send_nyhetsvarsel_epost(funn_liste: list[dict], statistikk: dict) -> None:
    if not funn_liste:
        return

    funn_liste = sorted(funn_liste, key=lambda item: item["score"], reverse=True)
    viste_funn = funn_liste[:MAKS_FUNN_I_EPOST]

    msg = EmailMessage()
    msg["Subject"] = f"AI-Tips: {len(funn_liste)} mulige saker i postjournalen"
    msg["From"] = EPOST_AVSENDER
    msg["To"] = EPOST_MOTTAKER

    silt_bort = statistikk.get("silt_bort", {})
    silt_bort_linje = ", ".join(
        f"{safe(årsak)}: {antall}" for årsak, antall in sorted(silt_bort.items())
    )
    if not silt_bort_linje:
        silt_bort_linje = "ingen"

    html_innhold = f"""
    <div style="font-family: Arial, sans-serif; color: #222;">
        <h2 style="color: #b8403a; margin-bottom: 8px;">Mulige saker fra Sarpsborg postjournal</h2>
        <p style="margin-top: 0;">
            Hentet {statistikk.get("hentet", 0)} journalposter.
            Nye: {statistikk.get("nye", 0)}.
            Sendt til AI: {statistikk.get("til_ai", 0)}.
            Silt bort: {silt_bort_linje}.
        </p>
    """

    if len(funn_liste) > len(viste_funn):
        html_innhold += (
            f"<p>Viser de {len(viste_funn)} høyest rangerte treffene "
            f"av {len(funn_liste)}.</p>"
        )

    for funn in viste_funn:
        post = funn["post"]
        part_linje = f"{post.part_rolle}: {post.part}" if post.part_rolle else post.part
        html_innhold += f"""
        <div style="border-left: 5px solid #b8403a; padding: 12px; margin: 0 0 16px 0; background-color: #f7f7f7;">
            <p style="font-size: 15px; margin: 0 0 8px 0;"><strong>{safe(post.dokumentnavn)}</strong></p>
            <p style="font-size: 14px; margin: 0 0 8px 0;">
                <strong>Score:</strong> {funn["score"]}/10
                &nbsp; <strong>Kategori:</strong> {safe(funn["kategori"])}
            </p>
            <p style="font-size: 14px; margin: 0 0 8px 0;">
                <strong>Hvorfor:</strong> {safe(funn["begrunnelse"])}
            </p>
            <p style="font-size: 13px; margin: 0; line-height: 1.5;">
                <strong>Dokumentnummer:</strong> {safe(post.dokumentnummer)}<br>
                <strong>Sak:</strong> {safe(post.sak)}<br>
                <strong>{safe(part_linje)}</strong><br>
                <strong>Journaldato:</strong> {safe(post.journaldato)}<br>
                <a href="{safe(post.url)}">Åpne i postjournalen</a>
            </p>
        </div>
        """

    html_innhold += "</div>"
    msg.add_alternative(html_innhold, subtype="html")

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EPOST_AVSENDER, EPOST_PASSORD)
            server.send_message(msg)
            print("DEBUG: E-post sendt!")
    except Exception as e:
        print(f"DEBUG: E-post feil: {e}")


def main() -> None:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)

    gamle_ids = hent_seen_ids()
    sett_ids_oppslag = set(gamle_ids)
    nye_ids_som_kan_lagres = []
    kandidater = []
    statistikk = {
        "hentet": 0,
        "nye": 0,
        "til_ai": 0,
        "silt_bort": {},
    }

    try:
        print(f"DEBUG: Åpner {URL_TIL_LISTEN}")
        driver.get(URL_TIL_LISTEN)
        vent_paa_journalposter(driver)

        for side in range(1, MAKS_SIDER + 1):
            if not gaa_til_side(driver, side):
                break

            poster = finn_journalposter(driver)
            statistikk["hentet"] += len(poster)
            print(f"DEBUG: Side {side}: fant {len(poster)} journalposter.")

            for post in poster:
                if post.stabil_id in sett_ids_oppslag:
                    continue

                sett_ids_oppslag.add(post.stabil_id)
                statistikk["nye"] += 1

                skal_vurderes, årsak = klassifiser_forfilter(post)
                if skal_vurderes:
                    kandidater.append(post)
                else:
                    nye_ids_som_kan_lagres.append(post.stabil_id)
                    statistikk["silt_bort"][årsak] = statistikk["silt_bort"].get(årsak, 0) + 1

        alle_ai_funn = []
        ai_godkjente_ids = []
        statistikk["til_ai"] = len(kandidater)

        ai_batcher = 0
        ai_feil = 0
        for batch in del_i_batcher(kandidater, AI_BATCH_SIZE):
            ai_batcher += 1
            funn, ok = analyser_batch_med_gemini(batch)
            alle_ai_funn.extend(funn)
            if ok:
                ai_godkjente_ids.extend(post.stabil_id for post in batch)
            else:
                ai_feil += 1

        if kandidater and ai_feil == ai_batcher:
            raise RuntimeError(
                "AI-vurdering feilet for alle kandidatbatcher. "
                "Stopper slik at dette ikke ser ut som 'ingen treff'."
            )

        nye_ids_som_kan_lagres.extend(ai_godkjente_ids)
        lagre_seen_ids(gamle_ids, nye_ids_som_kan_lagres)

        if alle_ai_funn:
            send_nyhetsvarsel_epost(alle_ai_funn, statistikk)
        else:
            print("DEBUG: Ingen nyhetstips funnet i denne runden.")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
