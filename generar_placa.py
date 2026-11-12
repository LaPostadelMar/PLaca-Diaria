#!/usr/bin/env python3
"""Genera la placa diaria del tiempo de La Posta del Mar (1080x1920, formato historia)."""
import os, sys, math, time, argparse, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

AQUI = os.path.dirname(os.path.abspath(__file__))
TZ_NOMBRE = "America/Argentina/Buenos_Aires"
TZ = ZoneInfo(TZ_NOMBRE)
LAT, LON = -37.1084, -56.8577          # Pinamar
MAR_LAT, MAR_LON = -37.15, -56.80      # punto en el mar frente a Pinamar (para las mareas)
MAREAS_ESTIMADAS = True                # la placa aclara "estimadas"
API_KEY = os.environ.get("OPEN_METEO_API_KEY", "").strip()   # opcional (plan comercial)

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
PUNTOS = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]


# ---------------------------------------------------------------- datos
def open_meteo(host, ruta, params):
    prefijo = "customer-" if API_KEY else ""
    if API_KEY:
        params["apikey"] = API_KEY
    for intento in range(3):
        try:
            r = requests.get(f"https://{prefijo}{host}.open-meteo.com{ruta}", params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if intento == 2:
                raise
            time.sleep(5)


def clima(hoy):
    j = open_meteo("api", "/v1/forecast", {
        "latitude": LAT, "longitude": LON, "timezone": TZ_NOMBRE,
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "kmh", "start_date": hoy.isoformat(), "end_date": hoy.isoformat()})
    h = j["hourly"]
    horas = [dt.datetime.fromisoformat(t) for t in h["time"]]
    tmax, tmin = max(h["temperature_2m"]), min(h["temperature_2m"])
    dia = [i for i, t in enumerate(horas) if 10 <= t.hour <= 19]      # horario de playa
    vel = [h["wind_speed_10m"][i] for i in dia]
    ang = [math.radians(h["wind_direction_10m"][i]) for i in dia]
    u = sum(v * math.sin(a) for v, a in zip(vel, ang))
    w = sum(v * math.cos(a) for v, a in zip(vel, ang))
    grados = math.degrees(math.atan2(u, w)) % 360
    return {"tmax": tmax, "tmin": tmin, "viento_kmh": sum(vel) / len(vel),
            "viento_dir": PUNTOS[round(grados / 45) % 8]}


def extremos(tiempos, alturas):
    """Pleamares y bajamares a partir de alturas horarias (con interpolación parabólica)."""
    out = []
    for i in range(1, len(alturas) - 1):
        a, b, c = alturas[i - 1], alturas[i], alturas[i + 1]
        if None in (a, b, c):
            continue
        if b > a and b >= c:
            tipo = "alta"
        elif b < a and b <= c:
            tipo = "baja"
        else:
            continue
        den = a - 2 * b + c
        desplaz = 0.5 * (a - c) / den if den else 0
        out.append((tiempos[i] + dt.timedelta(hours=desplaz), tipo))
    return out


def mareas(hoy):
    j = open_meteo("marine-api", "/v1/marine", {
        "latitude": MAR_LAT, "longitude": MAR_LON, "timezone": TZ_NOMBRE,
        "hourly": "sea_level_height_msl", "past_days": 1, "forecast_days": 2})
    t = [dt.datetime.fromisoformat(x) for x in j["hourly"]["time"]]
    v = j["hourly"]["sea_level_height_msl"]
    ex = [(m, tipo) for m, tipo in extremos(t, v) if m.date() == hoy]
    altas = [m.strftime("%H:%M") for m, tipo in ex if tipo == "alta"]
    bajas = [m.strftime("%H:%M") for m, tipo in ex if tipo == "baja"]
    if not altas and not bajas:
        return None
    return {"pleamar": " · ".join(altas) or "—", "bajamar": " · ".join(bajas) or "—"}


def puesta_de_sol(fecha, lat=LAT, lon=LON):
    """Hora local de la puesta del sol (fórmulas astronómicas NOAA, sin internet)."""
    n = fecha.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    dec = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
           + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    ha = math.degrees(math.acos(math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(dec))
                                - math.tan(la) * math.tan(dec)))
    minutos_utc = 720 - 4 * (lon - ha) - eqt
    base = dt.datetime(fecha.year, fecha.month, fecha.day, tzinfo=dt.timezone.utc)
    return (base + dt.timedelta(minutes=minutos_utc)).astimezone(TZ)


