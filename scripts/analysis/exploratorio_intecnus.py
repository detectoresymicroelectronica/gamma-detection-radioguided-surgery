#!/usr/bin/env python3
"""
ANÁLISIS EXPLORATORIO DE LAS MEDICIONES CON F-18 EN INTECNUS — exploratorio_intecnus.py — v0.6 (2026-11-20)

Analiza todos los .trc (LeCroy, modo SEQUENCE) de una carpeta, agrupados por el título del archivo
(C1<título><contador>: C102_X0_Y2000007.trc → título "02_X0_Y20"). Por cada título:
  - en la consola: segmentos, tasa de disparo, eventos con apilamiento y el fotopico;
  - una figura <título>_exploratorio (PDF + PNG) con cuatro paneles:
      (a) persistencia: todas las trazas superpuestas, como la pantalla del osciloscopio;
      (b) amplitud contra área de cada evento: los eventos limpios caen sobre una banda;
          los apilados y los que tienen otro pulso antes quedan fuera de ella;
      (c) espectro de áreas (cuentas por intervalo de área) de los eventos limpios, con el ajuste
          gaussiano del fotopico;
      (d) tasa de disparo de cada archivo a lo largo de la medición. La línea es la MEDIANA de los archivos
          (no la media), para que un archivo anómalo (p. ej. la fuente retirada mientras se adquiría) no la
          mueva; la consola lista los archivos a más de 5σ de Poisson de la mediana.

Si la señal está recortada por la escala vertical (la base o los picos fuera de la pantalla), amplitud y área
no sirven: la figura muestra solo (a) y (d) y la consola avisa el rango de pantalla.

Uso: python exploratorio_intecnus.py [carpeta .trc] [carpeta de salida]
     Sin argumentos analiza la carpeta del script y guarda ahí las figuras.
     Necesita estilo_pi.py en la misma carpeta que este script (y numpy, scipy, matplotlib ≥ 3.6).

Método (las mismas ideas que espectro_centelleo_evb.py, con las ventanas para el pulso del GSO):
  1. t_cf: cruce del 50 % de la amplitud (señal suavizada 5 ns).
  2. Base: media entre t_cf − 300 y t_cf − 60 ns.
  3. Amplitud: máximo de la señal suavizada − base. Área: Σ(v − base)·dt entre t_cf − 40 y t_cf + 460 ns,
     en V·ns, tal como sale del osciloscopio: sin convertir a carga ni a avalanchas (no depende de R_F
     ni de Q_SPE). El pulso promedio del GSO (20/11) cae al ~1 % a 460 ns del trigger.
  4. Evento limpio: sin otro pulso antes ni un segundo pulso dentro de la ventana (mediana + 5σ robusta
     del estadístico de cada archivo, como en espectro_centelleo_evb.py).
  5. Fotopico: el pico de mayor área del espectro; ajuste gaussiano iterado en ±1.5σ alrededor del
     centro. La línea punteada marca dónde caería el borde Compton (341 keV = 0.667 × 511 keV) si el
     fotopico es el de 511 keV: si el espectro muestra el borde ahí, la identificación es consistente.
"""
import os
import re
import struct
import sys
from datetime import datetime, timedelta

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d
from scipy.optimize import curve_fit
from scipy.signal import find_peaks

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estilo_pi as E  # noqa: E402

# ============================================================================
#  PARÁMETROS (si se cambia uno, anotarlo en el LOG). Tiempos en ns desde t_cf.
# ============================================================================
SUAVIZADO_NS = 5.0
BUSQUEDA_PICO_TRIG_NS = (-50.0, 150.0)   # respecto del trigger
BASE_NS = (-300.0, -60.0)
INTEGRACION_NS = (-40.0, 460.0)
SUBIDA_NS = 30.0
K_PRE, K_POST = 5.0, 5.0
ANCHO_BIN_VNS = 0.15            # V·ns, intervalo del espectro de áreas
COMPTON = 341.0 / 511.0


# ============================================================================
class ArchivoIncompleto(Exception):
    """El .trc tiene menos datos de los que declara el encabezado (p. ej. se cortó la escritura)."""


