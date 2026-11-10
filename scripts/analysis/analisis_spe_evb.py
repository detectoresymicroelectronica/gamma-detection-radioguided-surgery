#!/usr/bin/env python3
"""
=============================================================================
  ANÁLISIS DEL ESPECTRO DE FOTOELECTRONES (SPE) — EVB-TIA v0
=============================================================================
  Proyecto: Gamma Tracker — Caracterización de la EVB-TIA v0 (M1, M2, M3)
  Fecha de creación: 2026-11-05

  Entrada:
    Archivos .trc del LeCroy WaveRunner 104MXi-A adquiridos en modo SEQUENCE
    (N segmentos por archivo), con el SiPM en oscuridad: cada segmento es un
    evento de cuenta oscura disparado por flanco positivo en C1.

  Qué calcula, por cada archivo (una sobretensión):
    1. Base local de cada evento y selección de eventos "limpios"
       (sin pulsos previos en los 300 ns anteriores al trigger).
    2. Amplitud de cada evento respecto de SU base local (señal suavizada).
    3. Ajuste del histograma de amplitudes con picos gaussianos equiespaciados:
         mu_n = mu0 + n·Δ ,  sigma_n² = sigma0² + n·sigma1²
       Δ es la amplitud del SPE (no depende de la base ni del sesgo del máximo).
    4. Crosstalk aparente: P(> 1,5 PE) / P(> 0,5 PE) en eventos limpios.
    5. Tasa de disparos por encima del umbral (≈ tasa de cuentas oscuras), a
       partir de los tiempos de trigger de los segmentos.
    6. Forma promedio del SPE (eventos de 1 PE limpios antes y después):
       amplitud, tiempo de pico, subida 10–90 %, FWHM, constante de la cola.
    7. Carga del SPE (integral de la forma promedio / R_F) y ganancia G = Q/e.
    8. Ruido rms de la línea de base en ventanas limpias y SNR del SPE.

  Y entre archivos (M2):
    Ajuste lineal de Δ, de la amplitud de la forma promedio y de G contra la
    tensión aplicada → V_br (intersección) y capacidad efectiva de microcelda.

  Salidas (carpeta resultados_spe_<fecha_hora>/):
    resumen.txt, resumen.csv, ajuste_lineal.csv, eventos_<etiqueta>.csv,
    histogramas_amplitud.png, forma_spe.png, lineal_vs_vbias.png,
    tiempos_trigger.png, ejemplos_segmentos.png

  Uso:
    python analisis_spe_evb.py                         # usa el bloque MEDICIONES
    python analisis_spe_evb.py --dir "D:/2026-11-05_EVB02"
    python analisis_spe_evb.py --file a.trc 27.219 --file b.trc 29.719

  Requisitos: Python 3.9+, numpy, scipy, matplotlib (incluidos en Anaconda).
=============================================================================
"""

import argparse
import csv
import os
import struct
import sys
from datetime import datetime

import numpy as np
from scipy.optimize import curve_fit

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# =============================================================================
#  CONFIGURACIÓN DE LA MEDICIÓN  (editar acá)
# =============================================================================

CARPETA_DATOS = "."   # carpeta donde están los .trc (o usar --dir)

# Una entrada por archivo. V_source = tensión que aplicó la Keithley [V].
# I_bias_uA = corriente de bias medida en el monitoreo [µA] (None si no se anotó).
MEDICIONES = [
    {"archivo": "C1SEQ_Vov2500000.trc",  "V_source": 27.219, "I_bias_uA": None, "etiqueta": "Vov2p5"},
    {"archivo": "C1SEQ_Vov37500000.trc", "V_source": 28.469, "I_bias_uA": None, "etiqueta": "Vov3p75"},
    {"archivo": "C1SEQ_Vov500001.trc",   "V_source": 29.719, "I_bias_uA": 2.46, "etiqueta": "Vov5p0"},
]

PLACA = "EVB-02"
R_F = 2200.0            # resistencia de realimentación [Ω] (JP1–JP3 abiertos)
E_CARGA = 1.602176634e-19

# =============================================================================
#  PARÁMETROS DEL ANÁLISIS  (modificar con justificación y anotarlo)
# =============================================================================

