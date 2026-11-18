#!/usr/bin/env python3
"""Publica la placa de hoy como historia de Instagram (API oficial de Meta).

Reconoce solo dos tipos de token:
  - empieza con "IG": token de Instagram  -> graph.instagram.com
  - cualquier otro (ej. "EAA"): token de Facebook -> graph.facebook.com
"""
import os, sys, time, datetime as dt
from zoneinfo import ZoneInfo
import requests

VERSION = "v26.0"                                   # versión de la API de Meta


def fallar(msg, r=None):
    print("ERROR:", msg)
    if r is not None:
        print("Respuesta de Instagram:", r.status_code, r.text[:800])
    sys.exit(1)


# El token se limpia por si se pegó con espacios, saltos de línea o comillas de más
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip().strip("\"'").strip()
if TOKEN.lower().startswith("bearer "):
    TOKEN = TOKEN[7:]
TOKEN = "".join(TOKEN.split())
print(f"Diagnóstico del token: {len(TOKEN)} caracteres, empieza con '{TOKEN[:4]}'")
if not TOKEN:
    fallar("El secreto IG_ACCESS_TOKEN está vacío o no existe en el repositorio.")

ES_INSTAGRAM = TOKEN.startswith("IG")
BASE = f"https://graph.instagram.com/{VERSION}" if ES_INSTAGRAM else f"https://graph.facebook.com/{VERSION}"
print("Tipo de token:", "Instagram" if ES_INSTAGRAM else "Facebook")
HEAD = {"Authorization": f"Bearer {TOKEN}"}

hoy = dt.datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date()
repo = os.environ["GITHUB_REPOSITORY"]
rama = os.environ.get("GITHUB_REF_NAME", "main")
url = f"https://raw.githubusercontent.com/{repo}/{rama}/placas/placa-{hoy.isoformat()}.jpg"


def mostrar_permisos():
    """Para diagnóstico: lista los permisos que tiene el token (no muestra datos personales)."""
    try:
        r = requests.get(f"{BASE}/me/permissions", headers=HEAD, timeout=30)
        if r.ok:
            dados = sorted(p["permission"] for p in r.json().get("data", []) if p.get("status") == "granted")
            print("Permisos del token:", ", ".join(dados) or "(ninguno)")
        else:
            print("No pude leer los permisos del token:", r.status_code, r.text[:300])
    except Exception as e:
        print("No pude leer los permisos del token:", type(e).__name__)


def buscar_cuenta():
    """Devuelve el identificador de la cuenta de Instagram a la que publicar."""
    manual = "".join(os.environ.get("IG_USER_ID", "").split())
    if not ES_INSTAGRAM:
        mostrar_permisos()
    if manual:
        print("Uso la cuenta de Instagram indicada en el secreto IG_USER_ID.")
        return manual
    if ES_INSTAGRAM:
        return "me"
    # token de Facebook: buscar la cuenta de Instagram vinculada a la página
    r = requests.get(f"{BASE}/me", headers=HEAD, params={"fields": "id,instagram_business_account"}, timeout=30)
    if r.ok and r.json().get("instagram_business_account"):          # token de página
        return r.json()["instagram_business_account"]["id"]
    r = requests.get(f"{BASE}/me/accounts", headers=HEAD,
                     params={"fields": "name,instagram_business_account"}, timeout=30)
    if not r.ok:
        fallar("No pude leer las páginas de Facebook con este token.", r)
    for pagina in r.json().get("data", []):
        if pagina.get("instagram_business_account"):
            print("Página de Facebook vinculada encontrada:", pagina.get("name"))
            return pagina["instagram_business_account"]["id"]
    fallar("El token no ve ninguna página de Facebook con Instagram vinculado. "
           "Cargá el número de la cuenta de Instagram como secreto IG_USER_ID.", r)


USER_ID = buscar_cuenta()

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