def leer_trc(ruta):
    """.trc de LeCroy. Devuelve v [nseg, n] (mV), t [n] (ns, rel. trigger), tiempos de trigger (s, rel. al
    primer segmento) y hora del primer trigger."""
    with open(ruta, "rb") as fh:
        d = fh.read()
    d = d[d.find(b"WAVEDESC"):]
    end = "<" if struct.unpack("<h", d[34:36])[0] == 1 else ">"
    u = lambda f, o: struct.unpack(end + f, d[o:o + struct.calcsize(end + f)])[0]
    comm, wdl, ut, trt, ris, w1 = u("h", 32), u("l", 36), u("l", 40), u("l", 48), u("l", 52), u("l", 60)
    nseg = max(1, u("l", 144))
    gain, offset, dt, hoff = u("f", 156), u("f", 160), u("f", 176), u("d", 180)
    seg = u("d", 296)
    hora = datetime(u("h", 308), d[307], d[306], d[305], d[304]) + timedelta(seconds=seg)
    p = wdl + ut
    tt = np.frombuffer(d[p:p + trt], dtype=end + "f8").reshape(-1, 2) if trt > 0 else np.zeros((1, 2))
    if trt > 0:
        hoff = tt[0, 1]
    p += trt + ris
    if len(d) < p + w1:
        raise ArchivoIncompleto(f"archivo incompleto: tiene {max(len(d) - p, 0)} de {w1} bytes de datos")
    raw = np.frombuffer(d[p:p + w1], dtype=end + ("i2" if comm == 1 else "i1")).astype(float)
    v = (raw * gain - offset).reshape(nseg, -1) * 1e3
    return v, (hoff + np.arange(v.shape[1]) * dt) * 1e9, tt[:, 0], hora


def titulo(archivo):
    return re.sub(r"\d{5}$", "", re.sub(r"^[CFMZ]\d", "", os.path.splitext(os.path.basename(archivo))[0]))


def cota(x, k):
    """mediana + k·σ robusta (σ = 1.4826·MAD)."""
    x = x[np.isfinite(x)]
    m = np.median(x)
    return m + k * 1.4826 * np.median(np.abs(x - m))


def gauss(x, a, mu, s):
    return a * np.exp(-0.5 * ((x - mu) / s) ** 2)


def analizar(ruta, persist=None):
    v, t, tt, hora = leer_trc(ruta)
    nseg, ns = v.shape
    dt = t[1] - t[0]
    recorte = np.mean(v == v.min()) > 1e-3 or np.mean(v == v.max()) > 1e-3     # muchas muestras en el tope del ADC
    ix = lambda x: int(np.clip(np.searchsorted(t, x), 0, ns - 1))
    vs = uniform_filter1d(v, max(1, round(SUAVIZADO_NS / dt)), axis=1, mode="nearest")
    L = max(1, round(SUBIDA_NS / dt))
    if persist is not None:
        persist["h"] += np.histogram2d(np.broadcast_to(t[::10], (nseg, len(t[::10]))).ravel(), v[:, ::10].ravel(),
                                       bins=(persist["et"], persist["ev"]))[0]

    j0, j1 = ix(BUSQUEDA_PICO_TRIG_NS[0]), ix(BUSQUEDA_PICO_TRIG_NS[1])
    kpk = j0 + vs[:, j0:j1].argmax(1)
    base0 = v[:, ix(t[0] + 20):ix(-100)].mean(1)
    q, amp, pre, post = (np.full(nseg, np.nan) for _ in range(4))
    for i in range(nseg):
        nivel = base0[i] + 0.5 * (vs[i, kpk[i]] - base0[i])
        debajo = np.nonzero(vs[i, :kpk[i] + 1] < nivel)[0]
        if not len(debajo):
            continue
        tc = t[debajo[-1]]
        if tc + BASE_NS[0] < t[0] or tc + INTEGRACION_NS[1] > t[-1]:
            continue
        b = v[i, ix(tc + BASE_NS[0]):ix(tc + BASE_NS[1])].mean()
        a, z = ix(tc + INTEGRACION_NS[0]), ix(tc + INTEGRACION_NS[1])
        amp[i] = vs[i, a:z].max() - b
        q[i] = (v[i, a:z] - b).sum() * dt
        pre[i] = vs[i, ix(t[0] + 10):a].max() - b
        desde = max(kpk[i] + L + round(5 / dt), a + L)
        post[i] = (vs[i, desde:z] - vs[i, desde - L:z - L]).max() if z > desde else -np.inf

    ok = np.isfinite(q)
    sin_pre = ok & (pre < cota(pre[ok], K_PRE))
    limpio = sin_pre & (post < cota(post[sin_pre], K_POST))
    return {"recorte": recorte, "pantalla": (v.min(), v.max()), "vmax": v.max(1), "q": q / 1e3, "amp": amp, "ok": ok, "sin_pre": sin_pre, "limpio": limpio,
            "nseg": nseg, "tasa": (len(tt) - 1) / (tt[-1] - tt[0]), "hora": hora, "tt": tt}


