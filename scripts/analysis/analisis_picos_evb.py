#!/usr/bin/env python3
"""
ANÁLISIS DE PICOS DE FOTOELECTRONES — EVB-TIA v0     analisis_picos_evb.py — v1.0 (2026-11-07)

Complementa a area_pulsos_evb.py (tiene que estar en la misma carpeta; usa su
selección de eventos limpios y su área del SPE). Para cada .trc de la carpeta:

  1. Ajuste conjunto del espectro de amplitudes por máxima verosimilitud, sin
     histograma, en el rango que no recorta el trigger ni la pantalla:
         μ_n = μ0 + n·Δ ,  σ_n² = σ0² + n·σ1²
     σ0 = ruido de la amplitud, medido aparte (misma geometría que la amplitud,
     en la zona sin pulso). Da: Δ (SPE en amplitud), σ1/Δ (dispersión de la
     ganancia entre avalanchas) y la fracción de eventos en cada pico.
     Crosstalk = P(≥2 PE)/P(≥1 PE) = 1 − w1. Las fracciones corrigen el
     truncamiento del rango (supuesto: picos gaussianos).
  2. Bandas por pico: media del área de los eventos de 1 PE y de 2 PE
     (|amp − μ_n| < σ_n) → linealidad del área: área(2 PE)/área(1 PE) = 2.
  3. Tasa de cuentas oscuras desde los tiempos de trigger (exponencial
     desplazada por el tiempo muerto), descontando los disparos de ruido y
     corrigiendo el 1 PE que quedó debajo del trigger.
  4. Forma del pulso de 1 PE (eventos limpios): pico, subida 10–90 %, FWHM,
     constante de la cola.

V_ov (token Vov) y R_F (token RF) se leen del nombre del archivo.

Uso: python analisis_picos_evb.py  [carpeta]   (por defecto, la del script)
Salida: tabla en la consola y figuras picos_*.png en la carpeta.
"""

import os
import re
import struct
import sys

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.optimize import curve_fit, minimize
from scipy.special import logsumexp, ndtr

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import area_pulsos_evb as A  # noqa: E402

E = 1.602176634e-19
COLORES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#8c6bd6", "#3b3b3a", "#9c9b95"]


# ============================================================================
#  Ajuste conjunto por máxima verosimilitud
# ============================================================================
def _param(th, N, s0):
    mu0, de, ls1 = th[:3]
    s1 = np.exp(ls1)
    lw = np.concatenate([[0.0], th[3:3 + N - 1]])
    w = np.exp(lw - logsumexp(lw))
    n = np.arange(1, N + 1)
    return mu0 + n * de, np.sqrt(s0 ** 2 + n * s1 ** 2), w, mu0, de, s1


def _nll(th, x, lo, hi, N, s0):
    mu, sg, w, *_ = _param(th, N, s0)
    with np.errstate(divide="ignore"):
        lg = -0.5 * ((x[:, None] - mu) / sg) ** 2 - np.log(sg) - 0.5 * np.log(2 * np.pi) + np.log(w)
    P = ndtr((hi - mu) / sg) - ndtr((lo - mu) / sg)
    return -(logsumexp(lg, axis=1).sum() - len(x) * np.log((w * P).sum()))


def _hess(f, th, eps):
    k = len(th)
    H = np.zeros((k, k))
    h = eps * np.maximum(1, np.abs(th))
    for i in range(k):
        for j in range(i, k):
            a, b = np.zeros(k), np.zeros(k)
            a[i], b[j] = h[i], h[j]
            H[i, j] = H[j, i] = (f(th + a + b) - f(th + a - b) - f(th - a + b) + f(th - a - b)) / (4 * h[i] * h[j])
    return H


