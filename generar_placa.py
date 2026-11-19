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
def open_meteo(host, ruta, params, intentos=3):
    prefijo = "customer-" if API_KEY else ""
    if API_KEY:
        params["apikey"] = API_KEY
    for intento in range(intentos):
        try:
            r = requests.get(f"https://{prefijo}{host}.open-meteo.com{ruta}", params=params, timeout=(10, 25))
            r.raise_for_status()
            return r.json()
        except Exception:
            if intento == intentos - 1:
                raise
            time.sleep(10 * (intento + 1))


def resumen_lluvia(horas, mm, prob=None):
    """Resumen de lluvia desde las 8 hs. Devuelve None si no se espera lluvia relevante."""
    idx = [i for i, t in enumerate(horas) if t.hour >= 8]
    if not idx:
        return None
    total = sum((mm[i] or 0) for i in idx)
    pmax = max((prob[i] or 0) for i in idx) if prob else None
    sig = [i for i in idx if (mm[i] or 0) >= 0.1 or (prob and (prob[i] or 0) >= 60)]
    if not sig or not (total >= 0.5 or (pmax is not None and pmax >= 60)):
        return None
    return {"prob": pmax, "mm": total, "desde": horas[sig[0]].hour, "hasta": horas[sig[-1]].hour + 1}


def texto_lluvia(l):
    partes = []
    if l["prob"] is not None:
        partes.append(f"{l['prob']:.0f}%")
    partes.append(f"{l['mm']:.0f} mm" if l["mm"] >= 1 else "< 1 mm")
    partes.append(f"{l['desde']} a {l['hasta']} hs" if l["hasta"] - l["desde"] > 1 else f"a las {l['desde']} hs")
    return " · ".join(partes)


def resumen_clima(temps, vel, ang, fuente, lluvia=None):
    u = sum(v * math.sin(a) for v, a in zip(vel, ang))
    w = sum(v * math.cos(a) for v, a in zip(vel, ang))
    grados = math.degrees(math.atan2(u, w)) % 360
    return {"tmax": max(temps), "tmin": min(temps), "viento_kmh": sum(vel) / len(vel),
            "viento_dir": PUNTOS[round(grados / 45) % 8], "fuente": fuente, "lluvia": lluvia}


def clima_open_meteo(hoy):
    j = open_meteo("api", "/v1/forecast", {
        "latitude": LAT, "longitude": LON, "timezone": TZ_NOMBRE,
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation,precipitation_probability",
        "wind_speed_unit": "kmh", "start_date": hoy.isoformat(), "end_date": hoy.isoformat()})
    h = j["hourly"]
    horas = [dt.datetime.fromisoformat(t) for t in h["time"]]
    dia = [i for i, t in enumerate(horas) if 10 <= t.hour <= 19]      # horario de playa
    vel = [h["wind_speed_10m"][i] for i in dia]
    ang = [math.radians(h["wind_direction_10m"][i]) for i in dia]
    lluvia = resumen_lluvia(horas, h["precipitation"], h["precipitation_probability"])
    return resumen_clima(h["temperature_2m"], vel, ang, "Open-Meteo", lluvia)


def clima_metno(hoy):
    """Plan B: servicio meteorológico de Noruega (MET Norway), gratuito y sin clave."""
    r = requests.get("https://api.met.no/weatherapi/locationforecast/2.0/compact",
                     params={"lat": round(LAT, 4), "lon": round(LON, 4)},
                     headers={"User-Agent": "LaPostaDelMar-placa-diaria/1.0 github.com/LaPostadelMar/PLaca-Diaria"},
                     timeout=(10, 30))
    r.raise_for_status()
    temps, vel, ang, horas_ll, mm_ll = [], [], [], [], []
    for e in r.json()["properties"]["timeseries"]:
        t = dt.datetime.fromisoformat(e["time"].replace("Z", "+00:00")).astimezone(TZ)
        if t.date() != hoy:
            continue
        det = e["data"]["instant"]["details"]
        temps.append(det["air_temperature"])
        sig_h = (e["data"].get("next_1_hours") or {}).get("details", {})
        if "precipitation_amount" in sig_h:
            horas_ll.append(t.replace(tzinfo=None))
            mm_ll.append(sig_h["precipitation_amount"])
        if 10 <= t.hour <= 19:
            vel.append(det["wind_speed"] * 3.6)                          # m/s -> km/h
            ang.append(math.radians(det["wind_from_direction"]))
    if not temps or not vel:
        raise RuntimeError("MET Norway no devolvió datos para hoy")
    return resumen_clima(temps, vel, ang, "MET Norway", resumen_lluvia(horas_ll, mm_ll))


def clima(hoy):
    try:
        return clima_open_meteo(hoy)
    except Exception as e:
        print(f"Aviso: Open-Meteo no respondió ({type(e).__name__}). Pruebo con MET Norway...")
        return clima_metno(hoy)


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
        "hourly": "sea_level_height_msl", "past_days": 1, "forecast_days": 2}, intentos=2)
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


