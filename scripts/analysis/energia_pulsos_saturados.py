#!/usr/bin/env python3
"""
ENERGÍA DE LOS PULSOS QUE SATURAN, POR TIEMPO SOBRE UMBRAL — energia_pulsos_saturados.py — v0.1 (2026-11-12)

Los pulsos cuadrados (216 Ω, trigger en 2.405 V, LOG 19 §3.9) saturan: no tienen amplitud ni área útiles.
Se usa su tiempo sobre 2.4 V (ToT) para estimar la sobreexcitación x = A_ideal / 2.4 V (A_ideal: amplitud que
tendría el pulso sin saturar), y de ahí la carga y la energía.

Calibración ToT -> x, con los eventos del Lu-176 que saturan con 2.2 kΩ a 2.5 V (energía conocida):
  - se ordenan por ToT y se les asigna, por rango, la energía del espectro del Lu-176 medido con 216 Ω
    (misma tasa por segundo arriba de cada energía), con la escala preliminar k = 2.0 aval/keV;
  - x = E·k·Q_SPE·(A/Q a 2.2 kΩ) / 2.4 V. A/Q a 2.2 kΩ sale de los eventos sin saturar a 0.25 V.
  - Se compara con el ToT de un pulso ideal sin recuperación del opamp (forma promedio, cola exponencial).
Se transfiere a 216 Ω suponiendo que el ToT depende solo de x (inferencia: el tiempo de recuperación ~ carga
en exceso × R_F / V_sat, y R_F/(A/Q) es parecido en las dos R_F: ~128 y ~138 ns).
Energía: N = x·2.4 V/(A/Q a 216 Ω)/Q_SPE avalanchas; se corrige la ocupación de celdas con 11 900 y 18 980 celdas.

Uso: python energia_pulsos_saturados.py <carpeta .trc> <carpeta de salida>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estilo_pi as E  # noqa: E402
import figuras_log19 as F  # noqa: E402

VTH, K, QSPE = 2400.0, 2.0, 0.481                 # mV, aval/keV, pC
CELDAS = (11862, 18980)
DEDX = 1.25 * 7.1                                 # MeV/cm, muón de ionización mínima en LYSO


def plantilla(f, rf, amin, amax):
    v, t, b, amp, q, tcf = F.eventos(f, rf)
    sel = (amp > amin) & (amp < amax) & np.isfinite(tcf)
    tr = np.arange(-150, 480, t[1] - t[0])
    w = np.nanmean([np.interp(tr + tcf[i], t, v[i] - b[i], left=np.nan, right=np.nan) / amp[i]
                    for i in np.nonzero(sel)[0]], 0)
    w = w / np.nanmax(w)
    tp = tr[np.nanargmax(w)]
    z = (tr > tp + 120) & (tr < tp + 280) & (w > 0.01)
    tau = -1 / np.polyfit(tr[z], np.log(w[z]), 1)[0]
    dt = tr[1] - tr[0]
    t2 = np.arange(tr[-1] + dt, tr[-1] + 3000, dt)
    return np.r_[w, w[-1] * np.exp(-(t2 - tr[-1]) / tau)], dt, np.median(amp[sel] / q[sel])


def tot(f, rf):
    v, t, b, *_ = F.eventos(f, rf)
    sobre = (v - b[:, None]) > VTH
    return sobre.sum(1) * (t[1] - t[0]), sobre[:, -5:].any(1)


def main(datos, salida):
    F.DATOS = datos
    xs = np.logspace(0, 2.5, 500)
    w22, dt22, aq22 = plantilla(F.F_025, 2200, 300, 1500)
    w216, dt216, aq216 = plantilla(F.F216[0], 216, 500, 1500)
    ideal = np.array([(w22 > 1 / x).sum() * dt22 for x in xs])              # misma R_F que la calibración

    # calibración empírica con el Lu-176 a 2.2 kΩ
    t22, trunc = tot(F.F_SAT, 2200)
    e_lu = np.sort(np.concatenate([F.eventos(f, 216)[4] for f in F.F216]) / QSPE / K)[::-1]   # keV
    escala = sum(1000 / r for r in (96.7, 102.5)) / (1000 / 99.6)                         # tiempos vivos
    cal = []
    for rango, i in enumerate(np.argsort(-t22), start=1):
        j = int(round(rango * escala)) - 1
        if j >= len(e_lu) or t22[i] <= 0:
            break
        if not trunc[i]:
            cal.append((t22[i], e_lu[j] * K * QSPE * aq22 / VTH))
    cal = np.array(cal)
    bordes = np.arange(40, 520, 20)
    cx, cy = [], []
    for a, b in zip(bordes[:-1], bordes[1:]):
        s = (cal[:, 0] >= a) & (cal[:, 0] < b)
        if s.sum() >= 5:
            cx.append(cal[s, 0].mean())
            cy.append(np.median(cal[s, 1]))
    cx, cy = np.array(cx), np.array(cy)

    # pulsos cuadrados a 216 Ω
    tm = np.concatenate([tot(f, 216)[0] for f in F.F_CUAD])
    tm = tm[tm > 0]
    x = np.interp(tm, cx, cy)
    n_med = x * VTH / aq216 / QSPE
    print(f"A/Q: 2.2 kΩ = {aq22:.2f} mV/pC, 216 Ω = {aq216:.3f} mV/pC; R_F/(A/Q) = {2200 / aq22:.0f} y {216 / aq216:.0f} ns")
    print(f"{len(tm)} pulsos; ToT {tm.min():.0f}–{tm.max():.0f} ns (mediana {np.median(tm):.0f}); "
          f"x = {x.min():.2f}–{x.max():.2f}; avalanchas (celdas disparadas) {np.percentile(n_med, [10, 50, 90]).round(-2)}")
    energias = {}
    for nc in CELDAS:
        e = -nc * np.log(1 - n_med / nc) / K / 1e3
        energias[nc] = e
        print(f"  {nc} celdas: E = {np.percentile(e, [10, 50, 90]).round(1)} MeV (p10/50/90); máx {e.max():.1f}")
    print(f"  sin corregir la ocupación: E = {np.percentile(n_med / K / 1e3, [10, 50, 90]).round(1)} MeV")
    print(f"  muón que cruza de costado (4.5–5 mm): {0.45 * DEDX:.1f}–{0.5 * DEDX:.1f} MeV; a lo largo (24 mm): {2.4 * DEDX:.0f} MeV")

    E.aplicar("tesis")
    fig, (a, b) = E.figura("tesis", alto=0.42, columnas=2)
    a.plot(cal[:, 0], cal[:, 1], "o", ms=2, color=E.C[0], alpha=0.35, label="Lu-176 con 2.2 kΩ (energía por rango)")
    a.plot(cx, cy, color=E.C[0], label="calibración (mediana)")
    a.plot(ideal, xs, **E.MODELO, label="pulso ideal (forma con 2.2 kΩ), sin recuperación")
    a.axvspan(tm.min(), tm.max(), color=E.C[2], **E.REGION, label="pulsos cuadrados (216 Ω)")
    a.set_xlabel("Tiempo sobre 2.4 V (ns)")
    a.set_ylabel(r"Sobreexcitación $A_\mathrm{ideal}$ / 2.4 V")
    a.set_yscale("log")
    a.set_xlim(0, 520)
    a.set_ylim(1, 100)
    a.legend(loc="upper left", fontsize=6.5)
    bins = np.arange(0, 8.01, 0.25)
    for nc, col in zip(CELDAS, (E.C[0], E.C[2])):
        b.hist(energias[nc], bins=bins, histtype="step", lw=1.3, color=col,
               label=f"corrigiendo ocupación con {nc:,} celdas".replace(",", " "))
    b.hist(n_med / K / 1e3, bins=bins, histtype="step", lw=1.0, color=E.TINTA, ls=":", label="sin corregir la ocupación")
    b.axvline(1.19, **E.LIMITE, label="máximo del Lu-176 (1.19 MeV)")
    b.axvspan(0.45 * DEDX, 0.5 * DEDX, color=E.C[3], **E.REGION, label="muón de costado (4.5–5 mm)")
    b.set_xlabel("Energía depositada estimada (MeV)")
    b.set_ylabel("Pulsos por intervalo de 0.25 MeV")
    b.set_xlim(0, 8)
    b.set_ylim(0, 27)
    b.legend(loc="upper right", fontsize=6.5)
    E.paneles([a, b])
    E.guardar(fig, "pulsos_saturados_energia", salida, script=__file__, datos=[F.F_SAT, F.F_025] + F.F_CUAD + F.F216)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
