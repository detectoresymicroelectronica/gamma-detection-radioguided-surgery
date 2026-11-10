#!/usr/bin/env python3
"""
ESTILO GRÁFICO DEL PI — estilo_pi.py — v1.0 (2026-11-10)

Estilo fijo para TODOS los gráficos del Proyecto Integrador (LOG, tesis, paper, diapositivas).
La especificación completa y sus justificaciones están en GUIA_Estilo_Graficos.md.

Decisiones (10/11/2026):
  - Estilo IEEE: serif tipo Times (STIX, incluida en matplotlib), caja completa,
    marcas hacia adentro, grilla punteada.
  - Paleta "Neón lima" (5 colores, orden fijo): violeta, lima, magenta, cian, índigo.
  - Punto como separador decimal en todo.

Uso mínimo:
    import estilo_pi as E
    E.aplicar("ieee")                         # o "tesis", "slides"
    fig, ax = E.figura("columna")             # ancho de una columna IEEE
    ax.hist(q, bins=b, color=E.C[0], label="medido")
    ax.plot(x, y, **E.MODELO, label="modelo")
    E.guardar(fig, "19_fig1_espectro", "figuras", script=__file__, datos=["C1Vov2p5_RF216_LYSO00000.trc"])
"""
import os
from datetime import date

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

VERSION = "1.0"
CM = 1 / 2.54

# ---------------------------------------------------------------------------
# Paleta "Neón lima" — orden fijo: la serie k usa siempre C[k]
# Lima (2.0:1) y cian (2.8:1) tienen poco contraste sobre blanco: nunca como línea
# fina de referencia; como dato, con el grosor de línea del contexto o como relleno.
# ---------------------------------------------------------------------------
VIOLETA, LIMA, MAGENTA, CIAN, INDIGO = "#7B2CBF", "#8CC63F", "#E0218A", "#00A6C8", "#2B2D8C"
C = [VIOLETA, LIMA, MAGENTA, CIAN, INDIGO]

TINTA = "#000000"       # texto, ejes, modelos y límites
GRIS = "#7F7F7F"        # líneas de referencia secundarias (cero, base)
GRILLA = "#B0B0B0"

# Roles fijos (se pasan como **kwargs a plot / axvline / axhline)
MODELO = dict(color=TINTA, lw=1.0, ls=":")       # modelo, predicción, cálculo
LIMITE = dict(color=TINTA, lw=0.8, ls="--")      # límites, umbrales, valores de diseño
CERO = dict(color=GRIS, lw=0.6, ls="-")          # línea de cero / base
REGION = dict(alpha=0.12, lw=0, zorder=0)        # zonas sombreadas, detrás de los datos (+ color=C[k])

# Secuencial para parámetros ordenados (V_ov, T, R_F...) y mapas 2D:
# "viridis" (violeta oscuro → lima) recortada antes del amarillo pálido (ilegible sobre blanco).
SECUENCIAL = LinearSegmentedColormap.from_list("pi_secuencial", plt.get_cmap("viridis")(np.linspace(0.0, 0.9, 256)))
# Divergente (raro en el PI): índigo — blanco — magenta.
DIVERGENTE = LinearSegmentedColormap.from_list("pi_divergente", [INDIGO, "#FFFFFF", MAGENTA])


def secuencia(n):
    """n colores ordenados (oscuro = valor bajo, lima = valor alto) para series de un parámetro ordenado."""
    return [SECUENCIAL(x) for x in np.linspace(0, 1, n)] if n > 1 else [SECUENCIAL(0.0)]


# ---------------------------------------------------------------------------
# Tamaños (cm) — las figuras se dibujan al tamaño final: nunca se reescalan
# ---------------------------------------------------------------------------
ANCHOS_CM = {
    "columna": 8.89,       # IEEE, una columna (3.5 in)
    "doble": 18.19,        # IEEE, dos columnas (7.16 in)
    "tesis": 15.0,         # ancho de texto de la tesis — A CONFIRMAR con la plantilla
    "media_tesis": 7.3,    # dos figuras lado a lado en la tesis
    "slide": 24.0,         # diapositiva 16:9 (33.9 × 19.1 cm), con márgenes
}

CONTEXTOS = {              # tamaño base de letra (pt) y grosor de línea de datos
    "ieee": dict(base=8, lw=1.2),
    "tesis": dict(base=9, lw=1.3),
    "slides": dict(base=16, lw=2.2),
}