SUAVIZADO_NS = 5.0               # media móvil para la amplitud [ns]
VENTANA_BASE = (-60.0, -15.0)    # base local, justo antes del pulso [ns]
VENTANA_BASE_LEJOS = (-300.0, -250.0)  # base lejana (detecta colas de pulsos previos)
VENTANA_PRE = (-300.0, -15.0)    # ventana que tiene que estar limpia [ns]
VENTANA_PICO = (0.0, 100.0)      # búsqueda del máximo del pulso disparado [ns]
VENTANA_CARGA = (-15.0, 250.0)   # integración de la forma promedio [ns]
VENTANA_POST = (150.0, 495.0)    # sin pulsos posteriores (solo para forma y carga)

CORTE_PRE = 0.4       # máx. en VENTANA_PRE − base < CORTE_PRE·SPE
CORTE_DERIVA = 0.3    # |base lejana − base| < CORTE_DERIVA·SPE
CORTE_POST = 0.4      # máx. en VENTANA_POST − base < CORTE_POST·SPE
N_PICOS = 3           # picos del ajuste (1..N PE)
SEL_1PE_SIGMAS = 1.5  # eventos de 1 PE: |amp − mu_1| < SEL_1PE_SIGMAS·sigma_1

COLORES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]  # validados
TINTA = "#3b3b3a"
GRILLA = "#e4e3df"


# =============================================================================
#  LECTURA DE ARCHIVOS .trc (LeCroy WAVEDESC, plantilla LECROY_2_3)
# =============================================================================

def leer_trc(ruta):
    """Lee un .trc de LeCroy (simple o SEQUENCE).

    Devuelve dict con:
      v      [nseg, nmuestras]  tensión [V]
      t      [nseg, nmuestras]  tiempo respecto del trigger de cada segmento [s]
      t_trig [nseg]             tiempo de trigger de cada segmento respecto del primero [s]
      meta                      metadatos del encabezado
    """
    with open(ruta, "rb") as f:
        b = f.read()
    i = b.find(b"WAVEDESC")
    if i < 0:
        raise ValueError(f"{ruta}: no es un archivo .trc de LeCroy (falta WAVEDESC)")
    d = b[i:]
    end = "<" if struct.unpack("<h", d[34:36])[0] == 1 else ">"

    def u(fmt, off):
        return struct.unpack(end + fmt, d[off:off + struct.calcsize(end + fmt)])[0]

    def s(off, n):
        return d[off:off + n].split(b"\0")[0].decode(errors="replace").strip()

    comm = u("h", 32)
    wdl, ut, trt, ris, w1 = u("l", 36), u("l", 40), u("l", 48), u("l", 52), u("l", 60)
    nseg = max(1, u("l", 144))
    gain, offset, dt = u("f", 156), u("f", 160), u("f", 176)
    hoff = u("d", 180)
    seg, mi, hh, dd, mo, yy = u("d", 296), d[304], d[305], d[306], d[307], u("h", 308)
    acople = {0: "DC50", 1: "GND", 2: "DC1M", 3: "GND", 4: "AC1M"}.get(u("h", 326), "?")

    p = wdl + ut
    if trt > 0:
        tt = np.frombuffer(d[p:p + trt], dtype=end + "f8").reshape(-1, 2)
        t_trig, hoffs = tt[:, 0], tt[:, 1]
    else:
        t_trig, hoffs = np.zeros(1), np.array([hoff])
    p += trt + ris
    raw = np.frombuffer(d[p:p + w1], dtype=end + ("i2" if comm == 1 else "i1")).astype(float)
    v = (raw * gain - offset).reshape(nseg, -1)
    ns = v.shape[1]
    t = hoffs[:, None] + np.arange(ns)[None, :] * dt

    meta = {
        "instrumento": s(76, 16),
        "fecha_trigger": f"{yy:04d}-{mo:02d}-{dd:02d} {hh:02d}:{mi:02d}:{seg:06.3f}",
        "segmentos": nseg, "muestras_por_segmento": ns,
        "dt_ns": dt * 1e9, "paso_ADC_8bit_uV": gain * 1e6 * (256 if comm == 1 else 1),
        "offset_vertical_mV": offset * 1e3, "acople": acople,
    }
    return {"v": v, "t": t, "t_trig": t_trig, "meta": meta}


