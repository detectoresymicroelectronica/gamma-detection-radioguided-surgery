#!/usr/bin/env python3
"""
REDUCCIÓN DE LAS MEDICIONES DE INTECNUS — reducir_intecnus.py — v0.1 (2026-11-20)

Lee UNA VEZ todos los .trc de la carpeta dada y sus subcarpetas, y guarda dos archivos chicos que usan
después todos los análisis (no hace falta volver a leer los .trc):

  reduccion_archivos.csv   una fila por .trc: carpeta, archivo, título, número de medición, cristal,
                           X, Y, A, etiqueta, hora, segmentos, tasa de disparo, recorte y rango de pantalla.
                           Se abre en Excel.
  reduccion_eventos.npz    una fila por evento (segmento): índice del archivo en el CSV, área (V·ns),
                           amplitud (mV), máximo absoluto de la traza (mV), tiempo de disparo (s desde el
                           primer segmento del archivo) y la clase: 0 limpio, 1 pulso antes, 2 segundo
                           pulso, 3 sin analizar.

Área, amplitud y clases salen de la misma función que exploratorio_intecnus.py (mismo método y cortes).

Título: C1 + med_<n>_pd<cristal>_<tokens>_med + contador, p. ej. C1med_41_pdGSO3W_A90_Y7p5_med00012.trc.
Tokens X<cm>, Y<cm>, A<grados> ('p' = punto decimal; 'm' o '-' adelante = negativo). Lo que no es número de
medición, cristal ni posición queda en "etiqueta" (fondo, MEMA, NEMApos1, ...).
Un archivo incompleto o que falla se saltea y queda anotado en reduccion_errores.txt.

Uso: python reducir_intecnus.py [carpeta] [carpeta de salida]
     Sin argumentos usa la carpeta del script para las dos cosas. Necesita exploratorio_intecnus.py (v0.6 o
     posterior) y estilo_pi.py en la misma carpeta que este script.
"""
import csv
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exploratorio_intecnus as X  # noqa: E402


def numero(txt):
    txt = txt.lower().replace("p", ".")
    return -float(txt[1:]) if txt[0] in "m-" else float(txt)


def parsear(titulo):
    """Campos del título (sin el C1 inicial ni el contador)."""
    campos = {"med": "", "cristal": "", "X": "", "Y": "", "A": "", "etiqueta": ""}
    toks = titulo.split("_")
    resto = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok == "med" and i + 1 < len(toks) and toks[i + 1].isdigit() and not campos["med"]:
            campos["med"] = int(toks[i + 1])
            i += 2
            continue
        m = re.fullmatch(r"([XYA])([m-]?\d+(?:p\d+)?)", tok)
        if tok.startswith("pd") and not campos["cristal"]:
            campos["cristal"] = tok[2:]
        elif m:
            campos[m.group(1)] = numero(m.group(2))
        elif tok and tok != "med":
            resto.append(tok)
        i += 1
    campos["etiqueta"] = "_".join(resto)
    return campos


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    carpeta = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    salida = sys.argv[2] if len(sys.argv) > 2 else carpeta
    os.makedirs(salida, exist_ok=True)

    rutas = sorted(os.path.join(r, f) for r, _, fs in os.walk(carpeta) for f in fs if f.lower().endswith(".trc"))
    print(f"{len(rutas)} archivos .trc en {carpeta}")
    filas, errores = [], []
    ev = {k: [] for k in ("archivo", "area_Vns", "amp_mV", "vmax_mV", "t_s", "clase")}
    t0 = time.time()
    for k, ruta in enumerate(rutas):
        nombre = os.path.basename(ruta)
        try:
            r = X.analizar(ruta)
        except Exception as ex:                          # archivo incompleto u otro problema: se anota y sigue
            errores.append(f"{os.path.relpath(ruta, carpeta)}: {ex}")
            continue
        tit = X.titulo(nombre)
        fila = {"indice": len(filas), "carpeta": os.path.relpath(os.path.dirname(ruta), carpeta),
                "archivo": nombre, "titulo": tit, **parsear(tit),
                "hora": r["hora"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "segmentos": r["nseg"],
                "tasa_1_s": round(r["tasa"], 3), "recorte": int(r["recorte"]),
                "pantalla_min_mV": round(float(r["pantalla"][0]), 2), "pantalla_max_mV": round(float(r["pantalla"][1]), 2)}
        filas.append(fila)
        clase = np.full(r["nseg"], 3, np.uint8)
        clase[r["ok"] & ~r["sin_pre"]] = 1
        clase[r["sin_pre"] & ~r["limpio"]] = 2
        clase[r["limpio"]] = 0
        ev["archivo"].append(np.full(r["nseg"], fila["indice"], np.uint32))
        ev["area_Vns"].append(r["q"].astype(np.float32))
        ev["amp_mV"].append(r["amp"].astype(np.float32))
        ev["vmax_mV"].append(r["vmax"].astype(np.float32))
        ev["t_s"].append((r["tt"] - r["tt"][0]).astype(np.float32))
        ev["clase"].append(clase)
        if (k + 1) % 50 == 0 or k + 1 == len(rutas):
            dt = time.time() - t0
            print(f"  {k + 1}/{len(rutas)} archivos, {dt / 60:.1f} min (faltan ~{dt / (k + 1) * (len(rutas) - k - 1) / 60:.0f} min)")

    with open(os.path.join(salida, "reduccion_archivos.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    np.savez_compressed(os.path.join(salida, "reduccion_eventos.npz"), **{k: np.concatenate(v) for k, v in ev.items()})
    with open(os.path.join(salida, "reduccion_errores.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(errores) + ("\n" if errores else ""))
    n = sum(len(a) for a in ev["clase"])
    print(f"Listo: {len(filas)} archivos, {n} eventos, {len(errores)} archivos salteados (ver reduccion_errores.txt)")
    print(f"Salida en {salida}: reduccion_archivos.csv, reduccion_eventos.npz")


if __name__ == "__main__":
    main()
