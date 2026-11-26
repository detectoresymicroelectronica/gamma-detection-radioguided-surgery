#!/usr/bin/env python3
"""
ESPECTRO DEL F-18 EN ENERGÍA (GSO, INTECNUS) — espectro_energia_f18.py — v0.3 (2026-11-20)

Espectro de áreas de los eventos limpios de una medición (mismo análisis que exploratorio_intecnus.py),
con el eje en energía por una calibración de UN punto: el fotopico es 511 keV y la escala es lineal y pasa
por cero (E = 511 keV × área / área del fotopico). Se marcan la retrodispersión (0.17 MeV) y el
fotopico (0.511 MeV).

Uso: python espectro_energia_f18.py [carpeta .trc] [carpeta de salida]
     Necesita exploratorio_intecnus.py y estilo_pi.py en la misma carpeta que este script.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estilo_pi as E  # noqa: E402
import exploratorio_intecnus as X  # noqa: E402

ANCHO_KEV = 10.0
REFERENCIA = {170: "retrodispersión", 511: "fotopico"}   # keV


def figura_energia(q, nombre, salida, datos):
    """Espectro en energía de las áreas q (V·ns, eventos limpios). Devuelve (fotopico V·ns, FWHM %) o None."""
    fp = X.fotopico(q)
    if fp is None:
        return None
    _, mu, s, _ = fp
    en = 511.0 * q / mu
    E.aplicar("tesis")
    fig, ax = E.figura("tesis", alto=0.5)
    e = np.arange(0, 1500 + ANCHO_KEV, ANCHO_KEV)
    h, _ = np.histogram(en, bins=e)
    ax.stairs(h, e / 1e3, color=E.C[0], lw=1.2, label="GSO, EVB-TIA v0")
    for ek, txt in REFERENCIA.items():
        ax.axvline(ek / 1e3, **E.LIMITE)
        ax.text(ek / 1e3, h.max() * 1.05, f" ref.: {txt}\n {ek / 1e3:g} MeV", fontsize=7, va="bottom")
    ax.set_xlim(0, 1.5)
    ax.set_ylim(0, h.max() * 1.3)
    ax.set_xlabel("Energía (MeV), calibración de un punto: fotopico = 511 keV")
    ax.set_ylabel(f"Cuentas por {ANCHO_KEV:g} keV")
    ax.legend(loc="center", bbox_to_anchor=(0.69, 0.45))
    E.guardar(fig, nombre, salida, script=__file__, datos=datos)
    return mu, 235.5 * s / mu


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    carpeta = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    salida = sys.argv[2] if len(sys.argv) > 2 else carpeta
    archivos = sorted(f for f in os.listdir(carpeta) if f.lower().endswith(".trc"))
    R = []
    for f in archivos:
        try:
            R.append(X.analizar(os.path.join(carpeta, f)))
        except X.ArchivoIncompleto as ex:
            print(f"⚠ se omite {f}: {ex}")
    q = np.concatenate([r["q"][r["limpio"]] for r in R])
    res = figura_energia(q, "espectro_energia_f18", salida, archivos[:1] + ["…"])
    if res is None:
        print("fotopico no encontrado")
    else:
        print(f"{len(q)} eventos limpios; fotopico = {res[0]:.2f} V·ns ≡ 511 keV; FWHM {res[1]:.1f} %")


if __name__ == "__main__":
    main()
