#!/usr/bin/env python3
"""
FIGURAS DE TODAS LAS SERIES DE INTECNUS — series_intecnus.py — v0.2 (2026-11-20)

Recorre la carpeta dada y todas sus subcarpetas. En cada carpeta que tiene .trc crea una subcarpeta
"figuras" con:
  1. Por cada punto (un título de archivo, p. ej. med_41_pdGSO3W_A90_Y7p5_med):
       <título>_exploratorio   figura de 4 paneles de exploratorio_intecnus.py
       <título>_energia        espectro en energía de espectro_energia_f18.py (calibración de un punto,
                               fotopico = 511 keV); no se hace si el fotopico no se encuentra, ni para el
                               fondo o señales recortadas
  2. Las figuras de la serie, según qué cambia entre los puntos de esa carpeta (por cristal):
       serie_distancia_<cristal>      X = 0 (o sin X), ≥ 3 distancias Y: tasa contra d (log-log),
                                      con los ajustes A/d² y A/(d + d0)²
       serie_lateral_<cristal>_Y<d>   ≥ 3 posiciones X a la misma Y: tasa contra X
       serie_angular_<cristal>_Y<d>   ≥ 3 ángulos A a la misma Y: tasa contra A
       serie_puntos_<cristal>         los demás puntos (NEMA, posiciones sueltas), si son ≥ 2: tasa de cada uno
     El fondo no entra en las series; su tasa se informa en la consola.

Tasa de cada punto (igual que tasa_distancia_intecnus.py v0.6): tasa de disparo de cada archivo; media de los
archivos sin los que están a más de 5σ de Poisson de la mediana; dispersión = desviación estándar entre
archivos; barra de error = dispersión / √n. Sin corrección por decaimiento del F-18.
Los archivos incompletos se saltean con un aviso.

Título: C1 + med_<n>_pd<cristal>_<tokens>_med + contador. Tokens X<cm>, Y<cm>, A<grados> ('p' = punto decimal;
'neg', 'm' o '-' adelante = negativo). El resto (fondo, MEMA, NEMApos1...) es la etiqueta del punto.

Uso: python series_intecnus.py [carpeta raíz]
     Sin argumentos usa la carpeta del script. Necesita exploratorio_intecnus.py (v0.6 o posterior),
     reducir_intecnus.py (v0.2 o posterior), espectro_energia_f18.py (v0.3 o posterior) y estilo_pi.py en la
     misma carpeta que este script.
"""
import os
import sys
from collections import defaultdict

import numpy as np
from matplotlib.ticker import FixedLocator, FormatStrFormatter, NullFormatter, NullLocator
from scipy.optimize import curve_fit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estilo_pi as E  # noqa: E402
import exploratorio_intecnus as X  # noqa: E402
from espectro_energia_f18 import figura_energia  # noqa: E402
from reducir_intecnus import parsear  # noqa: E402


def num(v):
    return None if v == "" else v


def resumen(tasas):
    """Media sin los archivos a > 5σ de Poisson de la mediana, dispersión, n usados, n fuera, error de la media."""
    r, n = np.array(tasas).T
    dentro = np.abs(r - np.median(r)) <= 5 * r / np.sqrt(n - 1)
    rr = r[dentro]
    disp = rr.std(ddof=1) if len(rr) > 1 else np.nan
    return rr.mean(), disp, int(dentro.sum()), int((~dentro).sum()), disp / np.sqrt(len(rr))


def nombre_punto(p):
    partes = [f"{k} = {p[k]:g}" for k in ("X", "Y", "A") if p[k] is not None]
    return ", ".join(partes + ([p["etiqueta"]] if p["etiqueta"] else [])) or p["titulo"]


