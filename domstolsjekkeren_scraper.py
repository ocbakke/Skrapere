import os
import json
import html
import re
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
import smtplib
import ssl
from email.message import EmailMessage

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException

# --- KONFIGURASJON ---
DOMSTOL_ID = "AAAA2103101754092672012RXHZEG_EJBOrgUnit" # Søndre Østfold tingrett
CACHE_FILE = Path("cache_sa.json")
DEBUG_DIR = Path("debug_domstolsjekkeren")
PAGE_SIZE = 500
WAIT_SECONDS = 20
MAX_PAGES = 10
DATO_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")

# E-post konfigurasjon
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Henter innloggingsdetaljer fra miljøvariabler (GitHub Secrets)
EPOST_AVSENDER = os.environ.get("EPOST_BRUKER") 
EPOST_PASSORD = os.environ.get("EPOST_PASSORD") 
EPOST_MOTTAKER = os.environ.get("EPOST_MOTTAKER")
TINGRETT_EPOST = "sondre.ostfold.tingrett@domstol.no" 

def les_cache():
    if not CACHE_FILE.exists():
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Cache-filen {CACHE_FILE} er ugyldig JSON. Stopper for å unngå feilvarsling.") from e
    except OSError as e:
        raise SystemExit(f"Kunne ikke lese cache-filen {CACHE_FILE}: {e}") from e

    if not isinstance(cache, dict):
        raise SystemExit(f"Cache-filen {CACHE_FILE} må inneholde et JSON-objekt.")
    return cache

def skriv_cache(cache):
    tmp_file = CACHE_FILE.with_name(f"{CACHE_FILE.name}.tmp")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp_file.replace(CACHE_FILE)
    except OSError as e:
        raise SystemExit(f"Kunne ikke skrive cache-filen {CACHE_FILE}: {e}") from e

def html_escape(verdi):
    return html.escape(str(verdi or ""), quote=True)

def valider_epostkonfigurasjon():
    mangler = []
    if not EPOST_AVSENDER:
        mangler.append("EPOST_BRUKER")
    if not EPOST_PASSORD:
        mangler.append("EPOST_PASSORD")
    if not EPOST_MOTTAKER:
        mangler.append("EPOST_MOTTAKER")

    if mangler:
        raise SystemExit(f"Mangler miljøvariabler for e-post: {', '.join(mangler)}")

def lagre_debug_side(driver, navn):
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = DEBUG_DIR / f"{navn}_{timestamp}"
        html_fil = base.with_suffix(".html")
        png_fil = base.with_suffix(".png")
        html_fil.write_text(driver.page_source, encoding="utf-8")
        driver.save_screenshot(str(png_fil))
        print(f"Lagret debug-filer: {html_fil} og {png_fil}")
    except (OSError, WebDriverException) as e:
        print(f"Klarte ikke å lagre debug-filer: {e}")

def finn_neste_side_knapp(driver):
    selectors = [
        "a[rel='next']",
        "a[aria-label*='Neste']",
        "button[aria-label*='Neste']",
        "a[title*='Neste']",
        "button[title*='Neste']",
    ]

    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if er_klikkbar_neste_knapp(element):
                return element

    for element in driver.find_elements(By.CSS_SELECTOR, "a, button"):
        tekst = element.text.strip().lower()
        aria = (element.get_attribute("aria-label") or "").strip().lower()
        if (tekst in {"neste", "next", ">"} or "neste" in aria or "next" in aria) and er_klikkbar_neste_knapp(element):
            return element

    return None

def er_klikkbar_neste_knapp(element):
    disabled = element.get_attribute("disabled") is not None or element.get_attribute("aria-disabled") == "true"
    return not disabled and element.is_displayed() and element.is_enabled()

def hent_sakslenke(saksnr_celle, fallback_url):
    lenker = saksnr_celle.find_elements(By.TAG_NAME, "a")
    if not lenker:
        return fallback_url

    return lenker[0].get_attribute("href") or fallback_url

def parse_saksdato(rettsmoete_full):
    dato_match = DATO_RE.search(rettsmoete_full)
    if not dato_match:
        return None, None

    dato_str = dato_match.group(0)
    try:
        return dato_str, datetime.strptime(dato_str, "%d.%m.%Y")
    except ValueError:
        return dato_str, None

