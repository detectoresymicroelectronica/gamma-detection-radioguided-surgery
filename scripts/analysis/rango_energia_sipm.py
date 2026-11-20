#!/usr/bin/env python3
"""RANGO DE ENERGÍA MEDIBLE — rango_energia_sipm.py — v1.0 (2026-11-12)
Rango de energía medible: ocupación de celdas del SiPM y límite lineal de la salida (216 Ω, 2.5 V nominal).
Extrapola desde el punto calibrado (290 keV -> 562 avalanchas). Uso: python rango_energia_sipm.py <carpeta de salida>  (LOG 20, figura 4)"""
import sys
import numpy as np
from scipy.optimize import brentq
sys.path.insert(0, "/home/claude/scripts")
import estilo_pi as E

N_CAL, E_CAL, LIM_LINEAL = 562.0, 290.0, 3070.0
CELDAS = {"11 900 celdas (bajo la cara del cristal)": 11862, "18 980 celdas (todo el SiPM)": 18980}
LINEAS = [(140, "Tc-99m"), (511, "511"), (662, "Cs-137"), (1275, "Na-22")]

e = np.linspace(1, 2200, 600)
lineal = N_CAL * e / E_CAL
E.aplicar("tesis")
fig, (a, b) = E.figura("tesis", alto=0.42, columnas=2)
curvas = {}
for i, (nom, nc) in enumerate(CELDAS.items()):
    f = lambda n: nc * (1 - np.exp(-n / nc))
    k0 = brentq(lambda k: f(k * E_CAL) - N_CAL, 1, 4)
    curvas[nom] = f(k0 * e)
    a.plot(e, curvas[nom] / 1e3, color=E.C[2 * i], label=nom)
    b.plot(e, 100 * (curvas[nom] / lineal - 1), color=E.C[2 * i], label=nom)
    elim = brentq(lambda x: f(k0 * x) - LIM_LINEAL, 100, 1e5)
    print(f"{nom}: k0 = {k0:.3f}; límite lineal -> {elim:.0f} keV")
a.plot(e, lineal / 1e3, **E.MODELO, label="extrapolación lineal desde 290 keV")
a.axhline(LIM_LINEAL / 1e3, **E.LIMITE, label="límite lineal de la salida (216 Ω)")
a.plot([E_CAL], [N_CAL / 1e3], "o", color=E.TINTA, label="punto calibrado (borde de 290 keV)")
for x, t in LINEAS:
    a.axvline(x, **E.CERO)
    a.text(x + 15, 3.85, t, fontsize=7, rotation=90, va="top")
    b.axvline(x, **E.CERO)
a.set_xlabel("Energía depositada (keV)")
a.set_ylabel(r"Avalanchas $N$ ($10^3$)")
a.set_xlim(0, 2200)
a.set_ylim(0, 4.0)
a.legend(loc="lower right", fontsize=6.5)
b.axhline(0, **E.CERO)
b.axhline(-10, **E.LIMITE)
b.set_xlabel("Energía depositada (keV)")
b.set_ylabel("Desvío respecto de lo lineal (%)")
b.set_xlim(0, 2200)
b.set_ylim(-15, 3)
b.legend(loc="lower left", fontsize=6.5)
E.paneles([a, b])
E.guardar(fig, "20_fig4_rango_energia", sys.argv[1], script=__file__,
          datos=["C1Vov2p5_RF216_LYSO00000.trc", "C1Vov2p5_RF216_LYSO_00000.trc"])