# =============================================================================
#  UTILIDADES
# =============================================================================

def media_movil(v, n):
    if n <= 1:
        return v
    k = np.ones(n) / n
    return np.apply_along_axis(lambda x: np.convolve(x, k, mode="same"), 1, v)


def ventana(t_ns, a, b):
    return (t_ns > a) & (t_ns < b)


def modelo_picos(x, *p):
    """N gaussianas: mu_n = mu0 + n·Δ, sigma_n² = s0² + n·s1²."""
    n = len(p) - 4
    mu0, delta, s0, s1 = p[n:]
    y = np.zeros_like(x, dtype=float)
    for k in range(1, n + 1):
        sk = np.sqrt(s0 ** 2 + k * s1 ** 2)
        y += p[k - 1] * np.exp(-0.5 * ((x - mu0 - k * delta) / sk) ** 2)
    return y


def ajustar_picos(amp, spe0, n_picos):
    bins = np.arange(0.4 * spe0, (n_picos + 1.2) * spe0, spe0 / 20)
    h, e = np.histogram(amp, bins=bins)
    xc = 0.5 * (e[1:] + e[:-1])
    p0 = [max(h.max(), 1) * 0.25 ** k for k in range(n_picos)] + [0.0, spe0, 0.1 * spe0, 0.1 * spe0]
    lo = [0] * n_picos + [-0.5 * spe0, 0.5 * spe0, 0.0, 0.0]
    hi = [np.inf] * n_picos + [0.5 * spe0, 1.5 * spe0, spe0, spe0]
    sig = np.sqrt(np.maximum(h, 1))
    p, cov = curve_fit(modelo_picos, xc, h, p0=p0, bounds=(lo, hi), sigma=sig, maxfev=50000)
    perr = np.sqrt(np.diag(cov))
    return p, perr, (h, e)


def cruce(t, w, nivel, desde, hacia):
    """Instante (interpolado) en que w cruza 'nivel' recorriendo de 'desde' a 'hacia'."""
    paso = 1 if hacia > desde else -1
    for k in range(desde, hacia, paso):
        a, b = w[k], w[k + paso]
        if (a - nivel) * (b - nivel) <= 0 and a != b:
            return t[k] + (nivel - a) * (t[k + paso] - t[k]) / (b - a)
    return np.nan