# ---------------------------------------------------------------- imagen
S = 2
W, H = 1080 * S, 1920 * S


def font(peso, tam):
    return ImageFont.truetype(os.path.join(AQUI, "assets", "fonts", f"Poppins-{peso}.ttf"), int(tam * S))


def dibujar(datos, salida):
    stops = [(0.0, (11, 60, 93)), (0.55, (31, 122, 140)), (1.0, (242, 166, 90))]
    ys = np.linspace(0, 1, H)[:, None]
    arr = np.zeros((H, 1, 3))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        m = (ys >= p0) & (ys <= p1)
        t = (ys - p0) / (p1 - p0)
        for k in range(3):
            arr[:, :, k] = np.where(m, c0[k] + (c1[k] - c0[k]) * t, arr[:, :, k])
    bg = Image.fromarray(np.repeat(arr, W, axis=1).astype("uint8")).convert("RGBA")

    logo = Image.open(os.path.join(AQUI, "assets", "logo.png")).convert("RGBA")
    a = np.array(logo).astype(float)
    mask = ((255 - a[..., :3].mean(axis=2)) / 255 * a[..., 3]).clip(0, 255).astype("uint8")
    blanco = Image.new("RGBA", logo.size, (255, 255, 255, 255))
    blanco.putalpha(Image.fromarray(mask))
    ls = 430 * S
    blanco = blanco.resize((ls, ls), Image.LANCZOS)
    bg.alpha_composite(blanco, ((W - ls) // 2, 225 * S))

    d = ImageDraw.Draw(bg)

    def centrado(txt, y, f, fill=(255, 255, 255), esp=0):
        if esp:
            tw = sum(d.textlength(c, font=f) for c in txt) + esp * S * (len(txt) - 1)
            x = (W - tw) / 2
            for c in txt:
                d.text((x, y * S), c, font=f, fill=fill)
                x += d.textlength(c, font=f) + esp * S
        else:
            d.text(((W - d.textlength(txt, font=f)) / 2, y * S), txt, font=f, fill=fill)

    centrado("EL TIEMPO HOY", 664, font("Medium", 30), (255, 255, 255, 220), esp=8)
    centrado(datos["fecha"], 708, font("Bold", 56))

    x0, cw, ch, gap, y0 = 80, 920, 190, 20, 800
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for i in range(4):
        y = y0 + i * (ch + gap)
        od.rounded_rectangle([x0 * S, y * S, (x0 + cw) * S, (y + ch) * S], radius=44 * S, fill=(8, 34, 58, 120))
    bg.alpha_composite(ov)
    d = ImageDraw.Draw(bg)
    blanco = (255, 255, 255)

    def ajustada(txt, peso, tam, ancho=690):
        while tam > 20 and d.textlength(txt, font=font(peso, tam)) > ancho * S:
            tam -= 2
        return font(peso, tam)

    def icono(tipo, cx, cy):
        cx *= S; cy *= S; lw = 6 * S
        if tipo == "temp":
            d.rounded_rectangle([cx - 9 * S, cy - 46 * S, cx + 9 * S, cy + 20 * S], radius=9 * S, outline=blanco, width=lw)
            d.ellipse([cx - 22 * S, cy + 6 * S, cx + 22 * S, cy + 50 * S], fill=blanco)
            d.rectangle([cx - 4 * S, cy - 10 * S, cx + 4 * S, cy + 30 * S], fill=blanco)
        elif tipo == "viento":
            r = 13 * S
            for k, (dy, ex) in enumerate([(-26, 25), (0, 40), (26, 10)]):
                y = cy + dy * S; e = cx + ex * S
                d.line([cx - 50 * S, y, e, y], fill=blanco, width=lw)
                if k < 2:
                    d.arc([e - r, y - 2 * r, e + r, y], -180, 90, fill=blanco, width=lw)
                else:
                    d.arc([e - r, y, e + r, y + 2 * r], -90, 180, fill=blanco, width=lw)
        elif tipo == "marea":
            for dy in (-14, 18):
                pts = [(cx - 48 * S + t * S, cy + dy * S + math.sin(t / 96 * 2 * math.pi * 1.5) * 10 * S) for t in range(0, 97)]
                d.line(pts, fill=blanco, width=lw, joint="curve")
        elif tipo == "sol":
            d.arc([cx - 30 * S, cy - 24 * S, cx + 30 * S, cy + 36 * S], 180, 360, fill=blanco, width=lw)
            d.line([cx - 52 * S, cy + 6 * S, cx + 52 * S, cy + 6 * S], fill=blanco, width=lw)
            d.line([cx - 30 * S, cy + 30 * S, cx + 30 * S, cy + 30 * S], fill=blanco, width=lw)
            for ang in (-90, -45, -135):
                c, s_ = math.cos(math.radians(ang)), math.sin(math.radians(ang))
                d.line([cx + c * 42 * S, cy + 6 * S + s_ * 42 * S, cx + c * 58 * S, cy + 6 * S + s_ * 58 * S], fill=blanco, width=lw)

    etiqueta_marea = "MAREAS (ESTIMADAS)" if MAREAS_ESTIMADAS else "MAREAS"
    tarjetas = [("temp", "TEMPERATURA"), ("viento", "VIENTO"), ("marea", etiqueta_marea), ("sol", "PUESTA DEL SOL")]
    for i, (tipo, etiqueta) in enumerate(tarjetas):
        y = y0 + i * (ch + gap)
        icono(tipo, x0 + 95, y + ch / 2)
        tx = (x0 + 190) * S
        d.text((tx, (y + 26) * S), etiqueta, font=font("Medium", 28), fill=(255, 255, 255, 215))
        if tipo == "marea":
            m = datos["mareas"]
            if m:
                for dy, txt in ((66, "Pleamar  " + m["pleamar"]), (120, "Bajamar  " + m["bajamar"])):
                    d.text((tx, (y + dy) * S), txt, font=ajustada(txt, "Bold", 42), fill=blanco)
            else:
                d.text((tx, (y + 78) * S), "No disponible hoy", font=font("Bold", 46), fill=blanco)
        else:
            txt = datos[tipo]
            d.text((tx, (y + 68) * S), txt, font=ajustada(txt, "Bold", 70), fill=blanco)

    bg.convert("RGB").resize((W // S, H // S), Image.LANCZOS).save(salida, "JPEG", quality=95)


# ---------------------------------------------------------------- principal
def fecha_texto(f):
    return f"{DIAS[f.weekday()]} {f.day} de {MESES[f.month - 1]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="usa datos de ejemplo, sin internet")
    ap.add_argument("--fecha", help="AAAA-MM-DD (por defecto, hoy en Argentina)")
    args = ap.parse_args()
    hoy = dt.date.fromisoformat(args.fecha) if args.fecha else dt.datetime.now(TZ).date()

    if args.demo:
        c = {"tmax": 27.2, "tmin": 17.4, "viento_kmh": 18.3, "viento_dir": "SO"}
        m = {"pleamar": "04:12 · 16:35", "bajamar": "10:20 · 22:41"}
        salida = os.path.join(AQUI, "placa-demo.jpg")
    else:
        c = clima(hoy)
        try:
            m = mareas(hoy)
        except Exception as e:
            print("Aviso: no se pudieron obtener las mareas:", e)
            m = None
        carpeta = os.path.join(AQUI, "placas")
        os.makedirs(carpeta, exist_ok=True)
        salida = os.path.join(carpeta, f"placa-{hoy.isoformat()}.jpg")
        for nombre in os.listdir(carpeta):          # conserva solo los últimos 7 días
            try:
                f = dt.date.fromisoformat(nombre[6:16])
                if nombre.startswith("placa-") and (hoy - f).days > 7:
                    os.remove(os.path.join(carpeta, nombre))
            except ValueError:
                pass

    datos = {
        "fecha": fecha_texto(hoy),
        "temp": f"máx {c['tmax']:.0f}° · mín {c['tmin']:.0f}°",
        "viento": f"{c['viento_dir']} {c['viento_kmh']:.0f} km/h",
        "mareas": m,
        "sol": puesta_de_sol(hoy).strftime("%H:%M") + " hs",
    }
    dibujar(datos, salida)
    print("Placa generada:", salida, datos)


if __name__ == "__main__":
    main()