def ajuste_conjunto(x, mu1, s_nucleo, lo, hi, s0):
    x = x[(x > lo) & (x < hi)]
    N = int(np.clip(np.floor((hi - 0.5 * mu1) / mu1) + 1, 2, 6))
    cnt = [max(((x > (n - 0.5) * mu1) & (x < (n + 0.5) * mu1)).sum(), 1) for n in range(1, N + 1)]
    s1_ini = max(np.sqrt(max(s_nucleo ** 2 - s0 ** 2, 0)), 0.2 * s_nucleo)
    th0 = np.array([0.0, mu1, np.log(s1_ini)] + list(np.log(np.array(cnt[1:]) / cnt[0])))
    f = lambda th: _nll(th, x, lo, hi, N, s0)
    r = minimize(f, th0, method="Nelder-Mead", options={"maxiter": 40000, "maxfev": 40000, "xatol": 1e-7, "fatol": 1e-9})
    r2 = minimize(f, r.x, method="BFGS")
    th = r2.x if r2.fun <= r.fun else r.x
    C = np.full((len(th), len(th)), np.nan)
    for eps in (1e-4, 1e-3, 3e-3, 1e-2):
        try:
            Ct = np.linalg.inv(_hess(f, th, eps))
        except np.linalg.LinAlgError:
            continue
        if np.all(np.isfinite(Ct)) and np.all(np.diag(Ct) > 0):
            C = Ct
            break
    deriv = lambda t_: (lambda p: np.array([p[4], p[5], 1 - p[2][0], p[3]]))(_param(t_, N, s0))
    J = np.zeros((4, len(th)))
    for k in range(len(th)):
        d = np.zeros(len(th))
        d[k] = 1e-6 * max(1, abs(th[k]))
        J[:, k] = (deriv(th + d) - deriv(th - d)) / (2 * d[k])
    err = np.sqrt(np.clip(np.diag(J @ C @ J.T), 0, None))
    mu, sg, w, mu0, de, s1 = _param(th, N, s0)
    return {"N": N, "x": x, "lo": lo, "hi": hi, "mu": mu, "sg": sg, "w": w, "mu0": mu0, "Delta": de, "eDelta": err[0],
            "s1": s1, "es1": err[1], "xt": 1 - w[0], "ext": err[2], "s0": s0}


# ============================================================================
#  Auxiliares
# ============================================================================
def t_trigger(ruta):
    with open(ruta, "rb") as fh:
        d = fh.read()
    d = d[d.find(b"WAVEDESC"):]
    end = "<" if struct.unpack("<h", d[34:36])[0] == 1 else ">"
    u = lambda f, o: struct.unpack(end + f, d[o:o + struct.calcsize(end + f)])[0]
    wdl, ut, trt = u("l", 36), u("l", 40), u("l", 48)
    return np.frombuffer(d[wdl + ut:wdl + ut + trt], dtype=end + "f8").reshape(-1, 2)[:, 0] if trt > 0 else None


def vov_del_nombre(f):
    m = re.search(r"Vov(\d+(?:p\d+)?)", os.path.basename(f))
    return float(m.group(1).replace("p", ".")) if m else np.nan


def ruido_amplitud(ev):
    """σ de la amplitud sin pulso: v_suav(t) − media de una ventana de base, con la
    misma separación que pico−base, en la zona previa al pulso."""
    v, t, tcf = ev["v"], ev["t"], ev["t_cf"]
    vs = uniform_filter1d(v, max(1, round(A.SUAVIZADO_NS / (t[1] - t[0]))), axis=1, mode="nearest")
    largo = A.BASE_NS[1] - A.BASE_NS[0]
    sep = 30.0 - A.BASE_NS[1]                      # del fin de la base al pico (~30 ns después de t_cf)
    x = []
    for i in np.nonzero(ev["sin_previos"])[0]:
        b0 = tcf[i] + A.PRE_LIMPIO_NS[0]
        if b0 + largo + sep > tcf[i] + A.BASE_NS[0]:
            continue
        i0, i1, ip = np.searchsorted(t, [b0, b0 + largo, b0 + largo + sep])
        x.append(vs[i, ip] - v[i, i0:i1].mean())
    return A.escala(np.array(x))


def forma_metricas(tr, fo):
    k = int(np.nanargmax(np.where((tr > -20) & (tr < 100), fo, -np.inf)))
    pk = fo[k]
    cruce = lambda nivel, desde, paso: next((tr[j] + (nivel - fo[j]) * (tr[j + paso] - tr[j]) / (fo[j + paso] - fo[j])
                                             for j in range(desde, 0 if paso < 0 else len(tr) - 1, paso)
                                             if (fo[j] - nivel) * (fo[j + paso] - nivel) <= 0 and fo[j + paso] != fo[j]), np.nan)
    t10, t90 = cruce(0.1 * pk, k, -1), cruce(0.9 * pk, k, -1)
    fw = cruce(0.5 * pk, k, 1) - cruce(0.5 * pk, k, -1)
    m = (tr > tr[k] + 10) & (tr < A.INTEGRACION_NS[1]) & np.isfinite(fo)
    try:
        p, c = curve_fit(lambda x, a, tau: a * np.exp(-(x - tr[k]) / tau), tr[m], fo[m], p0=[pk, 60.0])
        tau = p[1]
    except Exception:
        tau = np.nan
    return {"t_pico": tr[k], "subida": t90 - t10, "FWHM": fw, "tau": tau}