def aplicar(contexto="ieee"):
    """Fija los rcParams del estilo del PI para el contexto indicado."""
    k = CONTEXTOS[contexto]
    b = k["base"]
    plt.rcParams.update(matplotlib.rcParamsDefault)
    plt.rcParams.update({
        # tipografía (STIX viene con matplotlib: reproducible en cualquier máquina)
        "font.family": "serif", "font.serif": ["STIXGeneral"], "mathtext.fontset": "stix",
        "font.size": b, "axes.labelsize": b, "axes.titlesize": b, "legend.fontsize": b - 1,
        "xtick.labelsize": b - 1, "ytick.labelsize": b - 1, "figure.titlesize": b + 1,
        "text.color": TINTA, "axes.labelcolor": TINTA, "xtick.color": TINTA, "ytick.color": TINTA,
        # ejes: caja completa, marcas hacia adentro en los cuatro lados, menores visibles
        "axes.edgecolor": TINTA, "axes.linewidth": 0.6 * b / 8,
        "xtick.direction": "in", "ytick.direction": "in", "xtick.top": True, "ytick.right": True,
        "xtick.minor.visible": True, "ytick.minor.visible": True,
        "xtick.major.size": 3.5 * b / 8, "ytick.major.size": 3.5 * b / 8,
        "xtick.minor.size": 2.0 * b / 8, "ytick.minor.size": 2.0 * b / 8,
        "xtick.major.width": 0.6 * b / 8, "ytick.major.width": 0.6 * b / 8,
        "xtick.minor.width": 0.5 * b / 8, "ytick.minor.width": 0.5 * b / 8,
        # grilla punteada solo en marcas mayores, detrás de los datos
        "axes.grid": True, "axes.grid.which": "major", "axes.axisbelow": True,
        "grid.linestyle": ":", "grid.color": GRILLA, "grid.linewidth": 0.5 * b / 8,
        # números: punto decimal, sin offset raro, notación científica fuera de 1e-3..1e4
        "axes.formatter.useoffset": False, "axes.formatter.limits": (-3, 4),
        "axes.formatter.use_mathtext": True,
        # datos
        "axes.prop_cycle": matplotlib.cycler(color=C),
        "lines.linewidth": k["lw"], "lines.markersize": 3.5 * b / 8,
        "errorbar.capsize": 2 * b / 8, "hist.bins": "auto",
        "scatter.edgecolors": "none",
        # leyenda sin marco
        "legend.frameon": False, "legend.handlelength": 1.8, "legend.borderaxespad": 0.4,
        # figura y salida
        "figure.facecolor": "white", "savefig.facecolor": "white",
        "figure.constrained_layout.use": True,
        "savefig.dpi": 300, "pdf.fonttype": 42, "ps.fonttype": 42,
    })


def figura(ancho="columna", alto=0.62, filas=1, columnas=1, **kw):
    """Crea una figura al tamaño final. `ancho`: clave de ANCHOS_CM o cm; `alto`: fracción del ancho."""
    w = ANCHOS_CM.get(ancho, ancho) * CM
    return plt.subplots(filas, columnas, figsize=(w, alto * w), **kw)


def paneles(axes):
    """Rotula paneles (a), (b), ... arriba a la izquierda, por fuera del eje (no compite con la leyenda)."""
    for i, ax in enumerate(np.ravel(axes)):
        ax.set_title(f"({chr(97 + i)})", loc="left", pad=3)


def guardar(fig, nombre, carpeta=".", script=None, datos=None, formatos=("pdf", "png")):
    """Guarda PDF vectorial + PNG 300 dpi, con script y datos de origen en los metadatos.
    `nombre` sigue la convención <LOG>_fig<n>_<descripción>, sin extensión."""
    os.makedirs(carpeta, exist_ok=True)
    origen = (f"script: {os.path.basename(script) if script else '?'}; "
              f"datos: {', '.join(datos) if datos else '?'}; "
              f"estilo_pi v{VERSION}; {date.today().isoformat()}")
    for f in formatos:
        meta = {"Subject": origen} if f == "pdf" else {"Description": origen}
        fig.savefig(os.path.join(carpeta, f"{nombre}.{f}"), metadata=meta)
    plt.close(fig)
    print("  ", nombre)