def fotopico(q):
    """Centro y σ del pico de mayor área (ajuste gaussiano iterado en ±1.5σ), o None."""
    if len(q) < 100:
        return None
    e = np.arange(0, np.percentile(q, 99.9) * 1.05, ANCHO_BIN_VNS)
    h = gaussian_filter1d(np.histogram(q, bins=e)[0].astype(float), 2)
    k, _ = find_peaks(h, prominence=0.1 * h.max())
    if not len(k):
        return None
    mu = 0.5 * (e[k[-1]] + e[k[-1] + 1])
    s = 0.08 * mu
    for _ in range(8):
        hh, ee = np.histogram(q, bins=np.linspace(mu - 1.5 * s, mu + 1.5 * s, 26))
        xc = 0.5 * (ee[1:] + ee[:-1])
        try:
            p, cov = curve_fit(gauss, xc, hh, p0=[hh.max(), mu, s], sigma=np.sqrt(np.maximum(hh, 1)))
        except RuntimeError:
            return None
        mu, s = p[1], abs(p[2])
    return p[0] * (e[1] - e[0]) / (ee[1] - ee[0]), mu, s, np.sqrt(cov[1, 1])


def figura(nombre, R, persist, salida, archivos):
    q, amp = np.concatenate([r["q"] for r in R]), np.concatenate([r["amp"] for r in R])
    ok, limpio = np.concatenate([r["ok"] for r in R]), np.concatenate([r["limpio"] for r in R])
    sin_pre = np.concatenate([r["sin_pre"] for r in R])
    fp = fotopico(q[limpio])

    recorte = any(r["recorte"] for r in R)
    E.aplicar("tesis")
    if recorte:
        fig, ax = E.figura("tesis", alto=0.38, columnas=2)
        a, d = ax
    else:
        fig, ax = E.figura("tesis", alto=0.75, filas=2, columnas=2)
        a, b, c, d = ax.ravel()

    h = np.ma.masked_equal(persist["h"].T, 0)
    m = a.pcolormesh(persist["et"], persist["ev"], h, cmap=E.SECUENCIAL, norm="log", rasterized=True)
    fig.colorbar(m, ax=a, label="Muestras", pad=0.01)
    a.set_xlabel("Tiempo respecto del trigger (ns)")
    a.set_ylabel("Tensión (mV)")
    ocupado = np.nonzero(persist["h"].sum(0))[0]
    a.set_ylim(persist["ev"][ocupado[0]] - 5, persist["ev"][ocupado[-1] + 1] + 5)
    a.grid(False)

    if recorte:
        dibujar_tasa(d, R)
        E.paneles(ax)
        E.guardar(fig, f"{nombre}_exploratorio", salida, script=__file__, datos=archivos)
        return q, amp, limpio, sin_pre, ok, None, *tasas(R)

    b.plot(q[limpio], amp[limpio], ".", ms=1, color=E.C[0], alpha=0.25, rasterized=True, label="limpios")
    b.plot(q[ok & ~sin_pre], amp[ok & ~sin_pre], ".", ms=2, color=E.C[2], label="pulso antes")
    b.plot(q[sin_pre & ~limpio], amp[sin_pre & ~limpio], ".", ms=2, color=E.C[3], label="segundo pulso")
    b.set_xlabel("Área del pulso (V·ns)")
    b.set_ylabel("Amplitud (mV)")
    b.legend(loc="upper left", markerscale=4)

    tope = np.percentile(q[limpio], 99.9) * 1.05
    ancho = ANCHO_BIN_VNS if tope > 30 * ANCHO_BIN_VNS else tope / 100
    e = np.arange(0, tope, ancho)
    c.hist(q[limpio], bins=e, color=E.C[0], label=f"limpios ({limpio.sum()})")
    if fp is not None:
        A, mu, s, _ = fp
        xf = np.linspace(mu - 1.5 * s, mu + 1.5 * s, 200)
        c.plot(xf, gauss(xf, A, mu, s), **E.MODELO, label=f"ajuste: FWHM {235.5 * s / mu:.1f} %")
        c.axvline(COMPTON * mu, ymax=0.5, **E.LIMITE, label="borde Compton\n(si el pico es 511 keV)")
    c.set_xlabel("Área del pulso (V·ns)")
    c.set_ylabel(f"Cuentas por {ancho:.2g} V·ns")
    c.set_xlim(0, e[-1])
    c.set_ylim(0, c.get_ylim()[1] * 1.45)
    c.legend(loc="upper right")

    dibujar_tasa(d, R)
    E.paneles(ax)
    E.guardar(fig, f"{nombre}_exploratorio", salida, script=__file__, datos=archivos)
    return q, amp, limpio, sin_pre, ok, fp, *tasas(R)