def lag_cache_id(saksnr, rettsmoete_full):
    return f"{saksnr}_{rettsmoete_full}"

def lag_legacy_cache_id(saksnr, dato_str):
    return f"{saksnr}_{dato_str}"

def lag_plaintekst(nye_saker):
    linjer = ["Følgende nye saker fra Sarpsborg er lagt til i berammingslisten:", ""]
    for sak in nye_saker:
        linjer.extend([
            f"Rettsmøte: {sak['rettsmoete']}",
            f"Saksnr: {sak['saksnr']}",
            f"Saken gjelder: {sak['saken_gjelder']}",
            f"Parter: {sak['parter']}",
            f"Sak hos domstol.no: {sak['sakslenke']}",
            "Presseportal: https://presse.domstol.no/",
            "",
        ])
    return "\n".join(linjer)

def bygg_html(nye_saker):
    html_innhold = """
    <div style="font-family: Arial, sans-serif; color: #333;">
        <h2>Følgende nye saker fra Sarpsborg er lagt til i berammingslisten:</h2>
    """

    for sak in nye_saker:
        saksnr = sak["saksnr"]
        emne = f"Innsyn i sluttinnlegg - {saksnr}"
        innhold = f"Hei,\n\nSarpsborg Arbeiderblad ber om innsyn i sluttinnleggene i {saksnr}."

        mailto_lenke = (
            f"mailto:{TINGRETT_EPOST}"
            f"?subject={urllib.parse.quote(emne)}"
            f"&body={urllib.parse.quote(innhold)}"
        )

        html_innhold += f"""
        <div style="border: 1px solid #ccc; padding: 15px; margin-bottom: 20px; border-radius: 5px; background-color: #f9f9f9;">
            <p style="margin: 0 0 10px 0;"><strong>Rettsmøte:</strong> {html_escape(sak['rettsmoete'])}</p>
            <p style="margin: 0 0 10px 0;"><strong>Saksnr:</strong> {html_escape(sak['saksnr'])}</p>
            <p style="margin: 0 0 10px 0;"><strong>Saken gjelder:</strong> {html_escape(sak['saken_gjelder'])}</p>
            <p style="margin: 0 0 15px 0;"><strong>Parter:</strong> {html_escape(sak['parter'])}</p>
            <div style="overflow: auto;">
                <a href="{html_escape(sak['sakslenke'])}" style="display:inline-block; padding:10px 15px; background-color:#0056b3; color:white; text-decoration:none; border-radius:4px; font-weight: bold;">Åpne saken hos domstol.no</a>
                <a href="{html_escape(mailto_lenke)}" style="display:inline-block; padding:10px 15px; background-color:#28a745; color:white; text-decoration:none; border-radius:4px; margin-left: 10px; font-weight: bold;">Opprett innsynskrav i e-post</a>
                <a href="https://presse.domstol.no/" style="display:inline-block; padding:8px 15px; background-color:#e9ecef; color:#0056b3; text-decoration:none; border-radius:4px; font-weight: bold; border: 2px solid #0056b3; float: right;">Se saken i presseportalen</a>
            </div>
            <div style="clear: both;"></div>
        </div>
        """

    html_innhold += "</div>"
    return html_innhold

def behandle_rad(rad, url, i_dag, grense, sendte_varsler, funnet_saker):
    cols = rad.find_elements(By.TAG_NAME, "td")
    if len(cols) < 5:
        return

    saksnr_celle = cols[1]
    saksnr = saksnr_celle.text.strip()
    rettsmoete_full = cols[0].text.strip()

    if not saksnr.endswith("TSAR"):
        return

    dato_str, sak_dato = parse_saksdato(rettsmoete_full)
    if sak_dato is None:
        print(f"Hopper over rad uten gyldig dato: {saksnr} / {rettsmoete_full}")
        return

    cache_id = lag_cache_id(saksnr, rettsmoete_full)
    legacy_cache_id = lag_legacy_cache_id(saksnr, dato_str)
    if cache_id in sendte_varsler or legacy_cache_id in sendte_varsler:
        return

    if not i_dag.date() <= sak_dato.date() <= grense.date():
        return

    print(f"Fant ny Sarpsborg-sak: {saksnr}")
    funnet_saker.append({
        "rettsmoete": rettsmoete_full,
        "saksnr": saksnr,
        "domstol": cols[2].text.strip(),
        "saken_gjelder": cols[3].text.strip(),
        "parter": cols[4].text.strip(),
        "sakslenke": hent_sakslenke(saksnr_celle, url),
    })
    sendte_varsler[cache_id] = datetime.now().isoformat(timespec="seconds")

