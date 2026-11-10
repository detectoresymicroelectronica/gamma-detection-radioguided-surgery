#!/usr/bin/env python3
"""
ÁREA DEL FOTOELECTRÓN — EVB-TIA v0          area_pulsos_evb.py — v2.0 (2026-11-06)

Para cada .trc (modo SEQUENCE) de la carpeta:
  - en la consola: el área del SPE (mV·ns y fC) con su incertidumbre;
  - una figura <archivo>_area.png para verificarla:
      izquierda: espectro de amplitudes de los eventos limpios, con la banda
                 de 1 PE que se usa para el área;
      derecha:   pulso promedio de 1 PE con la base y la integración marcadas,
                 y el control de la base (área de ruido sin pulso ≈ 0).

Uso: copiar el script en la carpeta de los .trc y correr
        python area_pulsos_evb.py
     o  python area_pulsos_evb.py "D:/datos/2026-11-06"

R_F se lee del nombre del archivo (token RF2200, RF1269, ...); si no está,
se usa R_F_POR_DEFECTO.

Método:
  1. Ventanas medidas desde t_cf: el instante en que cada pulso cruza el 50 %
     de su amplitud (no desde el trigger, que depende del umbral).
  2. Área de cada evento = Σ (v − base) · dt entre t_cf + INICIO y t_cf + FIN.
  3. Evento limpio:
       · sin pulsos antes (máximo antes del pulso − base);
       · sin deriva de la base (|base lejana − base|);
       · SIN UN SEGUNDO PULSO dentro de la ventana de integración: flanco de
         subida después del pico, v(t) − v(t − SUBIDA_NS);
       · sin saturar la pantalla del osciloscopio dentro de la ventana.
     Cada corte compara el estadístico con su propia distribución en el
     archivo (mediana + K·σ robusta, σ = 1,4826·MAD): no depende del SPE.
  4. Los eventos de 1 PE se eligen por AMPLITUD (el espectro de amplitudes
     separa mucho mejor los picos que el de áreas): banda |amp − μ1| < k·σ1
     alrededor del pico de 1 PE (ajuste gaussiano del núcleo del pico).
  5. Área del SPE = MEDIA del área de esos eventos. El ruido tiene media cero,
     así que la media no depende de la resolución en área.
     Control: el área de una ventana de igual largo SIN pulso (antes del
     pulso) tiene que dar ≈ 0; si no, la base está sesgada (aviso si > 3σ).
     Aviso también si el pico de 1 PE no está separado del ruido.
  6. Carga = área / (R_F · f_term); f_term = divisor R10 / (R11 ∥ entrada).

Cambios respecto de v1.0 (06/11), probados con los datos del 05/11 y 06/11:
  - Rechazo de un segundo pulso dentro de la ventana y de eventos saturados.
    En v1.0 las cuentas oscuras dentro de los 290 ns deformaban los picos de
    2 y 3 PE, y el resultado cambiaba hasta ~10 % según cuántos picos se
    ajustaban.
  - El SPE ya no sale de la separación entre picos del espectro de áreas: ese
    espectro tiene una resolución pobre (σ ≈ 370 mV·ns de ruido, dominado por
    el error de la base × el largo de la ventana) y a V_ov baja los picos no
    se separan.
"""