def tasas(R):
    return np.array([r["tasa"] for r in R]), np.array([r["tasa"] / np.sqrt(r["nseg"] - 1) for r in R])


def dibujar_tasa(d, R):
    t0 = R[0]["hora"]
    ts = np.array([(r["hora"] - t0).total_seconds() for r in R])
    tasa, err = tasas(R)
    d.errorbar(ts, tasa, err, fmt="o", color=E.C[0], label="cada archivo (±1σ de Poisson)")
    d.axhline(np.median(tasa), **E.LIMITE, label=f"mediana: {np.median(tasa):.0f} /s")
    d.set_xlabel(f"Tiempo desde {t0:%H:%M:%S} (s)")
    d.set_ylabel("Tasa de disparo (1/s)")
    d.set_ylim(0, tasa.max() * 1.3)
    d.legend(loc="lower right")


def main():
    sys.stdout.reconfigure(encoding="utf-8")         # para que σ, ⚠, etc. no fallen en la consola de Windows
    carpeta = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    salida = sys.argv[2] if len(sys.argv) > 2 else carpeta
    grupos = {}
    for f in sorted(x for x in os.listdir(carpeta) if x.lower().endswith(".trc")):
        grupos.setdefault(titulo(f), []).append(f)
    for nombre, archivos in grupos.items():
        persist = {"et": np.linspace(-500, 500, 501), "ev": np.arange(-100, 1000.5, 1.0)}   # mV; el eje se recorta a lo ocupado
        persist["h"] = np.zeros((500, len(persist["ev"]) - 1))
        R, usados = [], []
        for f in archivos:
            try:
                R.append(analizar(os.path.join(carpeta, f), persist))
                usados.append(f)
            except ArchivoIncompleto as ex:
                print(f"  ⚠ se omite {f}: {ex}")
        if not R:
            continue
        archivos = usados
        q, amp, limpio, sin_pre, ok, fp, tasa, err = figura(nombre, R, persist, salida, archivos)
        n = sum(r["nseg"] for r in R)
        print(f"{nombre}: {len(archivos)} archivos, {n} segmentos, {R[0]['hora']:%H:%M:%S}–{R[-1]['hora']:%H:%M:%S}")
        med = np.median(tasa)
        fuera = np.abs(tasa - med) > 5 * err
        print(f"  tasa de disparo: mediana {med:.0f} /s; sin los archivos fuera: media {tasa[~fuera].mean():.0f} /s, "
              f"dispersión {tasa[~fuera].std(ddof=1):.0f} /s (Poisson: {np.sqrt(np.mean(err[~fuera] ** 2)):.0f} /s)")
        for f, r in zip(np.array(archivos)[fuera], tasa[fuera]):
            print(f"  ⚠ {f}: {r:.0f} /s, a más de 5σ de la mediana")
        vmax = np.concatenate([r["vmax"] for r in R])
        if any(r["recorte"] for r in R):
            lo, hi = min(r["pantalla"][0] for r in R), max(r["pantalla"][1] for r in R)
            print(f"  ⚠ SEÑAL RECORTADA: la pantalla iba de {lo:.1f} a {hi:.1f} mV. Amplitud y área no sirven; "
                  f"solo la tasa. Máximo de las trazas ≥ {np.percentile(vmax, 0.1):.1f} mV (≈ trigger, sobre 0 V)")
            continue
        print(f"  limpios {limpio.sum()} | pulso antes {(ok & ~sin_pre).sum()} | segundo pulso {(sin_pre & ~limpio).sum()} "
              f"| sin analizar {(~ok).sum()}")
        print(f"  amplitud: mín {np.nanmin(amp):.1f} mV (≈ trigger), máx {np.nanmax(amp):.0f} mV")
        if fp is None:
            print("  fotopico: no encontrado")
            continue
        _, mu, s, emu = fp
        a_fp = np.median(amp[limpio & (np.abs(q - mu) < s)])
        print(f"  fotopico: área {mu:.2f} ± {emu:.2f} V·ns, amplitud mediana {a_fp:.0f} mV, FWHM {235.5 * s / mu:.1f} %")
        print(f"  borde Compton esperado si es 511 keV: {COMPTON * mu:.2f} V·ns")


if __name__ == "__main__":
    main()
