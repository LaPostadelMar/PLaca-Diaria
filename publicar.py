#!/usr/bin/env python3
"""Publica la placa de hoy como historia de Instagram (API oficial de Meta)."""
import os, sys, time, datetime as dt
from zoneinfo import ZoneInfo
import requests

VERSION = "v26.0"                                   # versión de la API de Meta
BASE = f"https://graph.instagram.com/{VERSION}"
TOKEN = os.environ["IG_ACCESS_TOKEN"]
USER_ID = os.environ.get("IG_USER_ID", "").strip() or "me"
HEAD = {"Authorization": f"Bearer {TOKEN}"}

hoy = dt.datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date()
repo = os.environ["GITHUB_REPOSITORY"]
rama = os.environ.get("GITHUB_REF_NAME", "main")
url = f"https://raw.githubusercontent.com/{repo}/{rama}/placas/placa-{hoy.isoformat()}.jpg"


def fallar(msg, r=None):
    print("ERROR:", msg)
    if r is not None:
        print("Respuesta de Instagram:", r.status_code, r.text[:800])
    sys.exit(1)


# 1) esperar a que la imagen ya sea visible públicamente (Instagram la descarga desde esa URL)
for _ in range(12):
    if requests.head(url, timeout=20, allow_redirects=True).status_code == 200:
        break
    time.sleep(10)
else:
    fallar(f"La imagen no está disponible en {url}. ¿El repositorio es público?")

# 2) crear el contenedor de la historia
r = requests.post(f"{BASE}/{USER_ID}/media", headers=HEAD,
                  data={"image_url": url, "media_type": "STORIES"}, timeout=60)
if r.status_code != 200:
    fallar("No se pudo crear la historia.", r)
contenedor = r.json()["id"]

# 3) esperar a que Instagram la procese
for _ in range(20):
    s = requests.get(f"{BASE}/{contenedor}", headers=HEAD, params={"fields": "status_code"}, timeout=30).json()
    estado = s.get("status_code")
    if estado == "FINISHED":
        break
    if estado in ("ERROR", "EXPIRED"):
        fallar(f"Instagram rechazó la imagen: {s}")
    time.sleep(5)
else:
    fallar("Instagram tardó demasiado en procesar la imagen.")

# 4) publicar
if os.environ.get("DRY_RUN") == "1":
    print("Modo prueba: no se publica. Todo OK hasta acá.")
    sys.exit(0)
r = requests.post(f"{BASE}/{USER_ID}/media_publish", headers=HEAD,
                  data={"creation_id": contenedor}, timeout=60)
if r.status_code != 200:
    fallar("No se pudo publicar la historia.", r)
print("Historia publicada:", r.json())