import os
import re
import struct
import sys

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d
from scipy.optimize import curve_fit
from scipy.signal import find_peaks

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ============================================================================
#  PARÁMETROS DE MÉTODO (a revisar; si se cambia uno, anotarlo en el LOG)
#  Tiempos en ns respecto de t_cf, salvo que se indique otra cosa.
# ============================================================================
R_F_POR_DEFECTO = 2200.0                    # Ω (JP1–JP3 abiertos)
SUAVIZADO_NS = 5.0                          # solo para amplitudes, t_cf y flancos; el área usa la señal cruda
FRACCION_CFD = 0.5
BUSQUEDA_PICO_TRIG_NS = (0.0, 100.0)        # respecto del TRIGGER
BASE_PROVISORIA_TRIG_NS = (-110.0, -50.0)   # respecto del TRIGGER, solo para t_cf
BASE_NS = (-110.0, -40.0)
BASE_LEJOS_NS = (-400.0, -330.0)
PRE_LIMPIO_NS = (-400.0, -40.0)
INTEGRACION_NS = (-40.0, 250.0)
RUIDO_AREA_NS = (-400.0, -110.0)            # control: ventana de igual largo sin pulso
# Cortes (mediana + K·σ robusta de cada estadístico):
K_PRE = 4.0                  # máximo antes del pulso − base
K_DERIVA = 3.0               # |base lejana − base|
K_POST = 4.0                 # flanco máximo después del pico (segundo pulso)
SUBIDA_NS = 30.0             # flanco de un segundo pulso: v(t) − v(t − SUBIDA_NS)
MARGEN_POST_NS = 5.0         # se busca desde (pico + SUBIDA_NS + margen) hasta el fin de la integración
BANDA_1PE_SIGMAS = 1.0       # eventos de 1 PE: |amp − μ1| < k·σ1
AJUSTE_NUCLEO_SIGMAS = 1.5   # el ajuste del pico de 1 PE (amplitud) usa ±k·σ alrededor del centro
RESOLUCION_MAX = 0.30        # si σ1/μ1 del pico de 1 PE supera esto, el pico no está separado del ruido
R10, R11 = 50.0, 10e3        # salida de la placa
R_ENTRADA = {"DC1M": 1e6, "AC1M": 1e6, "DC50": 50.0}
E = 1.602176634e-19


# ============================================================================
def leer_trc(ruta):
    """.trc de LeCroy (WAVEDESC). v [nseg, n] en V; t [n] en s rel. trigger."""
    with open(ruta, "rb") as fh:
        d = fh.read()
    d = d[d.find(b"WAVEDESC"):]
    end = "<" if struct.unpack("<h", d[34:36])[0] == 1 else ">"
    u = lambda f, o: struct.unpack(end + f, d[o:o + struct.calcsize(end + f)])[0]
    comm, wdl, ut, trt, ris, w1 = u("h", 32), u("l", 36), u("l", 40), u("l", 48), u("l", 52), u("l", 60)
    nseg = max(1, u("l", 144))
    gain, offset, dt, hoff = u("f", 156), u("f", 160), u("f", 176), u("d", 180)
    acople = {0: "DC50", 2: "DC1M", 4: "AC1M"}.get(u("h", 326), "?")
    p = wdl + ut
    if trt > 0:
        hoff = np.frombuffer(d[p:p + trt], dtype=end + "f8").reshape(-1, 2)[0, 1]
    p += trt + ris
    raw = np.frombuffer(d[p:p + w1], dtype=end + ("i2" if comm == 1 else "i1")).astype(float)
    v = (raw * gain - offset).reshape(nseg, -1)
    return v, hoff + np.arange(v.shape[1]) * dt, acople


def r_f_del_nombre(archivo):
    """Token RF<ohms> del nombre ('p' = coma). Tolera el contador de 5 dígitos
    del LeCroy pegado al final (RF2200 + 00000 → RF220000000)."""
    base = os.path.splitext(os.path.basename(archivo))[0]
    toks = re.sub(r"^[CFMZ]\d", "", base).split("_")
    m = re.search(r"\d+$", toks[-1])
    if m and not toks[-1].isdigit() and len(m.group()) >= 6:
        toks[-1] = toks[-1][:-5]
    for tok in toks:
        m = re.fullmatch(r"RF(\d+(?:p\d+)?)", tok, re.I)
        if m:
            return float(m.group(1).lower().replace("p", "."))
    return R_F_POR_DEFECTO


def escala(x):
    """σ robusta: 1,4826·MAD."""
    x = x[np.isfinite(x)]
    return 1.4826 * np.median(np.abs(x - np.median(x)))


def cota(x, k):
    """Umbral superior: mediana + k·σ robusta."""
    x = x[np.isfinite(x)]
    return np.median(x) + k * escala(x)


def gauss(x, a, mu, s):
    return a * np.exp(-0.5 * ((x - mu) / s) ** 2)


def picos_histograma(x, ancho):
    """Picos del histograma suavizado: centros, del más alto al más bajo."""
    x = x[np.isfinite(x)]
    if len(x) < 50:
        return np.array([])
    e = np.arange(np.percentile(x, 0.5), np.percentile(x, 99.5) + ancho, ancho)
    if len(e) < 5:
        return np.array([])
    h = gaussian_filter1d(np.histogram(x, bins=e)[0].astype(float), 1.5)
    k, _ = find_peaks(h, prominence=0.05 * h.max())
    c = 0.5 * (e[:-1] + e[1:])
    return c[k][np.argsort(h[k])[::-1]]