def ajuste_lineal(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2:
        return None
    a, b = np.polyfit(x, y, 1)
    res = y - (a * x + b)
    sa = sb = np.nan
    if len(x) > 2:
        s2 = (res ** 2).sum() / (len(x) - 2)
        sxx = ((x - x.mean()) ** 2).sum()
        sa = np.sqrt(s2 / sxx)
        sb = np.sqrt(s2 * (1 / len(x) + x.mean() ** 2 / sxx))
    vbr = -b / a
    # error de V_br por propagación (a y b correlacionados: se usa la forma centrada)
    svbr = np.nan
    if len(x) > 2:
        svbr = abs(1 / a) * np.sqrt(s2 / len(x) + (x.mean() - vbr) ** 2 * sa ** 2)
    return {"pendiente": a, "ordenada": b, "s_pendiente": sa, "s_ordenada": sb,
            "V_br": vbr, "s_V_br": svbr, "residuos": res}


# =============================================================================
#  ANÁLISIS DE UNA MEDICIÓN
# =============================================================================

def analizar(med, carpeta):
    ruta = med["archivo"] if os.path.isabs(med["archivo"]) else os.path.join(carpeta, med["archivo"])
    d = leer_trc(ruta)
    v = d["v"] * 1e3                     # mV
    t_ns = d["t"][0] * 1e9               # el eje es el mismo para todos (offset < 1 muestra)
    nseg = v.shape[0]
    if nseg < 50:
        raise ValueError(f"{ruta}: solo {nseg} segmento(s). ¿Se adquirió en modo SEQUENCE?")

    n_suav = max(1, int(round(SUAVIZADO_NS / d["meta"]["dt_ns"])))
    vs = media_movil(v, n_suav)

    base = vs[:, ventana(t_ns, *VENTANA_BASE)].mean(1)
    base_lejos = vs[:, ventana(t_ns, *VENTANA_BASE_LEJOS)].mean(1)
    premax = vs[:, ventana(t_ns, *VENTANA_PRE)].max(1) - base
    amp = vs[:, ventana(t_ns, *VENTANA_PICO)].max(1) - base
    postmax = vs[:, ventana(t_ns, *VENTANA_POST)].max(1) - base
    carga_ev = (v[:, ventana(t_ns, *VENTANA_CARGA)] - base[:, None]).sum(1) * d["meta"]["dt_ns"]

    # Estimación inicial del SPE: moda del histograma de amplitudes (excluye disparos de ruido)
    cand = amp[amp > 3.0]
    h0, e0 = np.histogram(cand, bins=np.arange(3, max(cand.max(), 10), 0.5))
    spe0 = e0[np.argmax(h0)] + 0.25

    limpio = (premax < CORTE_PRE * spe0) & (np.abs(base_lejos - base) < CORTE_DERIVA * spe0)
    p, perr, hist = ajustar_picos(amp[limpio], spe0, N_PICOS)
    mu0, delta, s0, s1 = p[N_PICOS:]
    e_delta = perr[N_PICOS + 1]
    mu1 = mu0 + delta
    sig1 = np.sqrt(s0 ** 2 + s1 ** 2)

    a_l = amp[limpio]
    n_05 = (a_l > mu0 + 0.5 * delta).sum()
    n_15 = (a_l > mu0 + 1.5 * delta).sum()
    p_ct = n_15 / n_05
    e_ct = np.sqrt(p_ct * (1 - p_ct) / n_05)
    frac_ruido = (amp < mu0 + 0.5 * delta).mean()

    # Tiempos entre triggers → tasa por encima del umbral (MLE de exponencial desplazada)
    dts = np.diff(d["t_trig"])
    t_muerto = dts.min()
    tasa = 1.0 / np.mean(dts - t_muerto)
    e_tasa = tasa / np.sqrt(len(dts))

    # Forma promedio del SPE
    post_ok = postmax < CORTE_POST * spe0
    sel1 = limpio & post_ok & (np.abs(amp - mu1) < SEL_1PE_SIGMAS * sig1)
    forma = (v[sel1] - base[sel1, None]).mean(0)
    kpk = int(np.argmax(np.where(ventana(t_ns, *VENTANA_PICO), forma, -np.inf)))
    A = forma[kpk]
    k0 = int(np.argmin(np.abs(t_ns - VENTANA_PRE[0])))  # buscar el 10 % hacia atrás hasta -300 ns
    t10 = cruce(t_ns, forma, 0.1 * A, kpk, k0)
    t90 = cruce(t_ns, forma, 0.9 * A, kpk, k0)
    t50a = cruce(t_ns, forma, 0.5 * A, kpk, k0)
    t50b = cruce(t_ns, forma, 0.5 * A, kpk, len(t_ns) - 1)
    # Cola: exponencial desde el pico + 10 ns hasta que cae al 10 % (con offset libre)
    kt = np.where((t_ns > t_ns[kpk] + 10) & (t_ns < t_ns[kpk] + 300))[0]
    corte = np.where(forma[kt] < 0.1 * A)[0]
    kt = kt[: corte[0]] if len(corte) else kt
    try:
        pc, cc = curve_fit(lambda x, a, tau, c: a * np.exp(-(x - t_ns[kpk]) / tau) + c,
                           t_ns[kt], forma[kt], p0=[A, 50.0, 0.0], maxfev=20000)
        tau, e_tau = pc[1], np.sqrt(cc[1, 1])
    except Exception:
        tau = e_tau = np.nan
    q_mVns = forma[ventana(t_ns, *VENTANA_CARGA)].sum() * d["meta"]["dt_ns"]
    Q = q_mVns * 1e-12 / R_F            # C
    G = Q / E_CARGA

    # Ruido de la línea de base en ventanas limpias
    pre_raw = v[limpio][:, ventana(t_ns, *VENTANA_PRE)]
    pre_suav = vs[limpio][:, ventana(t_ns, *VENTANA_PRE)]
    ruido = np.median((pre_raw - pre_raw.mean(1, keepdims=True)).std(1))
    ruido_s = np.median((pre_suav - pre_suav.mean(1, keepdims=True)).std(1))

    i_calc = tasa * G * E_CARGA * (1 + p_ct) * 1e6   # µA

    r = {
        "etiqueta": med["etiqueta"], "archivo": os.path.basename(ruta), "V_source_V": med["V_source"],
        "I_bias_uA": med.get("I_bias_uA"), **d["meta"],
        "eventos": nseg, "limpios": int(limpio.sum()), "eventos_1PE_forma": int(sel1.sum()),
        "frac_disparos_ruido": frac_ruido,
        "SPE_delta_mV": delta, "e_SPE_delta_mV": e_delta,
        "mu1_mV": mu1, "mu0_sesgo_mV": mu0, "sigma1_mV": sig1, "sigma_exceso_mV": s1,
        "crosstalk_aparente": p_ct, "e_crosstalk": e_ct,
        "tasa_trigger_MHz": tasa / 1e6, "e_tasa_MHz": e_tasa / 1e6, "t_muerto_us": t_muerto * 1e6,
        "A_forma_mV": A, "t_pico_ns": t_ns[kpk], "subida_10_90_ns": t90 - t10,
        "FWHM_ns": t50b - t50a, "tau_cola_ns": tau, "e_tau_cola_ns": e_tau,
        "Q_SPE_fC": Q * 1e15, "G": G,
        "base_absoluta_mV": float(np.median(base[limpio])),
        "ruido_rms_mV": ruido, "ruido_rms_suav_mV": ruido_s, "SNR_SPE": delta / ruido,
        "I_calc_uA": i_calc,
    }
    eventos = {"t_trigger_s": d["t_trig"], "base_mV": base, "base_lejos_mV": base_lejos,
               "premax_mV": premax, "amp_mV": amp, "postmax_mV": postmax,
               "carga_mVns": carga_ev, "limpio": limpio.astype(int),
               "post_limpio": post_ok.astype(int), "en_forma_1PE": sel1.astype(int)}
    graf = {"t_ns": t_ns, "forma": forma, "hist": hist, "p": p, "amp_limpio": a_l,
            "dts_us": dts * 1e6, "v": v, "limpio": limpio, "base": base}
    return r, eventos, graf


# =============================================================================
#  GRÁFICOS
# =============================================================================

def estilo(ax):
    ax.grid(color=GRILLA, lw=0.6)
    ax.set_axisbelow(True)
    for s_ in ("top", "right"):
        ax.spines[s_].set_visible(False)
    for s_ in ("left", "bottom"):
        ax.spines[s_].set_color("#9c9b95")
    ax.tick_params(colors=TINTA, labelsize=8)
    ax.xaxis.label.set_color(TINTA)
    ax.yaxis.label.set_color(TINTA)
    ax.title.set_color(TINTA)


def titulo_ov(r):
    return f"{r['etiqueta']}  (V_source = {r['V_source_V']:.3f} V)"


def graficar(res, grafs, lin, salida):
    n = len(res)

    # 1) Histogramas de amplitud
    fig, axs = plt.subplots(n, 1, figsize=(7.5, 2.6 * n), squeeze=False)
    for ax, r, g in zip(axs[:, 0], res, grafs):
        h, e = g["hist"]
        xc = 0.5 * (e[1:] + e[:-1])
        ax.bar(xc, h, width=(e[1] - e[0]) * 0.85, color=COLORES[0], alpha=0.85, label="eventos limpios")
        xf = np.linspace(e[0], e[-1], 600)
        ax.plot(xf, modelo_picos(xf, *g["p"]), color=COLORES[1], lw=2, label="ajuste")
        mu0, de = g["p"][N_PICOS], g["p"][N_PICOS + 1]
        for k in range(1, N_PICOS + 1):
            ax.axvline(mu0 + k * de, color="#9c9b95", lw=0.8, ls=":")
            ax.text(mu0 + k * de, ax.get_ylim()[1] * 0.92, f"{k} PE", ha="center", fontsize=8, color=TINTA)
        ax.set_title(f"{titulo_ov(r)}   Δ(SPE) = {r['SPE_delta_mV']:.2f} ± {r['e_SPE_delta_mV']:.2f} mV   "
                     f"crosstalk ≈ {100 * r['crosstalk_aparente']:.0f} %", fontsize=9)
        ax.set_ylabel("eventos")
        estilo(ax)
    axs[-1, 0].set_xlabel("amplitud respecto de la base local [mV]")
    axs[0, 0].legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(os.path.join(salida, "histogramas_amplitud.png"), dpi=150)
    plt.close(fig)

    # 2) Forma promedio del SPE
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.6))
    for i, (r, g) in enumerate(zip(res, grafs)):
        c = COLORES[i % len(COLORES)]
        a1.plot(g["t_ns"], g["forma"], color=c, lw=2, label=f"{r['etiqueta']} ({r['eventos_1PE_forma']} ev.)")
        a2.plot(g["t_ns"], g["forma"] / r["A_forma_mV"], color=c, lw=2, label=r["etiqueta"])
    for a, yl in ((a1, "amplitud [mV]"), (a2, "normalizada al pico")):
        a.axhline(0, color="#9c9b95", lw=0.8)
        a.set_xlim(-100, 495)
        a.set_xlabel("tiempo respecto del trigger [ns]")
        a.set_ylabel(yl)
        a.legend(fontsize=8, frameon=False)
        estilo(a)
    a1.set_title("Forma promedio de eventos de 1 PE", fontsize=9)
    a2.set_title("Forma normalizada (¿cambia con V_ov?)", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(salida, "forma_spe.png"), dpi=150)
    plt.close(fig)

    # 3) Linealidad vs tensión aplicada (un panel por magnitud: nunca doble eje)
    if lin:
        claves = [("SPE_delta_mV", "Δ (SPE) [mV]"), ("A_forma_mV", "amplitud forma 1 PE [mV]"), ("G", "ganancia G (carga)")]
        fig, axs = plt.subplots(1, len(claves), figsize=(11, 3.4))
        x = np.array([r["V_source_V"] for r in res])
        for ax, (k, yl) in zip(axs, claves):
            y = np.array([r[k] for r in res])
            f = lin.get(k)
            ax.plot(x, y, "o", color=COLORES[0], ms=8)
            if f:
                xx = np.linspace(min(f["V_br"], x.min()) - 0.1, x.max() + 0.2, 50)
                ax.plot(xx, f["pendiente"] * xx + f["ordenada"], color=COLORES[1], lw=2)
                ax.axhline(0, color="#9c9b95", lw=0.8)
                txt = f"V_br = {f['V_br']:.2f}" + (f" ± {f['s_V_br']:.2f}" if np.isfinite(f["s_V_br"]) else "") + " V"
                ax.text(0.04, 0.9, txt, transform=ax.transAxes, fontsize=8, color=TINTA)
            ax.set_xlabel("V_source [V]")
            ax.set_ylabel(yl)
            estilo(ax)
        fig.tight_layout()
        fig.savefig(os.path.join(salida, "lineal_vs_vbias.png"), dpi=150)
        plt.close(fig)

    # 4) Tiempos entre triggers
    fig, axs = plt.subplots(1, n, figsize=(3.6 * n, 3.0), squeeze=False)
    for ax, r, g in zip(axs[0], res, grafs):
        dts = g["dts_us"]
        bins = np.linspace(dts.min(), np.percentile(dts, 99), 40)
        ax.hist(dts, bins=bins, color=COLORES[0], alpha=0.85)
        R = r["tasa_trigger_MHz"]
        xx = np.linspace(bins[0], bins[-1], 200)
        ax.plot(xx, len(dts) * (bins[1] - bins[0]) * R * np.exp(-R * (xx - dts.min())), color=COLORES[1], lw=2)
        ax.set_yscale("log")
        ax.set_title(f"{r['etiqueta']}: {R:.2f} MHz", fontsize=9)
        ax.set_xlabel("Δt entre triggers [µs]")
        ax.set_ylabel("segmentos")
        estilo(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(salida, "tiempos_trigger.png"), dpi=150)
    plt.close(fig)

    # 5) Ejemplos de segmentos limpios
    fig, axs = plt.subplots(n, 1, figsize=(7.5, 2.4 * n), squeeze=False)
    for i, (ax, r, g) in enumerate(zip(axs[:, 0], res, grafs)):
        idx = np.where(g["limpio"])[0][:8]
        for j in idx:
            ax.plot(g["t_ns"], g["v"][j] - g["base"][j], lw=0.6, color=COLORES[i % len(COLORES)], alpha=0.7)
        ax.set_title(f"{titulo_ov(r)}: 8 segmentos limpios (base restada)", fontsize=9)
        ax.set_ylabel("mV")
        estilo(ax)
    axs[-1, 0].set_xlabel("tiempo respecto del trigger [ns]")
    fig.tight_layout()
    fig.savefig(os.path.join(salida, "ejemplos_segmentos.png"), dpi=150)
    plt.close(fig)