def procesar_carpeta(carpeta):
    trc = sorted(f for f in os.listdir(carpeta) if f.lower().endswith(".trc"))
    salida = os.path.join(carpeta, "figuras")
    grupos = defaultdict(list)
    for f in trc:
        grupos[X.titulo(f)].append(f)
    print(f"\n=== {carpeta}: {len(trc)} archivos, {len(grupos)} puntos")

    puntos = []
    for tit, archivos in grupos.items():
        persist = {"et": np.linspace(-500, 500, 501), "ev": np.arange(-100, 1000.5, 1.0)}
        persist["h"] = np.zeros((500, len(persist["ev"]) - 1))
        R, usados = [], []
        for f in archivos:
            try:
                R.append(X.analizar(os.path.join(carpeta, f), persist))
                usados.append(f)
            except X.ArchivoIncompleto as ex:
                print(f"  ⚠ se omite {f}: {ex}")
        if not R:
            continue
        X.figura(tit, R, persist, salida, usados)
        p = {k: num(v) for k, v in parsear(tit).items()}
        p["titulo"], p["etiqueta"] = tit, p["etiqueta"] or ""
        p["tasa"], p["disp"], p["n"], p["fuera"], p["err"] = resumen([(r["tasa"], r["nseg"]) for r in R])
        p["recorte"] = any(r["recorte"] for r in R)
        puntos.append(p)
        print(f"  {tit}: {p['tasa']:.1f} ± {p['err']:.1f} /s (dispersión {p['disp']:.1f}, {p['n']} archivos, "
              f"{p['fuera']} fuera)" + ("  ⚠ señal recortada" if p["recorte"] else ""))
        if not p["recorte"] and p["etiqueta"] != "fondo":
            fe = figura_energia(np.concatenate([r["q"][r["limpio"]] for r in R]), f"{tit}_energia", salida, usados)
            print(f"    fotopico {fe[0]:.2f} V·ns, FWHM {fe[1]:.1f} %" if fe else "    fotopico no encontrado: sin espectro en energía")

    for cristal in sorted({p["cristal"] for p in puntos}):
        pc = [p for p in puntos if p["cristal"] == cristal and p["etiqueta"] != "fondo"]
        usados = set()

        dist = [p for p in pc if p["Y"] is not None and p["A"] is None and not p["etiqueta"] and p["X"] in (None, 0)]
        if len({p["Y"] for p in dist}) >= 3:
            serie_distancia(cristal, dist, salida)
            usados |= {p["titulo"] for p in dist}

        lat = [p for p in pc if p["X"] is not None and p["A"] is None and not p["etiqueta"] and p["Y"] is not None]
        for y in sorted({p["Y"] for p in lat}):
            py = [p for p in lat if p["Y"] == y]
            if len({p["X"] for p in py}) >= 3:
                serie_xy(py, "X", f"serie_lateral_{cristal}_Y{y:g}", f"Posición lateral $X$ (cm), $Y$ = {y:g} cm",
                         cristal, salida)
                usados |= {p["titulo"] for p in py}

        ang = [p for p in pc if p["A"] is not None]
        for y in sorted({p["Y"] for p in ang if p["Y"] is not None}):
            py = [p for p in ang if p["Y"] == y]
            if len({p["A"] for p in py}) >= 3:
                serie_xy(py, "A", f"serie_angular_{cristal}_Y{y:g}", f"Ángulo $A$ (°), $Y$ = {y:g} cm", cristal, salida)
                usados |= {p["titulo"] for p in py}

        resto = [p for p in pc if p["titulo"] not in usados]
        if len(resto) >= 2:
            serie_puntos(cristal, resto, salida)