def buscar(parte, ext):
    """Encuentra un archivo por parte del nombre, en assets/fonts, assets o la carpeta principal."""
    for base in (os.path.join(AQUI, "assets", "fonts"), os.path.join(AQUI, "assets"), AQUI):
        if os.path.isdir(base):
            for f in sorted(os.listdir(base)):
                if parte.lower() in f.lower() and f.lower().endswith(ext):
                    return os.path.join(base, f)
    raise FileNotFoundError(f"No encuentro un archivo '*{parte}*{ext}'. Archivos en el repositorio: "
                            f"{sorted(os.listdir(AQUI))}")


def font(peso, tam):
    return ImageFont.truetype(buscar(f"Poppins-{peso}", ".ttf"), int(tam * S))


def dibujar(datos, salida):
    ll = datos.get("lluvia")
    if ll:      # con aviso de lluvia el diseño se compacta un poco
        logo_tam, logo_y, t1_y, t2_y = 400, 215, 640, 684
        x0, cw, ch, gap, y0, ch_ll = 80, 920, 164, 16, 770, 124
    else:
        logo_tam, logo_y, t1_y, t2_y = 430, 225, 664, 708
        x0, cw, ch, gap, y0, ch_ll = 80, 920, 190, 20, 800, 0
    k = ch / 190

    stops = [(0.0, (11, 60, 93)), (0.55, (31, 122, 140)), (1.0, (242, 166, 90))]
    ys = np.linspace(0, 1, H)[:, None]
    arr = np.zeros((H, 1, 3))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        m = (ys >= p0) & (ys <= p1)
        t = (ys - p0) / (p1 - p0)
        for c in range(3):
            arr[:, :, c] = np.where(m, c0[c] + (c1[c] - c0[c]) * t, arr[:, :, c])
    bg = Image.fromarray(np.repeat(arr, W, axis=1).astype("uint8")).convert("RGBA")

    logo = Image.open(buscar("logo", ".png")).convert("RGBA")
    a = np.array(logo).astype(float)
    mask = ((255 - a[..., :3].mean(axis=2)) / 255 * a[..., 3]).clip(0, 255).astype("uint8")
    blanco_img = Image.new("RGBA", logo.size, (255, 255, 255, 255))
    blanco_img.putalpha(Image.fromarray(mask))
    ls = logo_tam * S
    blanco_img = blanco_img.resize((ls, ls), Image.LANCZOS)
    bg.alpha_composite(blanco_img, ((W - ls) // 2, logo_y * S))

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

    centrado("EL TIEMPO HOY", t1_y, font("Medium", 30), (255, 255, 255, 220), esp=8)
    centrado(datos["fecha"], t2_y, font("Bold", 56))

    etiqueta_marea = "MAREAS (ESTIMADAS)" if MAREAS_ESTIMADAS else "MAREAS"
    tarjetas = [("temp", "TEMPERATURA", ch)]
    if ll:
        tarjetas.append(("lluvia", "LLUVIA PREVISTA", ch_ll))
    tarjetas += [("viento", "VIENTO", ch), ("marea", etiqueta_marea, ch), ("sol", "PUESTA DEL SOL", ch)]

    posiciones, y = [], y0
    for tipo, etiqueta, alto in tarjetas:
        posiciones.append(y)
        y += alto + gap
    y_fin = y - gap

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for (tipo, etiqueta, alto), y in zip(tarjetas, posiciones):
        od.rounded_rectangle([x0 * S, y * S, (x0 + cw) * S, (y + alto) * S], radius=44 * S, fill=(8, 34, 58, 120))
    bg.alpha_composite(ov)
    d = ImageDraw.Draw(bg)
    blanco = (255, 255, 255)

    def ajustada(txt, peso, tam, ancho=690):
        while tam > 20 and d.textlength(txt, font=font(peso, tam)) > ancho * S:
            tam -= 2
        return font(peso, tam)

    def icono(tipo, cx, cy, esc=1.0):
        cx *= S; cy *= S; lw = int(6 * S * esc)
        e = esc
        if tipo == "temp":
            d.rounded_rectangle([cx - 9 * S * e, cy - 46 * S * e, cx + 9 * S * e, cy + 20 * S * e], radius=9 * S * e, outline=blanco, width=lw)
            d.ellipse([cx - 22 * S * e, cy + 6 * S * e, cx + 22 * S * e, cy + 50 * S * e], fill=blanco)
            d.rectangle([cx - 4 * S * e, cy - 10 * S * e, cx + 4 * S * e, cy + 30 * S * e], fill=blanco)
        elif tipo == "viento":
            r = 13 * S * e
            for n, (dy, ex) in enumerate([(-26, 25), (0, 40), (26, 10)]):
                yy = cy + dy * S * e; xe = cx + ex * S * e
                d.line([cx - 50 * S * e, yy, xe, yy], fill=blanco, width=lw)
                if n < 2:
                    d.arc([xe - r, yy - 2 * r, xe + r, yy], -180, 90, fill=blanco, width=lw)
                else:
                    d.arc([xe - r, yy, xe + r, yy + 2 * r], -90, 180, fill=blanco, width=lw)
        elif tipo == "marea":
            for dy in (-14, 18):
                pts = [(cx - 48 * S * e + t * S * e, cy + dy * S * e + math.sin(t / 96 * 2 * math.pi * 1.5) * 10 * S * e) for t in range(0, 97)]
                d.line(pts, fill=blanco, width=lw, joint="curve")
        elif tipo == "sol":
            d.arc([cx - 30 * S * e, cy - 24 * S * e, cx + 30 * S * e, cy + 36 * S * e], 180, 360, fill=blanco, width=lw)
            d.line([cx - 52 * S * e, cy + 6 * S * e, cx + 52 * S * e, cy + 6 * S * e], fill=blanco, width=lw)
            d.line([cx - 30 * S * e, cy + 30 * S * e, cx + 30 * S * e, cy + 30 * S * e], fill=blanco, width=lw)
            for ang in (-90, -45, -135):
                c, s_ = math.cos(math.radians(ang)), math.sin(math.radians(ang))
                d.line([cx + c * 42 * S * e, cy + 6 * S * e + s_ * 42 * S * e, cx + c * 58 * S * e, cy + 6 * S * e + s_ * 58 * S * e], fill=blanco, width=lw)
        elif tipo == "lluvia":
            for ox, oy, rr in ((-24, 0, 17), (0, -12, 23), (24, 2, 16)):
                d.ellipse([cx + (ox - rr) * S * e, cy + (oy - rr - 6) * S * e, cx + (ox + rr) * S * e, cy + (oy + rr - 6) * S * e], fill=blanco)
            d.rectangle([cx - 24 * S * e, cy - 12 * S * e, cx + 24 * S * e, cy + 10 * S * e], fill=blanco)
            for ox in (-20, 0, 20):
                d.line([cx + ox * S * e, cy + 20 * S * e, cx + (ox - 7) * S * e, cy + 38 * S * e], fill=blanco, width=lw)

    for (tipo, etiqueta, alto), y in zip(tarjetas, posiciones):
        tx = (x0 + 190) * S
        if tipo == "lluvia":
            icono(tipo, x0 + 95, y + alto / 2 + 2, 0.9)
            d.text((tx, (y + 14) * S), etiqueta, font=font("Medium", 25), fill=(255, 255, 255, 215))
            txt = texto_lluvia(ll)
            d.text((tx, (y + 54) * S), txt, font=ajustada(txt, "Bold", 42), fill=blanco)
            continue
        icono(tipo, x0 + 95, y + alto / 2, 0.9 if k < 1 else 1.0)
        d.text((tx, (y + 26 * k) * S), etiqueta, font=font("Medium", 28 * k), fill=(255, 255, 255, 215))
        if tipo == "marea":
            m = datos["mareas"]
            if m:
                for dy, txt in ((66, "Pleamar  " + m["pleamar"]), (120, "Bajamar  " + m["bajamar"])):
                    d.text((tx, (y + dy * k) * S), txt, font=ajustada(txt, "Bold", 42 * k), fill=blanco)
            else:
                d.text((tx, (y + 78 * k) * S), "No disponible hoy", font=font("Bold", 46 * k), fill=blanco)
        else:
            txt = datos[tipo]
            d.text((tx, (y + 68 * k) * S), txt, font=ajustada(txt, "Bold", 70 * k), fill=blanco)

    if datos.get("fuentes"):
        centrado("Datos: " + " · ".join(datos["fuentes"]), y_fin + 16, font("Medium", 22), (255, 255, 255, 170))

    bg.convert("RGB").resize((W // S, H // S), Image.LANCZOS).save(salida, "JPEG", quality=95)


# ---------------------------------------------------------------- principal
def fecha_texto(f):
    return f"{DIAS[f.weekday()]} {f.day} de {MESES[f.month - 1]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="usa datos de ejemplo, sin internet")
    ap.add_argument("--demo-lluvia", action="store_true", help="como --demo pero con aviso de lluvia")
    ap.add_argument("--fecha", help="AAAA-MM-DD (por defecto, hoy en Argentina)")
    args = ap.parse_args()
    hoy = dt.date.fromisoformat(args.fecha) if args.fecha else dt.datetime.now(TZ).date()

    if args.demo or args.demo_lluvia:
        c = {"tmax": 27.2, "tmin": 17.4, "viento_kmh": 18.3, "viento_dir": "SO", "fuente": "Open-Meteo",
             "lluvia": {"prob": 70, "mm": 4.2, "desde": 15, "hasta": 19} if args.demo_lluvia else None}
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
        "lluvia": c.get("lluvia"),
        "sol": puesta_de_sol(hoy).strftime("%H:%M") + " hs",
        "fuentes": sorted({c["fuente"]} | ({"Open-Meteo"} if m else set())),
    }
    dibujar(datos, salida)
    print("Placa generada:", salida, datos)


if __name__ == "__main__":
    main()
