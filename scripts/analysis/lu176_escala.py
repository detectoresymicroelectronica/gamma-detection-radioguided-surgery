#!/usr/bin/env python3
"""
ESCALA DE ENERGÍA PRELIMINAR CON EL Lu-176 INTRÍNSECO — lu176_escala.py — v0.1 (2026-11-12)

Usa los eventos del Lu-176 con R_F = 216 Ω (LOG 19) y el modelo de espectro de
Ko et al., Sensors 21, 7040 (2021) para estimar la escala avalanchas/keV.

  A. Rasgos del espectro: máximo y hombro de nuestro espectro contra las energías
     de esos rasgos en el espectro medido del paper (fig. 6, LYSO 3×3×20 mm³).
  B. Ajuste con plantilla: espectro = beta + depósitos de la cascada (307, 202, 88 keV),
     con las probabilidades de la tabla 3 del paper (su cristal), Compton simplificado,
     ensanchamiento gaussiano. Parámetros libres: escala k (avalanchas/keV) y resolución.

Uso: python lu176_escala.py <carpeta con los .trc> <carpeta de salida>
PRELIMINAR: ~2000 eventos, probabilidades de otro cristal, Compton simplificado.
"""
import os
import sys

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import minimize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estilo_pi as E  # noqa: E402
import figuras_log19 as F  # noqa: E402

Q_SPE_PC = 0.481
N_MIN, N_MAX = 400, 2600        # avalanchas: por encima del umbral de los dos archivos y debajo del límite lineal
BIN = 50
ME, ALFA, Z, T0, T_MEDIA = 511.0, 1 / 137.036, 72, 593.0, 182.0
# Paper, tabla 3 (LYSO 3×3×20 mm³): rendimiento gamma Y, probabilidad de interacción P, fracción de fotopico E
CASCADA = [(307.0, 0.931, 0.224, 0.641), (202.0, 0.780, 0.399, 0.818), (88.0, 0.146, 0.863, 0.909)]
# Paper, fig. 6 (espectro medido): energía del máximo y del hombro (lectura del gráfico)
E_MAX_PAPER, E_HOMBRO_PAPER = (320.0, 360.0), (580.0, 620.0)
# Cadena del LOG 9 para convertir a LCE
LY_511, PDE, XT = 16350.0, 0.31, 0.125


def beta(n, rng):
    """Espectro beta del Lu-176: forma permitida con función de Fermi (Z = 72) y un factor
    lineal (1 - c·T) ajustado para que la media sea 182 keV (Basunia, NDS 2006)."""
    T = np.linspace(0.25, T0 - 0.25, 4000)
    W, W0 = 1 + T / ME, 1 + T0 / ME
    p = np.sqrt(W ** 2 - 1)
    eta = ALFA * Z * W / p
    base = 2 * np.pi * eta / (1 - np.exp(-2 * np.pi * eta)) * p * W * (W0 - W) ** 2
    def media(c):
        s = base * np.clip(1 - c * T, 0, None)
        return (s * T).sum() / s.sum()
    c = minimize(lambda c: (media(c[0]) - T_MEDIA) ** 2, [0.0005], method="Nelder-Mead").x[0]
    s = base * np.clip(1 - c * T, 0, None)
    return rng.choice(T, size=n, p=s / s.sum())


def compton(Eg, n, rng):
    """Depósito de una única dispersión Compton (Klein-Nishina), de 0 al borde Compton."""
    th = np.linspace(0, np.pi, 4000)
    k = Eg / ME
    r = 1 / (1 + k * (1 - np.cos(th)))
    dsig = r ** 2 * (r + 1 / r - np.sin(th) ** 2) * np.sin(th)
    t = rng.choice(th, size=n, p=dsig / dsig.sum())
    return Eg * (1 - 1 / (1 + k * (1 - np.cos(t))))


def depositos(n=1_000_000, seed=1, escala_P=1.0):
    """Energía total depositada por decaimiento (keV)."""
    rng = np.random.default_rng(seed)
    e = beta(n, rng)
    for Eg, Y, P, Ef in CASCADA:
        P = min(P * escala_P, 0.99)
        u = rng.random(n)
        conv = u >= Y                               # conversión interna: depósito local
        gam = ~conv
        v = rng.random(n)
        inter = gam & (v < P)
        foto = inter & (rng.random(n) < Ef)
        parcial = inter & ~foto
        d = np.zeros(n)
        d[conv | foto] = Eg
        d[parcial] = compton(Eg, parcial.sum(), rng)
        e += d
    return e


def histo_modelo(dep, k, R, bordes):
    """Espectro esperado en avalanchas: N = k·E, σ_E/E = R·sqrt(511/E) (R = σ relativa a 511 keV)."""
    N = k * dep
    sig = R * np.sqrt(511.0 * np.maximum(dep, 1.0)) * k
    rng = np.random.default_rng(7)
    Nb = N + rng.standard_normal(len(N)) * sig
    h, _ = np.histogram(Nb, bordes)
    return h / len(dep)


