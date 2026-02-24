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
from selenium.common.exceptions import TimeoutException

# --- KONFIGURASJON ---
DOMSTOL_ID = "AAAA2103101754092672012RXHZEG_EJBOrgUnit" # Søndre Østfold tingrett
CACHE_FILE = Path("cache_sa.json")

# E-post konfigurasjon
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Henter innloggingsdetaljer fra miljøvariabler (GitHub Secrets)
EPOST_AVSENDER = os.environ.get("EPOST_BRUKER") 
EPOST_PASSORD = os.environ.get("EPOST_PASSORD") 
EPOST_MOTTAKER = os.environ.get("EPOST_MOTTAKER", "ocb@sa.no, tina@sa.no") 
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
    msg['Subject'] = f"🚨 Nye Sarpsborg-saker i Søndre Østfold tingrett ({len(nye_saker)})"
    msg['From'] = EPOST_AVSENDER
    msg['To'] = EPOST_MOTTAKER

    html_innhold = """
    <div style="font-family: Arial, sans-serif; color: #333;">
        <h2>Følgende nye saker fra Sarpsborg er lagt til i berammingslisten:</h2>
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
            <div style="overflow: auto;">
                <a href="{sak['sakslenke']}" style="display:inline-block; padding:10px 15px; background-color:#0056b3; color:white; text-decoration:none; border-radius:4px; font-weight: bold;">Åpne saken hos domstol.no</a>
                <a href="{mailto_lenke}" style="display:inline-block; padding:10px 15px; background-color:#28a745; color:white; text-decoration:none; border-radius:4px; margin-left: 10px; font-weight: bold;">Opprett innsynskrav i e-post</a>
                <a href="https://presse.domstol.no/" style="display:inline-block; padding:8px 15px; background-color:#e9ecef; color:#0056b3; text-decoration:none; border-radius:4px; font-weight: bold; border: 2px solid #0056b3; float: right;">Se saken i presseportalen</a>
            </div>
            <div style="clear: both;"></div>
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
        print("ADVARSEL: EPOST_BRUKER eller EPOST_PASSORD mangler i miljøvariablene.")

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
    sendte_varsler = les_cache()
    
    i_dag = datetime.now()
    grense = i_dag + timedelta(days=14)
    
    fra_dato_str = i_dag.strftime("%Y-%m-%d")
    til_dato_str = grense.strftime("%Y-%m-%d")
    
    url = f"https://www.domstol.no/no/nar-gar-rettssaken/?fraDato={fra_dato_str}&tilDato={til_dato_str}&domstolid={DOMSTOL_ID}&sortTerm=rettsmoete&sortAscending=true&pageSize=100&query=TSAR"
    
    funnet_saker = []

    try:
        print(f"Henter saker fra: {url}")
        driver.get(url)
        time.sleep(5)
        
        try:
            wait = WebDriverWait(driver, 15)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        except TimeoutException:
            print("Fant ingen tabell på siden. Dette betyr at det ikke er noen TSAR-saker i denne perioden.")
            return 

        rader = driver.find_elements(By.CSS_SELECTOR, "table tr")[1:]
        print(f"Fant {len(rader)} rader i tabellen. Sjekker for nye saker...")
        
        for rad in rader:
            cols = rad.find_elements(By.TAG_NAME, "td")
            if len(cols) < 5: continue
            
            saksnr_celle = cols[1]
            saksnr = saksnr_celle.text.strip()
            rettsmoete_full = cols[0].text.strip()
            
            try:
                dato_str = rettsmoete_full.split()[0]
                sak_dato = datetime.strptime(dato_str, "%d.%m.%Y")
            except Exception:
                continue
            
            cache_id = f"{saksnr}_{dato_str}"
            
            if saksnr.endswith("TSAR") and cache_id not in sendte_varsler:
                try:
                    try:
                        lenke_element = saksnr_celle.find_element(By.TAG_NAME, "a")
                        sakslenke = lenke_element.get_attribute("href")
                    except:
                        sakslenke = url

                    if i_dag.date() <= sak_dato.date() <= grense.date():
                        print(f"Fant ny Sarpsborg-sak: {saksnr}")
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
            print("Ingen NYE Sarpsborg-saker funnet i dag (de som lå der var allerede varslet om).")
            
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