# =============================================================================
#  SALIDAS DE TEXTO
# =============================================================================

def escribir_csv(ruta, filas):
    if not filas:
        return
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
        w.writeheader()
        for fila in filas:
            w.writerow({k: (f"{x:.6g}" if isinstance(x, float) else x) for k, x in fila.items()})


def resumen_txt(res, lin, salida):
    L = []
    L.append("=" * 74)
    L.append(f"  ANÁLISIS SPE — {PLACA} — R_F = {R_F:.0f} Ω — generado {datetime.now():%Y-%m-%d %H:%M}")
    L.append("=" * 74)
    L.append("Parámetros: suavizado %.1f ns | base %s ns | pre-limpio %s ns | pico %s ns | carga %s ns"
             % (SUAVIZADO_NS, VENTANA_BASE, VENTANA_PRE, VENTANA_PICO, VENTANA_CARGA))
    L.append("Cortes: pre < %.2f SPE, deriva < %.2f SPE, post < %.2f SPE; ajuste con %d picos"
             % (CORTE_PRE, CORTE_DERIVA, CORTE_POST, N_PICOS))
    for r in res:
        L.append("")
        L.append(f"── {r['etiqueta']}  ({r['archivo']}, {r['fecha_trigger']})")
        L.append(f"   V_source = {r['V_source_V']:.3f} V | paso del ADC (8 bits) {r['paso_ADC_8bit_uV']:.0f} µV | acople {r['acople']}")
        L.append(f"   eventos {r['eventos']} | limpios {r['limpios']} | 1 PE para la forma {r['eventos_1PE_forma']}"
                 f" | disparos de ruido {100 * r['frac_disparos_ruido']:.1f} %")
        L.append(f"   SPE Δ = {r['SPE_delta_mV']:.2f} ± {r['e_SPE_delta_mV']:.2f} mV   (mu1 = {r['mu1_mV']:.2f} mV,"
                 f" sesgo mu0 = {r['mu0_sesgo_mV']:+.2f} mV, σ1 = {r['sigma1_mV']:.2f} mV)")
        L.append(f"   forma 1 PE: A = {r['A_forma_mV']:.2f} mV, pico a {r['t_pico_ns']:.0f} ns, subida 10–90 % = "
                 f"{r['subida_10_90_ns']:.0f} ns, FWHM = {r['FWHM_ns']:.0f} ns, τ cola = {r['tau_cola_ns']:.0f} ± {r['e_tau_cola_ns']:.0f} ns")
        L.append(f"   carga Q = {r['Q_SPE_fC']:.0f} fC → G = {r['G']:.3g}")
        L.append(f"   crosstalk aparente = {100 * r['crosstalk_aparente']:.1f} ± {100 * r['e_crosstalk']:.1f} %")
        L.append(f"   tasa de triggers = {r['tasa_trigger_MHz']:.2f} ± {r['e_tasa_MHz']:.2f} MHz (tiempo muerto {r['t_muerto_us']:.2f} µs)")
        L.append(f"   ruido rms base = {r['ruido_rms_mV']:.2f} mV (suavizado {r['ruido_rms_suav_mV']:.2f} mV) → SNR = {r['SNR_SPE']:.1f}")
        L.append(f"   base absoluta = {r['base_absoluta_mV']:+.2f} mV")
        ib = r.get("I_bias_uA")
        L.append(f"   I calculada = tasa·G·e·(1+CT) = {r['I_calc_uA']:.2f} µA" +
                 (f"   vs medida {ib:.2f} µA (cociente {r['I_calc_uA'] / ib:.2f})" if ib else "   (I medida no cargada)"))
    if lin:
        L.append("")
        L.append("── Ajustes lineales vs V_source (M2)")
        for k, f in lin.items():
            se = f" ± {f['s_V_br']:.2f}" if np.isfinite(f["s_V_br"]) else ""
            L.append(f"   {k:14s}: pendiente {f['pendiente']:.4g}/V, V_br = {f['V_br']:.3f}{se} V, "
                     f"residuos {np.array2string(f['residuos'], precision=3)}")
        if "G" in lin:
            L.append(f"   capacidad efectiva de microcelda (dG/dV·e) = {lin['G']['pendiente'] * E_CARGA * 1e15:.0f} fF")
    L.append("")
    L.append("Notas: G es la ganancia del SISTEMA (carga que llega al TIA, sin corregir pérdidas en la red de bias).")
    L.append("       El crosstalk aparente incluye coincidencias aleatorias y pulsos retardados dentro de la ventana de pico.")
    L.append("       La tasa de triggers depende del umbral de disparo y pierde pulsos por apilamiento.")
    txt = "\n".join(L)
    with open(os.path.join(salida, "resumen.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    return txt


# =============================================================================
#  PRINCIPAL
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description="Análisis SPE de la EVB-TIA (archivos .trc en modo Sequence)")
    ap.add_argument("--dir", default=CARPETA_DATOS, help="carpeta con los .trc")
    ap.add_argument("--file", nargs=2, action="append", metavar=("ARCHIVO", "V_SOURCE"),
                    help="archivo y tensión aplicada [V]; reemplaza el bloque MEDICIONES")
    ap.add_argument("--out", default=None, help="carpeta de salida")
    a = ap.parse_args()

    meds = MEDICIONES
    if a.file:
        meds = [{"archivo": f, "V_source": float(vs_), "I_bias_uA": None,
                 "etiqueta": os.path.splitext(os.path.basename(f))[0]} for f, vs_ in a.file]
    salida = a.out or f"resultados_spe_{datetime.now():%Y%m%d_%H%M%S}"
    os.makedirs(salida, exist_ok=True)

    res, grafs = [], []
    for m in sorted(meds, key=lambda m: m["V_source"]):
        print(f"Analizando {m['archivo']} (V_source = {m['V_source']} V)...")
        try:
            r, ev, g = analizar(m, a.dir)
        except Exception as ex:
            print(f"  ✖ {ex}")
            continue
        res.append(r)
        grafs.append(g)
        n = len(ev["amp_mV"])
        escribir_csv(os.path.join(salida, f"eventos_{r['etiqueta']}.csv"),
                     [{"segmento": i, **{k: float(ev[k][i]) for k in ev}} for i in range(n)])
    if not res:
        print("No se pudo analizar ningún archivo.")
        sys.exit(1)

    lin = {}
    if len(res) >= 2:
        x = [r["V_source_V"] for r in res]
        for k in ("SPE_delta_mV", "A_forma_mV", "G"):
            lin[k] = ajuste_lineal(x, [r[k] for r in res])

    escribir_csv(os.path.join(salida, "resumen.csv"), res)
    escribir_csv(os.path.join(salida, "ajuste_lineal.csv"),
                 [{"magnitud": k, **{kk: vv for kk, vv in f.items() if kk != "residuos"}} for k, f in lin.items()])
    graficar(res, grafs, lin, salida)
    print()
    print(resumen_txt(res, lin, salida))
    print(f"\nResultados en: {os.path.abspath(salida)}")


if __name__ == "__main__":
    main()