def ajustar(datos, dep, bordes, m):
    def nll(p):
        k, R = p
        if k <= 0 or not (0.01 < R < 0.4):
            return 1e12
        mu = histo_modelo(dep, k, R, bordes)[m]
        mu = mu / mu.sum() * datos[m].sum()
        mu = np.maximum(mu, 1e-9)
        return (mu - datos[m] * np.log(mu)).sum()
    mejor = None
    for k0 in (1.5, 2.0, 2.5, 3.0):
        r = minimize(nll, [k0, 0.08], method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-3})
        if mejor is None or r.fun < mejor.fun:
            mejor = r
    # incertidumbre de k por perfil de verosimilitud (Δ(-lnL) = 0.5), R reoptimizada
    k0, R0 = mejor.x
    def perfil(k):
        return minimize(lambda R: nll([k, R[0]]), [R0], method="Nelder-Mead").fun - mejor.fun
    ks = np.linspace(0.85 * k0, 1.15 * k0, 31)
    pr = np.array([perfil(k) for k in ks])
    dentro = ks[pr < 0.5]
    return mejor.x, (dentro.min(), dentro.max()), nll


def main(datos_dir, salida):
    F.DATOS = datos_dir
    N = np.concatenate([F.eventos(f, 216)[4][F.eventos(f, 216)[3] < F.LIM_LINEAL] / Q_SPE_PC for f in F.F216])
    bordes = np.arange(0, 3300, BIN)
    h, _ = np.histogram(N, bordes)
    c = 0.5 * (bordes[1:] + bordes[:-1])
    m = (c > N_MIN) & (c < N_MAX)
    print(f"eventos: {len(N)}; en el rango del ajuste ({N_MIN}-{N_MAX} avalanchas): {h[m].sum()}")

    # --- A: rasgos del espectro medido -----------------------------------------
    hs = gaussian_filter1d(h.astype(float), 2)
    imax = np.argmax(np.where(m, hs, -1))
    d2 = np.gradient(np.gradient(hs))
    zona = m & (c > c[imax] + 300)
    ihom = np.argmax(np.where(zona, d2, -np.inf))      # hombro: mayor curvatura positiva tras el máximo
    kA_max = c[imax] / np.array(E_MAX_PAPER)
    kA_hom = c[ihom] / np.array(E_HOMBRO_PAPER)
    print(f"A. máximo en ~{c[imax]:.0f} avalanchas -> k = {kA_max.min():.2f}–{kA_max.max():.2f} aval/keV")
    print(f"   hombro en ~{c[ihom]:.0f} avalanchas -> k = {kA_hom.min():.2f}–{kA_hom.max():.2f} aval/keV")

    # --- B: ajuste con plantilla --------------------------------------------------
    res = {}
    for nombre, eP in [("tabla 3 del paper (3×3×20 mm³)", 1.0), ("sensibilidad: P × 1.5 (cristal más grande)", 1.5)]:
        dep = depositos(escala_P=eP)
        (k, R), (klo, khi), nll = ajustar(h, dep, bordes, m)
        mu = histo_modelo(dep, k, R, bordes)
        mu = mu / mu[m].sum() * h[m].sum()
        chi2 = (((h[m] - mu[m]) ** 2) / np.maximum(mu[m], 1)).sum()
        res[nombre] = (k, klo, khi, R, mu, chi2, m.sum() - 2)
        n511 = 511 * k
        lce = n511 / (LY_511 * PDE * (1 + XT))
        print(f"B. {nombre}: k = {k:.2f} (+{khi - k:.2f}/-{k - klo:.2f}) aval/keV; "
              f"σ/E a 511 keV = {100 * R:.1f} % (FWHM {235.5 * R:.0f} %); χ² = {chi2:.0f} / {m.sum() - 2} gl; "
              f"511 keV -> {n511:.0f} avalanchas; LCE ≈ {100 * lce:.0f} %")

    # --- figura -------------------------------------------------------------------
    E.aplicar("tesis")
    fig, ax = E.figura("tesis", alto=0.45)
    ax.axvspan(0, N_MIN / 1e3, color=E.GRIS, alpha=0.12, lw=0, zorder=0, label="fuera del ajuste (umbral)")
    ax.hist(N / 1e3, bins=bordes / 1e3, color=E.C[0], label=f"Lu-176 medido ({len(N)} eventos)")
    for (nombre, (k, klo, khi, R, mu, chi2, gl)), kw in zip(res.items(), [E.MODELO, dict(color=E.TINTA, lw=1.0, ls="-.")]):
        ax.step(c / 1e3, mu, where="mid", **kw, label=f"modelo, {nombre}: k = {k:.2f} aval/keV")
    ax.axvline(c[imax] / 1e3, **E.LIMITE)
    ax.axvline(c[ihom] / 1e3, **E.LIMITE, label="rasgos usados en A")
    ax.set_xlabel(r"Avalanchas $N$ ($10^3$), con $Q_\mathrm{SPE}$ = 481 fC (provisoria)")
    ax.set_ylabel(f"Eventos por intervalo de {BIN}")
    ax.set_xlim(0, 3.2)
    ax.set_ylim(0, 1.6 * h.max())
    ax.legend(loc="upper right", fontsize=7)
    E.guardar(fig, "lu176_escala_preliminar", salida, script=__file__, datos=F.F216)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
