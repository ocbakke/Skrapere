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
EPOST_MOTTAKER = os.environ.get("EPOST_MOTTAKER", "ocb@sa.no") 
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
