import time
import os
import smtplib
from email.message import EmailMessage
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from google import genai

# --- KONFIGURASJON ---
# Henter hemmeligheter fra GitHub Secrets / miljøvariabler
API_KEY = os.environ.get("GEMINI_API_KEY") 
EPOST_AVSENDER = os.environ.get("EPOST_BRUKER") 
EPOST_PASSORD = os.environ.get("EPOST_PASSORD") 
EPOST_MOTTAKER = os.environ.get("EPOST_MOTTAKER", "ocb@sa.no, redaksjonen@sa.no") 

URL_TIL_LISTEN = "https://sarpsborg.pj.360online.com/"
SEEN_FILE = "sette_dokumenter.txt"
MAKS_SIDER = 10

# Start AI-klienten
try:
    if API_KEY:
        client = genai.Client(api_key=API_KEY)
    else:
        client = None
        print("ADVARSEL: GEMINI_API_KEY mangler. AI-analysen vil ikke fungere.")
except Exception as e:
    print(f"FEIL: Klarte ikke starte AI-klienten. ({e})")
    client = None

def grovfilter(tekst):
    """Returnerer False hvis teksten inneholder kjedelige ord."""
    kjedelige_ord = [
        "parkering", "skjenkebevilling", "ferdigattest", "igangsetting",
        "sanitær", "feilsortert avfall", "ekstratømming", "gebyr",
        "startlån", "tilleggslån", "motorferdsel", "vigsel", "elevpermisjon",
        "overfylte beholdere", "renovasjonsforskriften", "restavfallsbeholder",
        "oppmålingsforretning", "seksjonering", "transporttjeneste",
        "tt-kort", "parkeringstillatelse", "ledsagerbevis", "skoleskyss",
        "vedtak om spesialundervisning", "individuell opplæringsplan"
    ]
    return not any(ordet in tekst.lower() for ordet in kjedelige_ord)

def analyser_batch_med_gemini(liste_med_saker):
    """Sender en HEL LISTE til Gemini i én operasjon for å spare kvote."""
    if not client or not liste_med_saker:
        return []

    tekst_blokk = ""
    for i, sak in enumerate(liste_med_saker):
        tekst_blokk += f"ID {i}: {sak}\n"

    prompt = f"""
    Du er nyhetsjournalist i Sarpsborg Arbeiderblad. Her er en liste over nye dokumenter fra postjournalen.
    Din oppgave er å plukke ut DE FÅ som er nyhetsverdige. Vær streng.

    Kriterier for TREFF:
    - Konflikter, klager, lovbrudd, tvangsmulkt, trusler.
    - Konkurs, store pengesummer, erstatningskrav.
    - Alvorlig kritikk fra tilsyn (Statsforvalter, Arbeidstilsyn).
    - Politiske stridstemaer, varslingssaker, habilitet.
    - "Unntatt offentlighet" hvis tittelen virker dramatisk.

    Her er listen:
    {tekst_blokk}

    SVAR KUN SLIK (for hver sak du finner):
    ID [nummer]: [Kort begrunnelse]
    """

    for forsok in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )

            funn = []
            if response.text:
                linjer = response.text.split("\n")
                for linje in linjer:
                    if "ID" in linje and ":" in linje:
                        deler = linje.split(":", 1)
                        try:
                            id_str = deler[0].replace("ID", "").strip()
                            id_num = int(id_str)
                            begrunnelse = deler[1].strip()

                            if 0 <= id_num < len(liste_med_saker):
                                original_tekst = liste_med_saker[id_num]
                                # Returnerer som en ordbok (dictionary) for penere e-post
                                funn.append({
                                    "begrunnelse": begrunnelse,
                                    "tekst": original_tekst
                                })
                        except:
                            continue
            return funn

        except Exception as e:
            if "429" in str(e):
                print(f"  ⏳ Traff fartsgrensen. Venter 60 sek... (Batch-modus)")
                time.sleep(60)
                continue
            else:
                print(f"  ⚠️ AI-feil i batch: {e}")
                return []
    return []