# ============================================================================
def analizar(ruta):
    r, motivo = A.analizar(ruta)
    if r is None or r["f1"] is None or r["res"] is None:
        return None, motivo or "pico de 1 PE no encontrado"
    ev = r["ev"]
    mu1c, s1c = r["f1"][0], r["f1"][1]
    if s1c / mu1c > A.RESOLUCION_MAX:
        return None, f"pico de 1 PE no separado del ruido (σ1/μ1 = {s1c / mu1c:.2f})"
    out = {"Vov": vov_del_nombre(ruta), "rf": r["rf"], "area_spe": r["res"][0], "earea_spe": r["res"][1],
           "k_fC": 1e3 / (r["rf"] * r["f_term"])}

    # 1. ajuste conjunto
    lim = ev["limpio"]
    valido = np.isfinite(ev["area"]) & ~ev["sat"]
    s0 = ruido_amplitud(ev)
    piso = np.percentile(ev["amp"][valido], 2)
    hi = ev["vtope"] - np.nanmedian(ev["base"][lim]) - 2.0
    lo = max(0.55 * mu1c, piso + 2 * s0)
    m = ajuste_conjunto(ev["amp"][lim], mu1c, s1c, lo, hi, s0)
    out["fit"] = m

    # 2. bandas por pico (área de 1 y 2 PE) y contaminación de la banda de 2 PE
    areas = {}
    for n in (1, 2):
        if n > m["N"]:
            continue
        b = lim & (np.abs(ev["amp"] - m["mu"][n - 1]) < m["sg"][n - 1])
        x = ev["area"][b]
        if len(x) >= 10:
            areas[n] = (x.mean(), x.std(ddof=1) / np.sqrt(len(x)), len(x))
    if 2 in areas:
        lo2, hi2 = m["mu"][1] - m["sg"][1], m["mu"][1] + m["sg"][1]
        P = m["w"] * (ndtr((hi2 - m["mu"]) / m["sg"]) - ndtr((lo2 - m["mu"]) / m["sg"]))
        out["contam2"] = 1 - P[1] / P.sum()
        a1, e1 = areas[1][:2]
        a2, e2 = areas[2][:2]
        out["lin"] = (a2 / a1, (a2 / a1) * np.hypot(e1 / a1, e2 / a2))
    out["areas"] = areas

    # 3. tasa de cuentas oscuras
    tt = t_trigger(ruta)
    if tt is not None and len(tt) > 10:
        d = np.diff(tt)
        tasa = 1.0 / np.mean(d - d.min())
        f_ruido = np.mean(ev["amp"][valido] < 0.5 * m["Delta"])
        # fracción del 1 PE por debajo del piso del trigger (con el modelo ajustado)
        perdida = m["w"][0] * ndtr((piso - m["mu"][0]) / m["sg"][0])
        out["tasa"] = tasa * (1 - f_ruido) / (1 - perdida)
        out["tasa_trig"] = tasa
        out["e_tasa"] = out["tasa"] / np.sqrt(len(d))

    # 4. forma
    out["forma"] = (r["tr"], r["forma"], forma_metricas(r["tr"], r["forma"]))
    return out, None


# ============================================================================
def main():
    carpeta = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    archivos = sorted(f for f in os.listdir(carpeta) if f.lower().endswith(".trc"))
    res = []
    for f in archivos:
        try:
            o, motivo = analizar(os.path.join(carpeta, f))
        except Exception as ex:
            o, motivo = None, f"error: {ex}"
        if o is None:
            print(f"{f}: {motivo}")
            continue
        o["archivo"] = f
        res.append(o)
    if not res:
        sys.exit("Nada para analizar.")
    res.sort(key=lambda o: (-o["rf"], o["Vov"]))

    print(f"\n{'archivo':28s} {'Vov':>4s} {'R_F':>5s} | {'Δ amp [mV]':>12s} {'σ1/Δ':>6s} {'crosstalk':>11s} | "
          f"{'área 2PE/1PE':>13s} {'contam.':>7s} | {'tasa [MHz]':>11s} {'(trigger)':>9s} | "
          f"{'subida':>6s} {'FWHM':>5s} {'τ cola':>6s}")
    for o in res:
        m = o["fit"]
        lin = f"{o['lin'][0]:.2f} ± {o['lin'][1]:.2f}" if "lin" in o else "—"
        con = f"{100 * o['contam2']:.0f} %" if "contam2" in o else "—"
        tasa = f"{o['tasa'] / 1e6:.2f} ± {o['e_tasa'] / 1e6:.2f}" if "tasa" in o else "—"
        fm = o["forma"][2]
        print(f"{o['archivo']:28s} {o['Vov']:4.1f} {o['rf']:5.0f} | {m['Delta']:5.2f} ± {m['eDelta']:4.2f} {m['s1'] / m['Delta']:6.3f} "
              f"{100 * m['xt']:4.1f} ± {100 * m['ext']:3.1f} % | {lin:>13s} {con:>7s} | {tasa:>11s} {o.get('tasa_trig', np.nan) / 1e6:9.2f} | "
              f"{fm['subida']:5.0f}  {fm['FWHM']:4.0f}  {fm['tau']:5.0f}")
    figuras(res, carpeta)
    print(f"\nFiguras: {os.path.join(carpeta, 'picos_*.png')}")