def serie_distancia(cristal, pts, salida):
    pts = sorted(pts, key=lambda p: p["Y"])
    D = np.array([p["Y"] for p in pts])
    R = np.array([p["tasa"] for p in pts])
    S = np.array([p["err"] for p in pts])
    inv2 = lambda d, a: a / d ** 2                       # noqa: E731
    mod = lambda d, a, d0: a / (d + d0) ** 2             # noqa: E731
    print(f"  Serie de distancia {cristal}:")
    p1 = p2 = None
    try:
        p1, c1 = curve_fit(inv2, D, R, p0=[R[0] * D[0] ** 2], sigma=S, absolute_sigma=True)
        print(f"    A/d²:        A = {p1[0]:.4g} ± {np.sqrt(c1[0, 0]):.2g} cm²/s; "
              f"χ² = {np.sum(((R - inv2(D, *p1)) / S) ** 2):.1f} con {len(D) - 1} g.l.")
        p2, c2 = curve_fit(mod, D, R, p0=[p1[0], 0.0], sigma=S, absolute_sigma=True, maxfev=20000)
        print(f"    A/(d + d0)²: A = {p2[0]:.4g} ± {np.sqrt(c2[0, 0]):.2g} cm²/s, d0 = {p2[1]:.2f} ± {np.sqrt(c2[1, 1]):.2f} cm; "
              f"χ² = {np.sum(((R - mod(D, *p2)) / S) ** 2):.1f} con {len(D) - 2} g.l.")
    except RuntimeError:
        print(f"    ⚠ el ajuste {'A/(d + d0)²' if p1 is not None else 'A/d²'} no convergió; se grafica sin él")

    E.aplicar("tesis")
    fig, ax = E.figura("tesis", alto=0.5)
    x = np.linspace(D.min() * 0.85, D.max() * 1.1, 200)
    ax.errorbar(D, R, S, fmt="o", color=E.C[0], label=f"medido ({cristal})", zorder=3)
    if p1 is not None:
        ax.plot(x, inv2(x, *p1), **E.MODELO, label=r"ajuste $A/d^2$")
    if p2 is not None:
        ax.plot(x, mod(x, *p2), color=E.C[2], lw=1.0, label=rf"ajuste $A/(d+d_0)^2$, $d_0$ = {p2[1]:.2f} cm")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.xaxis.set_major_locator(FixedLocator(D))
    ax.xaxis.set_major_formatter(FormatStrFormatter("%g"))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_xlabel("Distancia fuente–placa $d$ (cm)")
    ax.set_ylabel("Tasa de cuentas (1/s)")
    ax.legend(loc="lower left")
    E.guardar(fig, f"serie_distancia_{cristal}", salida, script=__file__, datos=[p["titulo"] for p in pts])


def serie_xy(pts, var, nombre, etiqueta_x, cristal, salida):
    pts = sorted(pts, key=lambda p: p[var])
    x = np.array([p[var] for p in pts])
    print(f"  Serie {nombre}: " + ", ".join(f"{var} = {p[var]:g}: {p['tasa']:.0f} ± {p['err']:.0f} /s" for p in pts))
    E.aplicar("tesis")
    fig, ax = E.figura("media_tesis", alto=0.8)
    ax.errorbar(x, [p["tasa"] for p in pts], [p["err"] for p in pts], fmt="o-", color=E.C[0], lw=0.8,
                label=f"medido ({cristal})")
    ax.set_xticks(x)
    ax.set_xlabel(etiqueta_x)
    ax.set_ylabel("Tasa de cuentas (1/s)")
    ax.set_ylim(0, None)
    ax.legend(loc="best")
    E.guardar(fig, nombre, salida, script=__file__, datos=[p["titulo"] for p in pts])


def serie_puntos(cristal, pts, salida):
    pts = sorted(pts, key=lambda p: (p["med"] or 0))
    print(f"  Puntos {cristal}: " + ", ".join(f"{nombre_punto(p)}: {p['tasa']:.0f} ± {p['err']:.0f} /s" for p in pts))
    E.aplicar("tesis")
    fig, ax = E.figura("media_tesis", alto=0.8)
    k = np.arange(len(pts))
    ax.errorbar(k, [p["tasa"] for p in pts], [p["err"] for p in pts], fmt="o", color=E.C[0], label=f"medido ({cristal})")
    ax.set_xticks(k, [nombre_punto(p) for p in pts], rotation=30, ha="right")
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_ylabel("Tasa de cuentas (1/s)")
    ax.set_ylim(0, None)
    ax.legend(loc="best")
    E.guardar(fig, f"serie_puntos_{cristal}", salida, script=__file__, datos=[p["titulo"] for p in pts])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    raiz = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    for carpeta, _, fs in sorted(os.walk(raiz)):
        if os.path.basename(carpeta) != "figuras" and any(f.lower().endswith(".trc") for f in fs):
            procesar_carpeta(carpeta)


if __name__ == "__main__":
    main()
