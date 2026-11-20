#!/usr/bin/env python3
"""
AJUSTE DEL ESPECTRO DEL Lu-176 MEDIDO CON EL MONTE CARLO DE NUESTRO CRISTAL — ajuste_mc_lu176.py — v0.2 (2026-11-12)

Modelo: N = k · Luz, con Luz = Σ_i E_i · f(z_i)   (z = 0: cara acoplada al SiPM; f promedia 1 a lo largo del cristal).
  - uniforme:    f = 1
  - lineal:      f = 1 + g·(z/L − 1/2)
  - exponencial: f ∝ exp(−z/λ)  (absorción de la luz camino al SiPM; sugerencia de Fabri, 12/11)
Ensanchamiento gaussiano σ_N = R · sqrt(511 keV · Luz) · k  (R = σ relativa a 511 keV).
Datos: eventos del Lu-176 con 216 Ω (LOG 19), entre N_MIN y N_MAX avalanchas. Necesita el .npz de mc_lu176.py v0.2.

Uso: python ajuste_mc_lu176.py <carpeta .trc> <mc.npz> <carpeta de salida>
"""
import os
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import ndtr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estilo_pi as E  # noqa: E402
import figuras_log19 as F  # noqa: E402

Q_SPE_PC, N_MIN, N_MAX, BIN, N_MC = 0.481, 400, 2600, 50, 120_000


def f_lce(modelo, z, L, p):
    if modelo == "uniforme":
        return np.ones_like(z)
    if modelo == "lineal":
        return 1 + p * (z / L - 0.5)
    a = p                                                   # a = L/λ
    return np.exp(-a * z / L) * (a / (1 - np.exp(-a)) if a > 1e-6 else 1.0)


def main(datos, mc, salida):
    F.DATOS = datos
    ev_ = [F.eventos(f, 216) for f in F.F216]
    N = np.concatenate([e[4][e[3] < F.LIM_LINEAL] / Q_SPE_PC for e in ev_])
    b = np.arange(0, 3300, BIN, dtype=float)
    h, _ = np.histogram(N, b)
    c = 0.5 * (b[1:] + b[:-1])
    m = (c > N_MIN) & (c < N_MAX)
    bb = b[m.nonzero()[0][0]:m.nonzero()[0][-1] + 2]
    d = np.load(mc)
    L = float(d["dim"][2])
    sel = d["ev"] < N_MC
    dE, dz, ev = d["dE"][sel], d["dz"][sel], d["ev"][sel]

    def esperado(modelo, k, R, p):
        luz = np.maximum(np.bincount(ev, dE * f_lce(modelo, dz, L, p), minlength=N_MC), 0.1)
        mu, sg = k * luz, np.maximum(R * np.sqrt(511.0 * luz) * k, 1e-6)
        P = ndtr((bb[None, :] - mu[:, None]) / sg[:, None])
        return np.diff(P, axis=1).sum(0)

    def nll(q, modelo):
        k, R = q[0], q[1]
        p = q[2] if modelo != "uniforme" else 0.0
        if not (1 < k < 4 and 0.02 < R < 0.5):
            return 1e12
        if (modelo == "lineal" and not -1.9 < p < 1.9) or (modelo == "exponencial" and not 0 <= p < 6):
            return 1e12
        mu = esperado(modelo, k, R, p)
        mu = np.maximum(mu / mu.sum() * h[m].sum(), 1e-12)
        return (mu - h[m] * np.log(mu)).sum()

    arranques = {"uniforme": [(2.0, 0.25), (2.0, 0.08)],
                 "lineal": [(2.05, 0.10, -0.6), (2.0, 0.06, -0.3)],
                 "exponencial": [(2.05, 0.10, 0.6), (2.05, 0.08, 1.2), (2.0, 0.12, 0.3)]}
    res = {}
    for modelo, p0s in arranques.items():
        mejor = None
        for p0 in p0s:
            r = minimize(nll, p0, args=(modelo,), method="Powell", options={"maxiter": 20000, "xtol": 1e-5, "ftol": 1e-8})
            if mejor is None or r.fun < mejor.fun:
                mejor = r
        q = mejor.x
        k, R, p = q[0], q[1], (q[2] if modelo != "uniforme" else 0.0)
        mu = esperado(modelo, k, R, p)
        mu = mu / mu.sum() * h[m].sum()
        chi2 = (((h[m] - mu) ** 2) / np.maximum(mu, 1)).sum()
        # perfil del parámetro de colección (Δ(−lnL) = 0.5)
        ip = None
        if modelo != "uniforme":
            ps = np.linspace(p - 0.3, p + 0.3, 13) if modelo == "lineal" else np.linspace(max(0, p - 0.6), p + 0.6, 13)
            pr = np.array([minimize(lambda x: nll([x[0], x[1], pp], modelo), q[:2], method="Powell").fun - mejor.fun
                           for pp in ps])
            ip = ps[pr < 0.5]
        cola = {"uniforme": 1.0, "lineal": (1 + p / 2) / (1 - p / 2), "exponencial": np.exp(-p)}[modelo]
        res[modelo] = dict(k=k, R=R, p=p, mu=mu, chi2=chi2, ngl=m.sum() - len(q), nll=mejor.fun, ip=ip, cola=cola)
        extra = ""
        if modelo == "lineal":
            extra = f"g = {p:+.2f} ({ip.min():+.2f} a {ip.max():+.2f}); "
        if modelo == "exponencial":
            extra = (f"L/λ = {p:.2f} ({ip.min():.2f}–{ip.max():.2f}) -> λ = {L / p:.0f} mm "
                     f"({L / ip.max():.0f}–{L / max(ip.min(), 1e-3):.0f}); ")
        print(f"{modelo}: k = {k:.3f} aval/keV; FWHM(511) = {235.5 * R:.0f} %; {extra}"
              f"lejano/cercano = {cola:.2f}; χ² = {chi2:.0f}/{m.sum() - len(q)}; −lnL = {mejor.fun:.1f}")

    E.aplicar("tesis")
    fig, ax = E.figura("tesis", alto=0.45)
    ax.axvspan(0, N_MIN / 1e3, color=E.GRIS, alpha=0.12, lw=0, zorder=0, label="fuera del ajuste (umbral)")
    ax.hist(N / 1e3, bins=b / 1e3, color=E.C[0], label=f"Lu-176 medido ({len(N)} eventos)")
    cm = c[m] / 1e3
    estilos = {"uniforme": E.MODELO, "lineal": dict(color=E.TINTA, lw=1.0, ls="-."),
               "exponencial": dict(color=E.C[2], lw=1.4, ls="-")}
    for modelo, r in res.items():
        ax.step(cm, r["mu"], where="mid", **estilos[modelo],
                label=f"MC, colección {modelo}: k = {r['k']:.2f} aval/keV, FWHM {235.5 * r['R']:.0f} %, "
                      f"lejano/cercano = {r['cola']:.2f}")
    ax.set_xlabel(r"Avalanchas $N$ ($10^3$), con $Q_\mathrm{SPE}$ = 481 fC (provisoria)")
    ax.set_ylabel(f"Eventos por intervalo de {BIN}")
    ax.set_xlim(0, 3.0)
    ax.set_ylim(0, 150)
    ax.legend(loc="upper right", fontsize=7)
    E.guardar(fig, "lu176_ajuste_mc_exp", salida, script=__file__, datos=F.F216)


if __name__ == "__main__":
    main(*sys.argv[1:4])