def estilo(ax):
    ax.grid(color="#e4e3df", lw=0.6)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=8)


def figuras(res, carpeta):
    # 1. espectros de amplitud con el ajuste conjunto
    n = len(res)
    fig, axs = plt.subplots(n, 1, figsize=(8, 1.9 * n))
    axs = np.atleast_1d(axs)
    for ax, o in zip(axs, res):
        m = o["fit"]
        x = m["x"]
        bw = m["Delta"] / 15
        e = np.arange(m["lo"], m["hi"] + bw, bw)
        ax.hist(x, bins=e, color=COLORES[0], alpha=0.85)
        xf = np.linspace(m["lo"], m["hi"], 600)
        dens = sum(w_ * np.exp(-0.5 * ((xf - mu_) / s_) ** 2) / (s_ * np.sqrt(2 * np.pi))
                   for w_, mu_, s_ in zip(m["w"], m["mu"], m["sg"]))
        P = (m["w"] * (ndtr((m["hi"] - m["mu"]) / m["sg"]) - ndtr((m["lo"] - m["mu"]) / m["sg"]))).sum()
        ax.plot(xf, dens / P * len(x) * bw, color="#3b3b3a", lw=1.6)
        ax.set_title(f"{o['archivo']}   Δ = {m['Delta']:.2f} mV   crosstalk = {100 * m['xt']:.1f} %", fontsize=8)
        estilo(ax)
    axs[-1].set_xlabel("amplitud [mV] (rango del ajuste)")
    fig.tight_layout()
    fig.savefig(os.path.join(carpeta, "picos_ajustes.png"), dpi=120)
    plt.close(fig)

    # 2. magnitudes contra V_ov (R_F = 2200 Ω), un panel por magnitud
    sub = [o for o in res if o["rf"] == 2200]
    if len(sub) >= 2:
        V = np.array([o["Vov"] for o in sub])
        paneles = [
            ("Crosstalk [%]", [100 * o["fit"]["xt"] for o in sub], [100 * o["fit"]["ext"] for o in sub]),
            ("Tasa de cuentas oscuras [MHz]", [o.get("tasa", np.nan) / 1e6 for o in sub], [o.get("e_tasa", np.nan) / 1e6 for o in sub]),
            ("Dispersión de ganancia σ1/Δ", [o["fit"]["s1"] / o["fit"]["Delta"] for o in sub],
             [o["fit"]["s1"] / o["fit"]["Delta"] * np.hypot(o["fit"]["es1"] / o["fit"]["s1"], o["fit"]["eDelta"] / o["fit"]["Delta"]) for o in sub]),
            ("Área 2 PE / área 1 PE", [o["lin"][0] if "lin" in o else np.nan for o in sub], [o["lin"][1] if "lin" in o else np.nan for o in sub]),
        ]
        fig, axs = plt.subplots(1, len(paneles), figsize=(3.1 * len(paneles), 3.0))
        for ax, (tit, y, ey) in zip(axs, paneles):
            ax.errorbar(V, y, yerr=ey, fmt="o", color=COLORES[0], ms=6, capsize=3)
            if tit.startswith("Área"):
                ax.axhline(2, color="#9c9b95", lw=1, ls="--")
            ax.set_title(tit, fontsize=9)
            ax.set_xlabel("V_ov nominal [V]", fontsize=8)
            estilo(ax)
        fig.tight_layout()
        fig.savefig(os.path.join(carpeta, "picos_vs_Vov.png"), dpi=150)
        plt.close(fig)

        # 3. forma del pulso de 1 PE normalizada, contra V_ov
        fig, ax = plt.subplots(figsize=(7.5, 3.6))
        for c, o in zip(COLORES, sub):
            tr, fo, fm = o["forma"]
            ax.plot(tr, fo / np.nanmax(fo[(tr > -20) & (tr < 100)]), color=c, lw=1.8, label=f"V_ov {o['Vov']:g} V")
        ax.axhline(0, color="#9c9b95", lw=0.8)
        ax.set_xlim(-60, A.INTEGRACION_NS[1])
        ax.set_xlabel("tiempo respecto de t_cf [ns]")
        ax.set_ylabel("normalizado al pico")
        ax.set_title("Pulso promedio de 1 PE (eventos limpios), R_F = 2200 Ω", fontsize=9)
        ax.legend(fontsize=7, frameon=False, ncol=2)
        estilo(ax)
        fig.tight_layout()
        fig.savefig(os.path.join(carpeta, "picos_forma.png"), dpi=150)
        plt.close(fig)


if __name__ == "__main__":
    main()
