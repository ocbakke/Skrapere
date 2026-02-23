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
DOMSTOL_ID = "AAAA2103101754092672012RXHZEG_EJBOrgUnit" # Søndre Østfold tingrett
CACHE_FILE = Path("cache_sa.json")

# E-post konfigurasjon
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Henter innloggingsdetaljer fra miljøvariabler (GitHub Secrets)
EPOST_AVSENDER = os.environ.get("EPOST_BRUKER") 
EPOST_PASSORD = os.environ.get("EPOST_PASSORD") 

# Hvem skal motta varslene? 
EPOST_MOTTAKER = os.environ.get("EPOST_MOTTAKER", "redaksjonen@sa.no") 

# E-posten til domstolen for innsynskrav
TINGRETT_EPOST = "sondre.ostfold.tingrett@domstol.no" 

def les_cache():
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except: return {}
    return {}

def skriv_cache(cache):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

def send_epost_liste(nye_saker):
    if not nye_saker:
        return

    msg = EmailMessage()
    msg['Subject'] = f"🚨 Nye TSAR-saker i Søndre Østfold tingrett ({len(nye_saker)})"
    
    # Sendes rett fra din e-post, med ditt vanlige navn
    msg['From'] = EPOST_AVSENDER
    msg['To'] = EPOST_MOTTAKER

    # Bygger innholdet i e-posten (HTML)
    html_innhold = """
    <div style="font-family: Arial, sans-serif; color: #333;">
        <h2>Følgende nye saker med TSAR-endelse er lagt til i berammingslisten:</h2>
    """
    
    for sak in nye_saker:
        emne = f"Innsyn i sluttinnlegg - {sak['saksnr']}"
        innhold = f"Hei,\n\nSarpsborg Arbeiderblad ber om innsyn i sluttinnleggene i {sak['saksnr']}."
        
        mailto_lenke = (
            f"mailto:{TINGRETT_EPOST}"
            f"?subject={urllib.parse.quote(emne)}"
            f"&body={urllib.parse.quote(innhold)}"
        )

        html_innhold += f"""
        <div style="border: 1px solid #ccc; padding: 15px; margin-bottom: 20px; border-radius: 5px; background-color: #f9f9f9;">
            <p style="margin: 0 0 10px 0;"><strong>Rettsmøte:</strong> {sak['rettsmoete']}</p>
            <p style="margin: 0 0 10px 0;"><strong>Saksnr:</strong> {sak['saksnr']}</p>
            <p style="margin: 0 0 10px 0;"><strong>Saken gjelder:</strong> {sak['saken_gjelder']}</p>
            <p style="margin: 0 0 15px 0;"><strong>Parter:</strong> {sak['parter']}</p>
            <div>
                <a href="{sak['sakslenke']}" style="display:inline-block; padding:10px 15px; background-color:#0056b3; color:white; text-decoration:none; border-radius:4px; font-weight: bold;">Åpne saken hos domstol.no</a>
                <a href="{mailto_lenke}" style="display:inline-block; padding:10px 15px; background-color:#28a745; color:white; text-decoration:none; border-radius:4px; margin-left: 10px; font-weight: bold;">Opprett innsynskrav i e-post</a>
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
        print(f"Suksess: Sendte e-post med {len(nye_saker)} saker til {EPOST_MOTTAKER}.")
    except Exception as e:
        print(f"Feil ved sending av e-post: {e}")

def main():
    if not EPOST_AVSENDER or not EPOST_PASSORD:
        print("ADVARSEL: EPOST_BRUKER eller EPOST_PASSORD mangler i miljøvariablene. E-post vil feile.")

    # --- NYE CHROME-INNSTILLINGER FOR Å UNNGÅ BLOKKERING ---
    options = Options()
    options.add_argument("--headless=new") # Ny headless-modus som er vanskeligere å oppdage
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080") # Tvinger PC-skjerm så tabellen faktisk vises
    options.add_argument("--disable-blink-features=AutomationControlled") # Skjuler at det er en robot
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    sendte_varsler = les_cache()
    
    i_dag_str = datetime.now().strftime("%Y-%m-%d")
    url = f"https://www.domstol.no/no/nar-gar-rettssaken/?fraDato={i_dag_str}&domstolid={DOMSTOL_ID}&sortTerm=rettsmoete&sortAscending=true&pageSize=1000"
    
    funnet_saker = []

    try:
        print(f"Henter saker fra: {url}")
        driver.get(url)
        time.sleep(10) # Venter 10 sek for å la JavaScript bygge tabellen
        
        # Venter til selve tabellen dukker opp i HTML-en (maks 30 sekunder)
        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        
        rader = driver.find_elements(By.CSS_SELECTOR, "table tr")[1:]
        i_dag = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        grense = i_dag + timedelta(days=14)
        
        print(f"Fant {len(rader)} rader i tabellen. Starter filtrering...")
        
        for rad in rader:
            cols = rad.find_elements(By.TAG_NAME, "td")
            if len(cols) < 5: continue
            
            saksnr_celle = cols[1]
            saksnr = saksnr_celle.text.strip()
            rettsmoete_full = cols[0].text.strip()
            
            try:
                dato_str = rettsmoete_full.split()[0]
                sak_dato = datetime.strptime(dato_str, "%d.%m.%Y")
            except Exception as e:
                continue
            
            cache_id = f"{saksnr}_{dato_str}"
            
            if saksnr.endswith("TSAR") and cache_id not in sendte_varsler:
                try:
                    try:
                        lenke_element = saksnr_celle.find_element(By.TAG_NAME, "a")
                        sakslenke = lenke_element.get_attribute("href")
                    except:
                        sakslenke = url

                    if i_dag <= sak_dato <= grense:
                        print(f"Fant ny TSAR-sak: {saksnr}")
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
                    
        if funnet_saker:
            send_epost_liste(funnet_saker)
            skriv_cache(sendte_varsler)
        else:
            print("Ingen nye TSAR-saker funnet i dag.")
            
    except Exception as e:
        print(f"Kritisk feil under kjøring: {e}")
        raise e
            
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