def ajustar_nucleo(x, semilla, s0):
    """Ajuste gaussiano del núcleo de un pico (±AJUSTE_NUCLEO_SIGMAS·σ), iterado.
    Devuelve (centro, sigma, parámetros) o None."""
    x = x[np.isfinite(x)]
    mu, s = semilla, s0
    for _ in range(6):
        lo, hi = mu - AJUSTE_NUCLEO_SIGMAS * s, mu + AJUSTE_NUCLEO_SIGMAS * s
        h, e = np.histogram(x, bins=np.linspace(lo, hi, 26))
        if h.sum() < 30:
            return None
        xc = 0.5 * (e[1:] + e[:-1])
        try:
            p, _ = curve_fit(gauss, xc, h, p0=[h.max(), mu, s], sigma=np.sqrt(np.maximum(h, 1)), maxfev=20000)
        except Exception:
            return None
        if not (0.5 * semilla < p[1] < 1.5 * semilla) or not (0 < abs(p[2]) < semilla):
            return None
        mu, s = p[1], abs(p[2])
    return mu, s, (p[0], mu, s)


# ============================================================================
def analizar(ruta):
    v, t, acople = leer_trc(ruta)
    v, t = v * 1e3, t * 1e9                     # mV, ns
    nseg, ns = v.shape
    if nseg < 50:
        return None, f"se omite: {nseg} segmento(s), no es una secuencia"
    dt = t[1] - t[0]
    ix = lambda tt: int(np.clip(np.searchsorted(t, tt), 0, ns))
    vs = uniform_filter1d(v, max(1, round(SUAVIZADO_NS / dt)), axis=1, mode="nearest")
    L = max(1, round(SUBIDA_NS / dt))
    flanco = vs[:, L:] - vs[:, :-L]             # flanco[j] = vs[j + L] − vs[j]

    # t_cf de cada pulso disparado
    b0, b1 = ix(BASE_PROVISORIA_TRIG_NS[0]), ix(BASE_PROVISORIA_TRIG_NS[1])
    base0 = v[:, b0:b1].mean(1)
    j0, j1 = ix(BUSQUEDA_PICO_TRIG_NS[0]), ix(BUSQUEDA_PICO_TRIG_NS[1])
    kpk = j0 + vs[:, j0:j1].argmax(1)
    t_cf = np.full(nseg, np.nan)
    for i in range(nseg):
        nivel = base0[i] + FRACCION_CFD * (vs[i, kpk[i]] - base0[i])
        debajo = np.nonzero(vs[i, b1:kpk[i] + 1] < nivel)[0]
        if len(debajo) and vs[i, kpk[i]] > base0[i]:
            k = b1 + debajo[-1]
            t_cf[i] = t[k] + (nivel - vs[i, k]) * dt / (vs[i, k + 1] - vs[i, k])

    # por evento, todo respecto de t_cf
    vtope = v.max()
    base, amp, pre, der, post, area, area0 = (np.full(nseg, np.nan) for _ in range(7))
    sat = np.zeros(nseg, bool)
    for i in np.nonzero(np.isfinite(t_cf))[0]:
        tc = t_cf[i]
        if tc + PRE_LIMPIO_NS[0] < t[0] or tc + INTEGRACION_NS[1] > t[-1]:
            continue
        a, b = ix(tc + INTEGRACION_NS[0]), ix(tc + INTEGRACION_NS[1])
        base[i] = v[i, ix(tc + BASE_NS[0]):ix(tc + BASE_NS[1])].mean()
        amp[i] = vs[i, kpk[i]] - base[i]
        pre[i] = vs[i, ix(tc + PRE_LIMPIO_NS[0]):ix(tc + PRE_LIMPIO_NS[1])].max() - base[i]
        der[i] = v[i, ix(tc + BASE_LEJOS_NS[0]):ix(tc + BASE_LEJOS_NS[1])].mean() - base[i]
        desde = max(ix(t[kpk[i]] + SUBIDA_NS + MARGEN_POST_NS), a + L)
        post[i] = flanco[i, desde - L:b - L].max() if b > desde else -np.inf
        area[i] = (v[i, a:b] - base[i]).sum() * dt
        area0[i] = (v[i, ix(tc + RUIDO_AREA_NS[0]):ix(tc + RUIDO_AREA_NS[1])] - base[i]).sum() * dt
        sat[i] = v[i, a:b].max() >= vtope - 0.2
    if (v.max(1) >= vtope - 0.2).sum() < 5:     # si casi nadie toca el tope, no hubo saturación
        sat[:] = False

    ok = np.isfinite(area)
    sin_previos = ok & (pre < cota(pre[ok], K_PRE)) & \
        (np.abs(der - np.median(der[ok])) < K_DERIVA * escala(der[ok]))
    limpio = sin_previos & (post < cota(post[sin_previos], K_POST)) & ~sat

    # pico de 1 PE en el espectro de AMPLITUDES (el más alto)
    al = amp[limpio]
    ruido = escala((vs[:, b0:b1] - vs[:, b0:b1].mean(1, keepdims=True)).ravel())
    centros = picos_histograma(al, max(ruido / 3, 0.05))
    f1, aviso = None, ""
    if len(centros):
        f1 = ajustar_nucleo(al, centros[0], 0.2 * centros[0])
    if f1 is not None:
        mu1, s1 = f1[0], f1[1]
        # control: el pico siguiente tiene que estar en ~2·μ1; si aparece en ~1,5·μ1,
        # el pico usado es de 2 PE (el de 1 PE quedó debajo del trigger)
        e = np.arange(0, 4, 0.04)
        h = gaussian_filter1d(np.histogram(al / mu1, bins=e)[0].astype(float), 2)
        kk, _ = find_peaks(h, prominence=0.08 * h.max())
        rel = 0.5 * (e[1:] + e[:-1])[kk]
        if np.any((rel > 1.3) & (rel < 1.7)):
            aviso = ("hay un pico en ~1,5× del usado: el pico usado puede ser de 2 PE "
                     "(¿el de 1 PE quedó debajo del trigger?)")

    # área del SPE: media del área de los eventos de 1 PE
    res, forma = None, None
    tr = np.arange(PRE_LIMPIO_NS[0], INTEGRACION_NS[1] + 100, dt)
    if f1 is not None:
        es1 = limpio & (np.abs(amp - mu1) < BANDA_1PE_SIGMAS * s1)
        if es1.sum() >= 10:
            x = area[es1]
            res = (x.mean(), x.std(ddof=1) / np.sqrt(len(x)), int(es1.sum()))
            forma = np.nanmean([np.interp(tr + t_cf[i], t, v[i] - base[i], left=np.nan, right=np.nan)
                                for i in np.nonzero(es1)[0]], 0)
    a0 = area0[sin_previos]
    a0 = a0[np.isfinite(a0)]
    control0 = (a0.mean(), a0.std(ddof=1) / np.sqrt(len(a0))) if len(a0) > 2 else (np.nan, np.nan)
    avisos = [aviso] if aviso else []
    if f1 is not None and s1 / mu1 > RESOLUCION_MAX:
        avisos.append(f"pico de 1 PE no separado del ruido (σ1/μ1 = {s1 / mu1:.2f}): resultado no confiable")
    if res is not None and abs(control0[0]) > 3 * control0[1]:
        avisos.append(f"la base está sesgada: área sin pulso = {control0[0]:+.0f} ± {control0[1]:.0f} mV·ns "
                      f"({100 * control0[0] / res[0]:+.0f} % del SPE)")
    aviso = " | ".join(avisos)

    rin = R_ENTRADA.get(acople, 1e6)
    z = R11 * rin / (R11 + rin)
    return {"nseg": nseg, "limpios": int(limpio.sum()), "res": res, "f1": f1, "aviso": aviso,
            "amp_l": al, "control0": control0, "tr": tr, "forma": forma,
            "rf": r_f_del_nombre(ruta), "acople": acople, "f_term": z / (R10 + z),
            # datos por evento, para otros análisis (no cambian la salida de este script)
            "ev": {"amp": amp, "area": area, "base": base, "t_cf": t_cf, "kpk": kpk, "limpio": limpio,
                   "sin_previos": sin_previos, "sat": sat, "flanco": flanco, "L": L,
                   "cota_post": cota(post[sin_previos], K_POST), "v": v, "t": t, "vtope": vtope}}, None


