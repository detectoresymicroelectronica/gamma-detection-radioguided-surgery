#!/usr/bin/env python3
"""
ESCALA DE ENERGÍA CON EL BORDE DE 290 keV DEL Lu-176 — escala_lu176_borde.py — v1.0 (2026-11-12)

Métodos 1 y 2 de la escala preliminar (LOG 20), sobre los eventos del Lu-176 con 216 Ω (LOG 19):
  1. Borde: punto de máxima pendiente de subida del espectro medido (histograma de 25 avalanchas suavizado),
     con bootstrap; contra la máxima pendiente del escalón de 290 keV en el modelo en keV.
  2. Forma completa: espectro = Σ_j w_j · (beta + gammas absorbidos de la combinación j), en keV, pasado a
     avalanchas con k y ensanchado (σ_N = R·sqrt(511·E)·k); k, R y las proporciones w_j libres; Poisson; perfil de k.
El modelo en keV usa lu176_escala.py (espectro beta, Compton simple, tabla 3 de Ko et al., Sensors 21, 7040, 2021).

Uso: python escala_lu176_borde.py <carpeta .trc> <carpeta de salida>
Salidas: 20_fig1_borde_290keV y 20_fig2_componentes (PDF + PNG, estilo_pi).
"""
import os
import sys

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import minimize
from scipy.special import ndtr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estilo_pi as E  # noqa: E402
import figuras_log19 as F  # noqa: E402
import lu176_escala as L  # noqa: E402

Q_SPE_PC, N_MIN, N_MAX = 0.481, 400, 2600
NOMBRES = ["β+88", "β+202+88", "β+307+88", "β+307+202+88"]


def componentes(n=2_000_000, seed=11):
    """Espectros en keV (sin ensanchar) de cada combinación de gammas absorbidos, y sus pesos del paper."""
    rng = np.random.default_rng(seed)
    e = L.beta(n, rng)
    dentro = []
    for Eg, Y, P, Ef in L.CASCADA:
        conv = rng.random(n) >= Y
        inter = ~conv & (rng.random(n) < P)
        foto = inter & (rng.random(n) < Ef)
        parc = inter & ~foto
        d = np.zeros(n)
        d[conv | foto] = Eg
        d[parc] = L.compton(Eg, parc.sum(), rng)
        e += d
        dentro.append(conv | inter)
    i307, i202, _ = dentro
    sel = [~i307 & ~i202, ~i307 & i202, i307 & ~i202, i307 & i202]
    eg = np.arange(0, 1400, 2.0)
    P = np.array([np.histogram(e[s], eg)[0] / s.sum() for s in sel])
    return 0.5 * (eg[1:] + eg[:-1]), P, np.array([s.mean() for s in sel])