def send_nyhetsvarsel_epost(funn_liste):
    """Sender en oppsummert e-post med alle AI-funnene."""
    if not funn_liste or not EPOST_AVSENDER or not EPOST_PASSORD:
        return

    msg = EmailMessage()
    msg['Subject'] = f"🤖 AI-Tips: {len(funn_liste)} nye saker i postjournalen"
    msg['From'] = EPOST_AVSENDER
    msg['To'] = EPOST_MOTTAKER

    html_innhold = """
    <div style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #0056b3;">Nyhetsroboten har funnet noe interessant! 🗞️</h2>
        <p>Her er dokumentene Gemini mener er verdt å sjekke ut i Sarpsborg kommunes postjournal:</p>
    """
    
    for sak in funn_liste:
        html_innhold += f"""
        <div style="border: 1px solid #ccc; padding: 15px; margin-bottom: 20px; border-radius: 5px; background-color: #f9f9f9;">
            <h4 style="margin: 0 0 10px 0; color: #d9534f;">Hvorfor: {sak['begrunnelse']}</h4>
            <p style="margin: 0; font-family: monospace; background-color: #e9ecef; padding: 10px; border-radius: 4px;">{sak['tekst']}</p>
            <div style="margin-top: 15px;">
                <a href="{URL_TIL_LISTEN}" style="display:inline-block; padding:8px 15px; background-color:#0056b3; color:white; text-decoration:none; border-radius:4px; font-weight: bold;">Gå til postjournalen</a>
            </div>
        </div>
        """

    html_innhold += "</div>"
    msg.add_alternative(html_innhold, subtype='html')

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EPOST_AVSENDER, EPOST_PASSORD)
            server.send_message(msg)
        print(f"Suksess: Sendte e-post med {len(funn_liste)} saker.")
    except Exception as e:
        print(f"Feil ved sending av e-post: {e}")

def finn_saker_via_innsynsknapp(driver):
    saker = []
    knapper = driver.find_elements(By.XPATH, "//*[contains(text(), 'Be om innsyn')]")
    unike_tekster = set()
    for knapp in knapper:
        try:
            element = knapp
            for i in range(6):
                try:
                    element = element.find_element(By.XPATH, "./..")
                    tekst = element.text.strip()
                    if "Dokumentnummer" in tekst and len(tekst) > 50:
                        tekst = tekst.replace("\n", " | ")
                        if tekst not in unike_tekster:
                            saker.append(tekst)
                            unike_tekster.add(tekst)
                        break
                except:
                    break
        except:
            continue
    return saker

def main():
    print("--- 🗞️ STARTER NYHETSROBOTEN ---")

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
    
    # Klargjør e-post konfigurasjon globalt slik at funksjonen når dem
    global SMTP_SERVER, SMTP_PORT
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587

    totalt_lest = 0
    alle_ai_funn = []

    try:
        print(f"--> Kobler til Sarpsborg kommune...")
        driver.get(URL_TIL_LISTEN)
        time.sleep(5)

        sett_ids = set()
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                sett_ids = set(f.read().splitlines())

        for side in range(1, MAKS_SIDER + 1):
            if side > 1:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                try:
                    kandidater = driver.find_elements(By.XPATH, f"//*[text()='{side}']")
                    for k in kandidater:
                        if k.tag_name != "td":
                            driver.execute_script("arguments[0].click();", k)
                            time.sleep(3)
                            break
                except:
                    break

            driver.execute_script("window.scrollBy(0, 500);")
            time.sleep(2)

            alle_saker = finn_saker_via_innsynsknapp(driver)
            totalt_lest += len(alle_saker)

            kandidater_til_ai = []
            for sak in alle_saker:
                fingeravtrykk = sak[:60]
                if fingeravtrykk not in sett_ids and grovfilter(sak):
                    kandidater_til_ai.append(sak)

                if fingeravtrykk not in sett_ids:
                    sett_ids.add(fingeravtrykk)
                    with open(SEEN_FILE, "a", encoding="utf-8") as f:
                        f.write(fingeravtrykk + "\n")

            print(f"\nSide {side}: Fant {len(alle_saker)} saker. {len(kandidater_til_ai)} sendes til AI-sjekk...", end="")

            if kandidater_til_ai:
                resultater = analyser_batch_med_gemini(kandidater_til_ai)
                if resultater:
                    alle_ai_funn.extend(resultater)
                    print(f" 🔥 FANT {len(resultater)} SAKER!")
                else:
                    print(" (Ingen nyheter her)", end="")
            else:
                print(" (Alt var kjedelig/sett før)", end="")

        # Når alle sidene er bladd gjennom, sendes e-posten hvis vi fant noe
        if alle_ai_funn:
            print(f"\nSender e-post med totalt {len(alle_ai_funn)} nyhetstips...")
            send_nyhetsvarsel_epost(alle_ai_funn)

    except Exception as e:
        print(f"\nKritisk feil: {e}")
    finally:
        driver.quit()

    print("\n" + "=" * 40)
    print(f"FERDIG! Lest {totalt_lest} dokumenter.")
    print(f"Fant {len(alle_ai_funn)} potensielle saker.")
    print("=" * 40)

if __name__ == "__main__":
    main()