def figura(ruta, r):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.8))
    al = r["amp_l"][np.isfinite(r["amp_l"])]
    e = np.linspace(0, np.percentile(al, 99.5) if len(al) else 1, 120)
    if len(al):
        a1.hist(al, bins=e, color="#2a78d6", label=f"eventos limpios ({r['limpios']}/{r['nseg']})")
    if r["f1"] is not None:
        mu1, s1, p = r["f1"]
        a1.axvspan(mu1 - BANDA_1PE_SIGMAS * s1, mu1 + BANDA_1PE_SIGMAS * s1, color="#eb6834", alpha=0.25, lw=0,
                   label="banda de 1 PE")
        xf = np.linspace(mu1 - AJUSTE_NUCLEO_SIGMAS * s1, mu1 + AJUSTE_NUCLEO_SIGMAS * s1, 200)
        a1.plot(xf, gauss(xf, *p) * (e[1] - e[0]) / ((2 * AJUSTE_NUCLEO_SIGMAS * s1) / 25), color="#3b3b3a", lw=2)
    a1.set_title("Espectro de amplitudes" + ("  ⚠ ver consola" if r["aviso"] else ""), fontsize=10)
    a1.set_xlabel("amplitud respecto de la base [mV]")
    a1.set_ylabel("eventos")
    if len(al):
        a1.legend(fontsize=8, frameon=False, loc="upper right")
    if r["forma"] is not None:
        a2.axvspan(*BASE_NS, color="#d9d8d3", lw=0, label="base")
        a2.axvspan(*INTEGRACION_NS, color="#cfe0f5", lw=0, label="integración")
        a2.plot(r["tr"], r["forma"], color="#2a78d6", lw=2, label=f"promedio de {r['res'][2]} eventos de 1 PE")
        a2.axhline(0, color="#9c9b95", lw=0.8)
        a2.legend(fontsize=8, frameon=False, loc="upper right")
    if r["res"] is not None:
        c0, ec0 = r["control0"]
        a2.set_title(f"Área del SPE = {r['res'][0]:.0f} ± {r['res'][1]:.0f} mV·ns   "
                     f"(control sin pulso: {c0:+.0f} ± {ec0:.0f})", fontsize=10)
    else:
        a2.set_title("Pico de 1 PE no encontrado", fontsize=10)
    a2.set_xlabel("tiempo respecto de t_cf [ns]")
    a2.set_ylabel("mV")
    for a in (a1, a2):
        a.grid(color="#e4e3df", lw=0.6)
        a.set_axisbelow(True)
        a.spines[["top", "right"]].set_visible(False)
    fig.suptitle(os.path.basename(ruta), fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.splitext(ruta)[0] + "_area.png", dpi=150)
    plt.close(fig)