def send_epost_liste(nye_saker):
    if not nye_saker:
        return True

    msg = EmailMessage()
    msg['Subject'] = f"🚨 Nye Sarpsborg-saker i Søndre Østfold tingrett ({len(nye_saker)})"
    msg['From'] = EPOST_AVSENDER
    msg['To'] = EPOST_MOTTAKER
    msg.set_content(lag_plaintekst(nye_saker))
    msg.add_alternative(bygg_html(nye_saker), subtype='html')

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(EPOST_AVSENDER, EPOST_PASSORD)
            server.send_message(msg)
        print(f"Suksess: Sendte e-post med {len(nye_saker)} saker til {EPOST_MOTTAKER}.")
        return True
    except (OSError, smtplib.SMTPException) as e:
        print(f"Feil ved sending av e-post: {e}")
        return False

def main():
    valider_epostkonfigurasjon()
    sendte_varsler = les_cache()

    options = Options()
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080") 
    options.add_argument("--disable-blink-features=AutomationControlled") 
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    
    i_dag = datetime.now()
    grense = i_dag + timedelta(days=14)
    
    fra_dato_str = i_dag.strftime("%Y-%m-%d")
    til_dato_str = grense.strftime("%Y-%m-%d")
    
    url = f"https://www.domstol.no/no/nar-gar-rettssaken/?fraDato={fra_dato_str}&tilDato={til_dato_str}&domstolid={DOMSTOL_ID}&sortTerm=rettsmoete&sortAscending=true&pageSize={PAGE_SIZE}&query=TSAR"
    
    funnet_saker = []
    wait = WebDriverWait(driver, WAIT_SECONDS)

    try:
        print(f"Henter saker fra: {url}")
        driver.get(url)
        
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        except TimeoutException as e:
            lagre_debug_side(driver, "ingen_tabell")
            raise RuntimeError("Fant ingen tabell på siden. Sjekk debug-filene før dette tolkes som tomt søkeresultat.") from e

        side_nr = 1
        while True:
            rader = driver.find_elements(By.CSS_SELECTOR, "table tr")[1:]
            print(f"Fant {len(rader)} rader i tabellen på side {side_nr}. Sjekker for nye saker...")

            for rad in rader:
                try:
                    behandle_rad(rad, url, i_dag, grense, sendte_varsler, funnet_saker)
                except WebDriverException as e:
                    print(f"Feil ved parsing av rad: {e}")

            neste_knapp = finn_neste_side_knapp(driver)
            if not neste_knapp:
                break
            if side_nr >= MAX_PAGES:
                raise RuntimeError(f"Stoppet etter {MAX_PAGES} resultatsider for å unngå uendelig paginering.")

            forste_rad = rader[0] if rader else None
            print("Går videre til neste resultatside...")
            driver.execute_script("arguments[0].click();", neste_knapp)
            if forste_rad:
                try:
                    wait.until(EC.staleness_of(forste_rad))
                except TimeoutException:
                    pass
            try:
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
            except TimeoutException as e:
                lagre_debug_side(driver, f"ingen_tabell_side_{side_nr + 1}")
                raise RuntimeError(f"Fant ingen tabell etter klikk til side {side_nr + 1}.") from e
            side_nr += 1
                    
        if funnet_saker:
            if not send_epost_liste(funnet_saker):
                raise SystemExit("E-postsending feilet. Cache er ikke oppdatert, slik at sakene prøves igjen neste gang.")
            skriv_cache(sendte_varsler)
        else:
            print("Ingen NYE Sarpsborg-saker funnet i dag (de som lå der var allerede varslet om).")
            
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