def main(datos, salida):
    F.DATOS = datos
    N = np.concatenate([e[4][e[3] < F.LIM_LINEAL] / Q_SPE_PC for e in (F.eventos(f, 216) for f in F.F216)])
    ec, P, w_paper = componentes()

    # ---- método 1: borde -------------------------------------------------------------------------
    bf = np.arange(0, 3300, 25.0)
    cf = 0.5 * (bf[1:] + bf[:-1])
    z = (cf > 420) & (cf < 900)

    def max_pend(x):
        g = np.gradient(gaussian_filter1d(np.histogram(x, bf)[0].astype(float), 2))
        return cf[z][np.argmax(g[z])]

    n_borde = max_pend(N)
    rng = np.random.default_rng(5)
    boot = np.array([max_pend(rng.choice(N, len(N))) for _ in range(400)])
    bb = np.arange(0, 1300, 2.5)
    cc = 0.5 * (bb[1:] + bb[:-1])
    R0 = 0.05
    M = np.diff(ndtr((bb[None, :] - ec[:, None]) / np.maximum(R0 * np.sqrt(511 * ec), 1e-6)[:, None]), axis=1)
    g = np.gradient(gaussian_filter1d(w_paper @ (P @ M), 2))
    zz = (cc > 200) & (cc < 450)
    e_borde = cc[zz][np.argmax(g[zz])]
    k1 = n_borde / e_borde
    print(f"Método 1: máxima pendiente en {n_borde:.0f} avalanchas (bootstrap p16–p84: "
          f"{np.percentile(boot, 16):.0f}–{np.percentile(boot, 84):.0f}); en el modelo, {e_borde:.0f} keV "
          f"-> k = {k1:.2f} aval/keV")

    # ---- método 2: forma completa con proporciones libres -----------------------------------------
    b = np.arange(0, 3300, 50.0)
    h, _ = np.histogram(N, b)
    c = 0.5 * (b[1:] + b[:-1])
    m = (c > N_MIN) & (c < N_MAX)

    def base(k, R):
        mu, sg = k * ec, np.maximum(R * np.sqrt(511 * ec) * k, 1e-6)
        return P @ np.diff(ndtr((b[None, :] - mu[:, None]) / sg[:, None]), axis=1)

    def desarma(p):
        w = np.exp(p[2:])
        return p[0], p[1], w / w.sum()

    def nll(p):
        k, R, w = desarma(p)
        if not (0.02 < R < 0.35 and 1 < k < 4):
            return 1e12
        mu = (w @ base(k, R))[m]
        mu = np.maximum(mu / mu.sum() * h[m].sum(), 1e-12)
        return (mu - h[m] * np.log(mu)).sum()

    mejor = None
    for k0 in (1.8, 2.0, 2.2):
        for r0 in (0.05, 0.12):
            r = minimize(nll, np.r_[k0, r0, np.log(w_paper)], method="Powell",
                         options={"maxiter": 20000, "xtol": 1e-5, "ftol": 1e-7})
            if mejor is None or r.fun < mejor.fun:
                mejor = r
    k2, R2, w2 = desarma(mejor.x)
    ks = np.linspace(0.9 * k2, 1.1 * k2, 11)
    pr = np.array([minimize(lambda q: nll(np.r_[kk, q]), mejor.x[1:], method="Powell",
                            options={"maxiter": 8000}).fun - mejor.fun for kk in ks])
    dentro = ks[pr < 0.5]
    print(f"Método 2: k = {k2:.3f} ({dentro.min():.3f}–{dentro.max():.3f}) aval/keV; FWHM(511) = {235.5 * R2:.0f} %; "
          f"proporciones {dict(zip(NOMBRES, np.round(w2, 3)))} (paper: {dict(zip(NOMBRES, np.round(w_paper, 3)))})")

    # ---- figuras ------------------------------------------------------------------------------------
    E.aplicar("tesis")
    fig, ax = E.figura("tesis", alto=0.45)
    ax.axvspan(0, N_MIN / 1e3, color=E.GRIS, alpha=0.12, lw=0, zorder=0, label="debajo del umbral de trigger")
    ax.hist(N / 1e3, bins=b / 1e3, color=E.C[0], label=f"espectro del Lu-176 intrínseco del LYSO ({len(N)} eventos)")
    ax.axvline(n_borde / 1e3, color=E.TINTA, lw=1.2, label="borde de 290 keV (γ de 202 + 88 keV absorbidos)")
    ax.axvline(511 * k1 / 1e3, **E.LIMITE, label=f"511 keV con esa escala (~{511 * k1:.0f} avalanchas)")
    ax.set_xlim(0, 3.0)
    ax.set_ylim(0, 150)
    ax.set_xlabel(r"Avalanchas $N$ ($10^3$)")
    ax.set_ylabel("Eventos por intervalo de 50")
    top = ax.secondary_xaxis("top", functions=(lambda n: n * 1e3 / k1, lambda e: e * k1 / 1e3))
    top.set_xlabel(f"Energía depositada $E$ (keV), escala preliminar: {k1:.2f} avalanchas/keV")
    top.tick_params(direction="in", which="both")
    ax.tick_params(top=False, which="both")
    ax.legend(loc="upper right", fontsize=7)
    E.guardar(fig, "20_fig1_borde_290keV", salida, script=__file__, datos=F.F216)

    fig, (a1, a2) = E.figura("tesis", alto=0.40, columnas=2)
    for ax in (a1, a2):
        ax.axvspan(0, N_MIN / 1e3, color=E.GRIS, alpha=0.12, lw=0, zorder=0)
        ax.hist(N / 1e3, bins=b / 1e3, color=E.C[0], label="Lu-176 medido")
        ax.set_xlim(0, 3.0)
        ax.set_ylim(0, 170)
        ax.set_xlabel(r"Avalanchas $N$ ($10^3$)")
        ax.set_ylabel("Eventos por intervalo de 50")
    comp = w_paper[:, None] * base(k1, R0)
    comp /= comp.sum(0)[m].sum() / h[m].sum()
    for i, (nm, col) in enumerate(zip(NOMBRES, E.C[1:])):
        a1.step(c / 1e3, comp[i], where="mid", color=col, lw=1.0, label=nm)
    a1.step(c / 1e3, comp.sum(0), where="mid", **E.MODELO, label=f"suma (k = {k1:.2f} aval/keV)")
    a1.axvline(n_borde / 1e3, **E.LIMITE, label=f"máxima pendiente medida ({n_borde:.0f})")
    mu = w2 @ base(k2, R2)
    a2.step(c / 1e3, mu / mu[m].sum() * h[m].sum(), where="mid", **E.MODELO,
            label=f"mejor ajuste: k = {k2:.2f}, FWHM {235.5 * R2:.0f} %")
    a1.legend(loc="upper right", fontsize=6.5)
    a2.legend(loc="upper right", fontsize=6.5)
    E.paneles([a1, a2])
    E.guardar(fig, "20_fig2_componentes", salida, script=__file__, datos=F.F216)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