def main():
    carpeta = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    archivos = sorted(f for f in os.listdir(carpeta) if f.lower().endswith(".trc"))
    if not archivos:
        sys.exit(f"No hay .trc en {carpeta}")
    for f in archivos:
        ruta = os.path.join(carpeta, f)
        try:
            r, motivo = analizar(ruta)
        except Exception as ex:
            r, motivo = None, f"error: {ex}"
        if r is None:
            print(f"{f}: {motivo}")
            continue
        figura(ruta, r)
        if r["res"] is None:
            print(f"{f}: pico de 1 PE no encontrado ({r['limpios']}/{r['nseg']} limpios); ver la figura")
            continue
        a, ea, n = r["res"]
        k = 1e3 / (r["rf"] * r["f_term"])           # mV·ns → fC
        print(f"{f}: área SPE = {a:.0f} ± {ea:.0f} mV·ns → {a * k:.0f} ± {ea * k:.0f} fC "
              f"(G = {a * k * 1e-15 / E:.3g}) | R_F = {r['rf']:.0f} Ω, {r['acople']} | "
              f"{n} eventos de 1 PE, {r['limpios']}/{r['nseg']} limpios")
        for av in filter(None, r["aviso"].split(" | ")):
            print(f"   ⚠ {av}")


if __name__ == "__main__":
    main()
