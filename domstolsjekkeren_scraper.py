import os
import json
import urllib.parse
import time
from datetime import datetime, timedelta
from pathlib import Path
import smtplib
from email.message import EmailMessage

import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

# --- KONFIGURASJON ---
DOMSTOL_ID = "AAAA2103101754092672012RXHZEG_EJBOrgUnit"  # Søndre Østfold tingrett
CACHE_FILE = Path("cache_sa.json")

# E-post konfigurasjon (Må fylles ut med SA sine detaljer)
SMTP_SERVER = "smtp.gmail.com"  # F.eks. smtp.gmail.com eller smtp.office365.com
SMTP_PORT = 587
EPOST_AVSENDER = os.environ.get("EPOST_BRUKER")  # Hentes fra miljøvariabel for sikkerhet
EPOST_PASSORD = os.environ.get("EPOST_PASSORD")  # App-passord
EPOST_MOTTAKER = "journalister@sa.no"  # Legg inn fellesadresse eller en liste med e-poster
TINGRETT_EPOST = "sondre.ostfold.tingrett@domstol.no"  # E-posten for innsynskrav


def les_cache():
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}


def skriv_cache(cache):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)


def send_epost_liste(nye_saker):
    if not nye_saker:
        return  # Ingen vits i å sende e-post hvis det ikke er noen nye saker

    msg = EmailMessage()
    msg['Subject'] = f"🚨 Nye TSAR-saker i Søndre Østfold tingrett ({len(nye_saker)})"
    msg['From'] = EPOST_AVSENDER
    msg['To'] = EPOST_MOTTAKER

    # Bygger innholdet i e-posten (HTML for at lenkene skal bli klikkbare)
    html_innhold = "<h2>Følgende nye saker med TSAR-endelse er lagt til i berammingslisten:</h2>"

    for sak in nye_saker:
        emne = f"Innsyn i sluttinnlegg - {sak['saksnr']}"
        innhold = f"Hei,\n\nSarpsborg Arbeiderblad ber om innsyn i sluttinnleggene i {sak['saksnr']}."

        # Lager en mailto-lenke for å generere e-posten til domstolen
        mailto_lenke = (
            f"mailto:{TINGRETT_EPOST}"
            f"?subject={urllib.parse.quote(emne)}"
            f"&body={urllib.parse.quote(innhold)}"
        )

        html_innhold += f"""
        <div style="border: 1px solid #ccc; padding: 10px; margin-bottom: 15px; border-radius: 5px;">
            <p><strong>Rettsmøte:</strong> {sak['rettsmoete']}</p>
            <p><strong>Saksnr:</strong> {sak['saksnr']}</p>
            <p><strong>Saken gjelder:</strong> {sak['saken_gjelder']}</p>
            <p><strong>Parter:</strong> {sak['parter']}</p>
            <p>
                <a href="{sak['sakslenke']}" style="display:inline-block; padding:8px 12px; background-color:#0056b3; color:white; text-decoration:none; border-radius:4px;">Åpne saken hos domstol.no</a>
                <a href="{mailto_lenke}" style="display:inline-block; padding:8px 12px; background-color:#28a745; color:white; text-decoration:none; border-radius:4px; margin-left: 10px;">Opprett innsynskrav</a>
            </p>
        </div>
        """

    msg.add_alternative(html_innhold, subtype='html')

    # Sender e-posten
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EPOST_AVSENDER, EPOST_PASSORD)
            server.send_message(msg)
        print(f"Sendte e-post med {len(nye_saker)} saker.")
    except Exception as e:
        print(f"Feil ved sending av e-post: {e}")


def main():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)
    sendte_varsler = les_cache()

    i_dag_str = datetime.now().strftime("%Y-%m-%d")
    # Genererer dynamisk URL fra dagens dato og setter pageSize høyere for sikkerhets skyld
    url = f"https://www.domstol.no/no/nar-gar-rettssaken/?fraDato={i_dag_str}&domstolid={DOMSTOL_ID}&sortTerm=rettsmoete&sortAscending=true&pageSize=1000"

    funnet_saker = []

    try:
        driver.get(url)
        time.sleep(10)  # Venter på at JavaScript skal laste tabellen

        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))

        rader = driver.find_elements(By.CSS_SELECTOR, "table tr")[1:]
        i_dag = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        grense = i_dag + timedelta(days=14)

        for rad in rader:
            cols = rad.find_elements(By.TAG_NAME, "td")
            if len(cols) < 5: continue

            saksnr_celle = cols[1]
            saksnr = saksnr_celle.text.strip()
            rettsmoete_full = cols[0].text.strip()
            dato_str = rettsmoete_full.split()[0]

            cache_id = f"{saksnr}_{dato_str}"

            # FILTRERER PÅ "TSAR"
            if saksnr.endswith("TSAR") and cache_id not in sendte_varsler:
                try:
                    try:
                        lenke_element = saksnr_celle.find_element(By.TAG_NAME, "a")
                        sakslenke = lenke_element.get_attribute("href")
                    except:
                        sakslenke = url

                    sak_dato = datetime.strptime(dato_str, "%d.%m.%Y")

                    if i_dag <= sak_dato <= grense:
                        funnet_saker.append({
                            'rettsmoete': rettsmoete_full,
                            'saksnr': saksnr,
                            'domstol': cols[2].text.strip(),
                            'saken_gjelder': cols[3].text.strip(),
                            'parter': cols[4].text.strip(),
                            'sakslenke': sakslenke
                        })
                        sendte_varsler[cache_id] = datetime.now().isoformat()
                except Exception as e:
                    print(f"Feil ved parsing av rad for {saksnr}: {e}")

        # Sender e-post om vi fant nye saker i dag
        if funnet_saker:
            send_epost_liste(funnet_saker)
            skriv_cache(sendte_varsler)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
