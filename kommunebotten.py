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
API_KEY = os.environ.get("GEMINI_API_KEY") 
EPOST_AVSENDER = os.environ.get("EPOST_BRUKER") 
EPOST_PASSORD = os.environ.get("EPOST_PASSORD") 
EPOST_MOTTAKER = os.environ.get("EPOST_MOTTAKER", "ocb@sa.no, redaksjonen@sa.no, johnny.helgesen@sa.no") 

URL_TIL_LISTEN = "https://sarpsborg.pj.360online.com/"
SEEN_FILE = "sette_dokumenter.txt"
MAKS_SIDER = 20
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Debug-info for GitHub Actions loggen
print(f"DEBUG: API-nøkkel funnet: {'JA' if API_KEY else 'NEI'}")

try:
    if API_KEY:
        client = genai.Client(api_key=API_KEY)
    else:
        client = None
except Exception as e:
    print(f"FEIL: Klarte ikke starte AI-klienten. ({e})")
    client = None

def grovfilter(tekst):
    """Siler ut åpenbart kjedelige ting før AI-en får se dem."""
    kjedelige_ord = ["avslag søknad", "parkering", "skjenkebevilling", "ferdigattest", "igangsetting", "sanitær", "feilsortert avfall", "ekstratømming", "gebyr", "startlån", "tilleggslån", "motorferdsel", "vigsel", "elevpermisjon", "elevmappe", "ledsagerbevis"]
    return not any(ordet in tekst.lower() for ordet in kjedelige_ord)

def analyser_batch_med_gemini(liste_med_saker):
    if not client or not liste_med_saker: 
        return []
    
    tekst_blokk = ""
    for i, sak in enumerate(liste_med_saker):
        tekst_blokk += f"ID {i}: {sak}\n"

    # OPPDATERT PROMPT: Ber Gemini trekke ut feltene og skrive dem med | (pipes)
    prompt = f"""Du er en erfaren nyhetsjournalist i lokalavisen Sarpsborg Arbeiderblad. Her er en liste over nye dokumenter fra postjournalen i Sarpsborg kommune.
    Din oppgave er å lese gjennom og plukke ut de som er nyhetsverdige.

    Kriterier for TREFF:
    - Konflikter, klager, lovbrudd, tvangsmulkt, trusler.
    - Konkurs, store pengesummer, erstatningskrav.
    - Alvorlig kritikk fra tilsyn (Statsforvalter, Arbeidstilsyn).
    - Politiske stridstemaer, varslingssaker, habilitet.
    - "Unntatt offentlighet" hvis tittelen virker interessant, men ikke dersom sakene omhandler elevmapper.
    - Svar på søknader og ansettelsesprosesser, er som hovedregel uinteressant, med mindre det er snakk om lederstillinger i kommunen.

    Her er listen:
    {tekst_blokk}

    SVAR KUN SLIK (for hver sak du finner, formater nøyaktig slik på én linje):
    ID [nummer]: Begrunnelse: [Din begrunnelse] | Saksnummer: [Saksnummer] | Sak: [Sak] | Dokumentnavn: [Dokumentnavn] | Mottaker: [Mottaker eller Avsender] | Journaldato: [Dato]"""

    try:
        response = client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
        funn = []
        if response.text:
            print(f"DEBUG: AI vurderte {len(liste_med_saker)} saker.")
            for linje in response.text.split("\n"):
                if "ID" in linje and ":" in linje:
                    try:
                        id_num = int(linje.split(":")[0].replace("ID", "").strip())
                        # Henter hele den ferdigformaterte strengen etter "ID X:"
                        formatert_tekst = linje.split(":", 1)[1].strip() 
                        if 0 <= id_num < len(liste_med_saker):
                            funn.append(formatert_tekst)
                    except: continue
        return funn
    except Exception as e:
        print(f"⚠️ AI-feil: {e}")
        return []

def send_nyhetsvarsel_epost(funn_liste):
    msg = EmailMessage()
    msg['Subject'] = f"🤖 AI-Tips: {len(funn_liste)} nyhetsverdige saker i postjournalen"
    msg['From'] = EPOST_AVSENDER
    msg['To'] = EPOST_MOTTAKER

    html_innhold = f"""
    <div style="font-family: Arial, sans-serif;">
        <h2 style="color: #d9534f;">AI-roboten har funnet potensielle nyheter! 🗞️</h2>
        <p>Her er de utvalgte dokumentene fra Sarpsborg kommune:</p>
    """
    
    # OPPDATERT: Nå spytter e-posten bare ut den pene, formaterte strengen Gemini har laget.
    for sak in funn_liste:
        html_innhold += f"""
        <div style="border-left: 5px solid #d9534f; padding: 10px; margin-bottom: 20px; background-color: #f9f9f9;">
            <p style="font-family: Arial, sans-serif; font-size: 14px; color: #333; line-height: 1.6; margin: 0;">
                {sak}
            </p>
        </div>
        """
    html_innhold += "</div>"
    
    msg.add_alternative(html_innhold, subtype='html')

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EPOST_AVSENDER, EPOST_PASSORD)
            server.send_message(msg)
            print("DEBUG: E-post sendt!")
    except Exception as e: 
        print(f"DEBUG: E-post feil: {e}")

def finn_saker_via_innsynsknapp(driver):
    saker = []
    knapper = driver.find_elements(By.XPATH, "//*[contains(text(), 'Be om innsyn')]")
    for knapp in knapper:
        try:
            # LØSNINGEN: Vi prøver først å hente kun tabellraden (tr) knappen ligger i.
            # Dette forhindrer at vi får med hele nettsiden i ett dokument.
            rad = knapp.find_element(By.XPATH, "./ancestor::tr")
            saker.append(rad.text.replace("\n", " | "))
        except:
            # Fallback hvis nettsiden endrer kode (klatrer maks 3 hakk i stedet for 6)
            try:
                element = knapp
                for _ in range(3):
                    element = element.find_element(By.XPATH, "./..")
                    if "Dokumentnummer" in element.text:
                        saker.append(element.text.replace("\n", " | "))
                        break
            except: continue
    return list(set(saker))

def main():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    
    alle_ai_funn = []
    try:
        print(f"DEBUG: Åpner {URL_TIL_LISTEN}")
        driver.get(URL_TIL_LISTEN)
        time.sleep(7)
        
        gamle_ids = []
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                gamle_ids = [l.strip() for l in f.readlines() if l.strip()]
        
        sett_ids_oppslag = set(gamle_ids)
        nye_ids = []

        for side in range(1, MAKS_SIDER + 1):
            if side > 1:
                try:
                    btn = driver.find_element(By.XPATH, f"//*[text()='{side}' and not(contains(@class, 'active'))]")
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(5)
                except: break

            saker = finn_saker_via_innsynsknapp(driver)
            kandidater = []
            for s in saker:
                fid = s[:90] # Nå vil dette fungere mye bedre fordi dokumentene er skilt fra hverandre
                if fid not in sett_ids_oppslag and grovfilter(s):
                    kandidater.append(s)
                    sett_ids_oppslag.add(fid)
                    nye_ids.append(fid)

            if kandidater:
                alle_ai_funn.extend(analyser_batch_med_gemini(kandidater))

        total_liste = (gamle_ids + nye_ids)[-500:]
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            for i in total_liste: f.write(i + "\n")

        if alle_ai_funn:
            send_nyhetsvarsel_epost(alle_ai_funn)
        else:
            print("DEBUG: Ingen nyhetstips funnet i denne runden.")

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
