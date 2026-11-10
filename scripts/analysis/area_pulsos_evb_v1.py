#!/usr/bin/env python3
"""
ÁREA DEL FOTOELECTRÓN — EVB-TIA v0          area_pulsos_evb_v1.py — v1.0 (2026-11-05)

Copia del algoritmo v1.0, conservada solo para regenerar las figuras del LOG 18 que
lo usan (espectros preliminares y forma promedio con la meseta). Es idéntica a la v1.0
salvo que analizar() devuelve además: fa, sigma_s, ruido, base_abs, amp_l, n1.
El algoritmo vigente es area_pulsos_evb.py v2.0.

Para cada .trc (modo SEQUENCE) de la carpeta:
  - en la consola: el área del SPE (mV·ns y fC) con su incertidumbre;
  - una figura <archivo>_area.png para verificarla:
      izquierda: espectro de áreas de los eventos limpios con el ajuste;
      derecha:   pulso promedio de 1 PE con la base y la integración marcadas.

Uso: copiar el script en la carpeta de los .trc y correr
        python area_pulsos_evb.py
     o  python area_pulsos_evb.py "D:/datos/2026-11-06"

R_F se lee del nombre del archivo (token RF2200, RF1269, ...); si no está,
se usa R_F_POR_DEFECTO.

Método:
  - Las ventanas se miden desde t_cf: el instante en que cada pulso cruza el
    50 % de su amplitud (no desde el trigger, que depende del umbral).
  - Área = Σ (v − base) · dt entre t_cf + INICIO y t_cf + FIN.
  - Evento limpio: sin pulsos previos ni deriva de la base. Los cortes son
    max(k·σ_ruido, f·SPE), así no caen dentro del ruido cuando el SPE es chico.
  - Área del SPE = separación entre picos del espectro de áreas
    (gaussianas equiespaciadas μ_n = μ0 + n·Δ).
  - Carga = área / (R_F · f_term); f_term = divisor R10 / (R11 ∥ entrada).
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
SUAVIZADO_NS = 5.0                          # solo para máximos y t_cf; el área usa la señal cruda
FRACCION_CFD = 0.5
BUSQUEDA_PICO_TRIG_NS = (0.0, 100.0)        # respecto del TRIGGER
BASE_PROVISORIA_TRIG_NS = (-110.0, -50.0)   # respecto del TRIGGER, solo para t_cf
BASE_NS = (-110.0, -40.0)
BASE_LEJOS_NS = (-400.0, -330.0)
PRE_LIMPIO_NS = (-400.0, -40.0)
INTEGRACION_NS = (-40.0, 250.0)
CORTE_PRE = (4.0, 0.4)       # (k, f): máximo antes del pulso − base < max(k·σ, f·SPE)
CORTE_DERIVA = (1.0, 0.3)    # |base lejana − base|                   < max(k·σ, f·SPE)
N_PICOS = 3
SEL_1PE_SIGMAS = 1.5         # 1 PE para el pulso promedio: |amp − μ1| < k·σ1
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


def picos(x, *p):
    n = len(p) - 4
    mu0, de, s0, s1 = p[n:]
    return sum(p[k - 1] * np.exp(-0.5 * (x - mu0 - k * de) ** 2 / (s0 ** 2 + k * s1 ** 2)) for k in range(1, n + 1))


def ajustar(x, semilla):
    """Espectro con N picos equiespaciados. Devuelve (p, error de Δ, histograma) o None."""
    x = x[np.isfinite(x)]
    if not np.isfinite(semilla) or semilla <= 0 or len(x) < 50:
        return None
    h, e = np.histogram(x, bins=np.arange(0.4 * semilla, (N_PICOS + 1.2) * semilla, semilla / 20))
    xc = 0.5 * (e[1:] + e[:-1])
    p0 = [max(h.max(), 1) * 0.25 ** k for k in range(N_PICOS)] + [0, semilla, 0.1 * semilla, 0.1 * semilla]
    lo = [0] * N_PICOS + [-0.5 * semilla, 0.5 * semilla, 0, 0]
    hi = [np.inf] * N_PICOS + [0.5 * semilla, 1.5 * semilla, semilla, semilla]
    try:
        p, c = curve_fit(picos, xc, h, p0=p0, bounds=(lo, hi), sigma=np.sqrt(np.maximum(h, 1)), maxfev=50000)
    except Exception:
        return None
    return p, np.sqrt(c[N_PICOS + 1, N_PICOS + 1]), (h, e)


def semilla_spe(amp, sigma):
    """Pico más alto del histograma de amplitudes por encima de 3σ."""
    a = amp[np.isfinite(amp) & (amp > 3 * sigma)]
    if len(a) < 50:
        return np.nan
    e = np.arange(3 * sigma, np.percentile(a, 99) + sigma / 3, sigma / 3)
    if len(e) < 5:
        return np.nan
    h = gaussian_filter1d(np.histogram(a, bins=e)[0].astype(float), 1.5)
    k, _ = find_peaks(h, prominence=0.05 * h.max())
    if not len(k):
        return np.nan
    j = k[np.argmax(h[k])]
    return 0.5 * (e[j] + e[j + 1])


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

    # base, amplitud, cortes y área, todo respecto de t_cf
    base, amp, pre, der, area = (np.full(nseg, np.nan) for _ in range(5))
    for i in np.nonzero(np.isfinite(t_cf))[0]:
        tc = t_cf[i]
        if tc + BASE_LEJOS_NS[0] < t[0] or tc + INTEGRACION_NS[1] > t[-1]:
            continue
        base[i] = v[i, ix(tc + BASE_NS[0]):ix(tc + BASE_NS[1])].mean()
        amp[i] = vs[i, kpk[i]] - base[i]
        pre[i] = vs[i, ix(tc + PRE_LIMPIO_NS[0]):ix(tc + PRE_LIMPIO_NS[1])].max() - base[i]
        der[i] = v[i, ix(tc + BASE_LEJOS_NS[0]):ix(tc + BASE_LEJOS_NS[1])].mean() - base[i]
        area[i] = (v[i, ix(tc + INTEGRACION_NS[0]):ix(tc + INTEGRACION_NS[1])] - base[i]).sum() * dt

    # ruido (robusto) y selección en dos pasadas con el SPE de amplitud
    sigma = np.median(vs[:, b0:b1].std(1))
    spe = semilla_spe(amp, sigma)
    fa = None
    for _ in range(2):
        u = lambda kf: max(kf[0] * sigma, kf[1] * spe) if np.isfinite(spe) else kf[0] * sigma
        limpio = np.isfinite(area) & (pre < u(CORTE_PRE)) & (np.abs(der) < u(CORTE_DERIVA))
        fa = ajustar(amp[limpio], spe)
        if fa is None:
            break
        spe = fa[0][N_PICOS + 1]

    # área del SPE: separación entre picos del espectro de áreas
    fq, es1 = None, np.zeros(nseg, bool)
    if fa is not None:
        mu1 = fa[0][N_PICOS] + fa[0][N_PICOS + 1]
        s1 = np.hypot(fa[0][N_PICOS + 2], fa[0][N_PICOS + 3])
        es1 = limpio & (np.abs(amp - mu1) < SEL_1PE_SIGMAS * s1)
        if es1.sum() >= 10:
            fq = ajustar(area[limpio], np.nanmean(area[es1]))

    # pulso promedio de 1 PE alineado en t_cf (para ver las ventanas)
    tr = np.arange(PRE_LIMPIO_NS[0], INTEGRACION_NS[1] + 100, dt)
    forma = None
    if es1.sum() >= 10:
        forma = np.nanmean([np.interp(tr + t_cf[i], t, v[i] - base[i], left=np.nan, right=np.nan)
                            for i in np.nonzero(es1)[0]], 0)

    rin = R_ENTRADA.get(acople, 1e6)
    z = R11 * rin / (R11 + rin)
    ruido_raw = np.median([v[i, ix(t_cf[i] + PRE_LIMPIO_NS[0]):ix(t_cf[i] + PRE_LIMPIO_NS[1])].std()
                           for i in np.nonzero(limpio)[0]]) if limpio.any() else np.nan
    base_abs = np.nanmedian(base[limpio]) if limpio.any() else np.nan
    return {"fa": fa, "sigma_s": sigma, "ruido": ruido_raw, "base_abs": base_abs, "amp_l": amp[limpio],
            "nseg": nseg, "limpios": int(limpio.sum()), "fq": fq, "area_l": area[limpio], "tr": tr,
            "forma": forma, "n1": int(es1.sum()), "rf": r_f_del_nombre(ruta), "acople": acople,
            "f_term": z / (R10 + z)}, None


def figura(ruta, r):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.8))
    if r["fq"] is not None:
        p, ed, (h, e) = r["fq"]
        xc = 0.5 * (e[1:] + e[:-1])
        a1.bar(xc, h, width=(e[1] - e[0]) * 0.85, color="#2a78d6")
        xf = np.linspace(e[0], e[-1], 600)
        a1.plot(xf, picos(xf, *p), color="#3b3b3a", lw=1.6)
        for k in range(1, N_PICOS + 1):
            a1.axvline(p[N_PICOS] + k * p[N_PICOS + 1], color="#9c9b95", lw=0.8, ls=":")
        a1.set_title(f"Área del SPE = {p[N_PICOS + 1]:.1f} ± {ed:.1f} mV·ns", fontsize=10)
    else:
        a1.hist(r["area_l"][np.isfinite(r["area_l"])], bins=80, color="#2a78d6")
        a1.set_title("Espectro de áreas: picos no resueltos", fontsize=10)
    a1.set_xlabel("área [mV·ns]")
    a1.set_ylabel(f"eventos limpios ({r['limpios']}/{r['nseg']})")
    if r["forma"] is not None:
        a2.axvspan(*BASE_NS, color="#d9d8d3", lw=0, label="base")
        a2.axvspan(*INTEGRACION_NS, color="#cfe0f5", lw=0, label="integración")
        a2.plot(r["tr"], r["forma"], color="#2a78d6", lw=2, label=f"promedio de {r['n1']} eventos de 1 PE")
        a2.axhline(0, color="#9c9b95", lw=0.8)
        a2.legend(fontsize=8, frameon=False, loc="upper right")
    a2.set_title("Pulso de 1 PE y ventanas", fontsize=10)
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
        if r["fq"] is None:
            print(f"{f}: picos no resueltos ({r['limpios']}/{r['nseg']} limpios); ver la figura")
            continue
        a, ea = r["fq"][0][N_PICOS + 1], r["fq"][1]
        k = 1e3 / (r["rf"] * r["f_term"])           # mV·ns → fC
        print(f"{f}: área SPE = {a:.1f} ± {ea:.1f} mV·ns → {a * k:.0f} ± {ea * k:.0f} fC "
              f"(G = {a * k * 1e-15 / E:.3g}) | R_F = {r['rf']:.0f} Ω, {r['acople']} | "
              f"{r['limpios']}/{r['nseg']} limpios")


if __name__ == "__main__":
    main()
