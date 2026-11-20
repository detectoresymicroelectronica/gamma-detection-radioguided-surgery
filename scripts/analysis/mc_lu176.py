#!/usr/bin/env python3
"""
MONTE CARLO DEL Lu-176 INTRÍNSECO EN UN PRISMA DE LYSO — mc_lu176.py — v0.2 (2026-11-12)

Energía depositada por decaimiento del Lu-176 (beta + cascada 307/202/88 keV) en un prisma de LYSO.
  - Decaimientos uniformes en el volumen; el beta deposita todo en el lugar.
  - Cada gamma (rendimientos del paper, Ko et al. 2021, tabla 3; el resto es conversión interna,
    depositada en el lugar) se sigue paso a paso: fotoeléctrico, Compton (Klein–Nishina, varias
    dispersiones), Rayleigh (cambia la dirección) o escape.
  - Tras un fotoeléctrico en la capa K del Lu se emite un rayo X K (fluorescencia) que se sigue igual.
  - Coeficientes de atenuación: xraydb (tablas de Elam, Ravel y Sieber), LYSO = Lu1.8Y0.2SiO5, 7.1 g/cm³.
Guarda por evento: energía depositada E y su primer momento a lo largo del eje Σ E_i·z_i (z = 0 en la cara
acoplada al SiPM), para probar una colección de luz que varía linealmente con z.

Uso: python mc_lu176.py <a_mm> <b_mm> <L_mm> <n_decaimientos> <salida.npz>
"""
import math
import random
import sys

import numpy as np
import xraydb

FORMULA, RHO = "Lu1.8Y0.2SiO5", 7.1
CASCADA = [(307.0, 0.931), (202.0, 0.780), (88.0, 0.146)]   # (keV, rendimiento gamma); 1 - Y = conversión interna
EK = 63.314                                                  # keV, borde K del Lu
ME = 511.0

# --- tablas de atenuación (1/mm) en una grilla fina -------------------------------------------------
GRID = np.arange(1.0, 350.0, 0.05)
MU = {k: np.array([xraydb.material_mu(FORMULA, e * 1e3, density=RHO, kind=k) for e in GRID]) / 10.0
      for k in ("photo", "incoh", "coh")}
MU["total"] = MU["photo"] + MU["incoh"] + MU["coh"]
# fracción del fotoeléctrico que ocurre en el Lu (masa del Lu en el compuesto × sección eficaz del Lu)
_masas = {"Lu": 1.8 * 174.967, "Y": 0.2 * 88.906, "Si": 28.086, "O": 5 * 15.999}
_wLu = _masas["Lu"] / sum(_masas.values())
F_LU = np.array([_wLu * xraydb.mu_elam("Lu", e * 1e3, kind="photo") for e in GRID]) / (MU["photo"] * 10 / RHO)
_edge = xraydb.xray_edge("Lu", "K")
P_K = (1 - 1 / _edge.jump_ratio) * _edge.fyield            # probabilidad de emitir un rayo X K tras absorción en K
_lin = xraydb.xray_lines("Lu", "K")
XE = np.array([v.energy / 1e3 for v in _lin.values()])
XW = np.array([v.intensity for v in _lin.values()])
XW = XW / XW.sum()


def tabla(nombre, E):
    i = int((E - 1.0) / 0.05)
    i = min(max(i, 0), len(GRID) - 2)
    return MU[nombre][i] if nombre != "flu" else F_LU[i]


def iso():
    c = 2 * random.random() - 1
    p = 2 * math.pi * random.random()
    s = math.sqrt(1 - c * c)
    return s * math.cos(p), s * math.sin(p), c


def rotar(u, ct, phi):
    ux, uy, uz = u
    st = math.sqrt(max(0.0, 1 - ct * ct))
    if abs(uz) > 0.99999:
        return st * math.cos(phi), st * math.sin(phi), ct * (1 if uz > 0 else -1)
    d = math.sqrt(1 - uz * uz)
    return (ux * ct + st * (ux * uz * math.cos(phi) - uy * math.sin(phi)) / d,
            uy * ct + st * (uy * uz * math.cos(phi) + ux * math.sin(phi)) / d,
            uz * ct - st * math.cos(phi) * d)


def klein_nishina(E):
    """Muestrea cos(θ) de Klein–Nishina por rechazo; devuelve (E', cosθ)."""
    k = E / ME
    while True:
        ct = 2 * random.random() - 1
        r = 1 / (1 + k * (1 - ct))
        f = r * r * (r + 1 / r - (1 - ct * ct))           # ∝ dσ/dΩ, máximo 2 en ct = 1
        if random.random() * 2 <= f:
            return E * r, ct


def salida(pos, u, dim):
    t = math.inf
    for i in range(3):
        if u[i] > 0:
            t = min(t, (dim[i] - pos[i]) / u[i])
        elif u[i] < 0:
            t = min(t, -pos[i] / u[i])
    return t


def seguir(E, pos, dim, dep):
    """Sigue un fotón; agrega a dep (lista de (energía, z)) lo que deposita."""
    u = iso()
    while E > 1.0:
        mt = tabla("total", E)
        d = -math.log(1 - random.random()) / mt
        if d >= salida(pos, u, dim):
            return
        pos = (pos[0] + d * u[0], pos[1] + d * u[1], pos[2] + d * u[2])
        r = random.random() * mt
        if r < tabla("photo", E):
            if E > EK and random.random() < tabla("flu", E) and random.random() < P_K:
                ex = float(np.random.choice(XE, p=XW))
                dep.append((E - ex, pos[2]))
                seguir(ex, pos, dim, dep)
            else:
                dep.append((E, pos[2]))
            return
        elif r < tabla("photo", E) + tabla("incoh", E):
            E2, ct = klein_nishina(E)
            dep.append((E - E2, pos[2]))
            E = E2
            u = rotar(u, ct, 2 * math.pi * random.random())
        else:                                              # Rayleigh: solo cambia la dirección (aprox. isotrópica)
            u = iso()
    return


def beta_muestras(n, seed):
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    import lu176_escala as L
    return L.beta(n, np.random.default_rng(seed))


def correr(a, b, L, n, seed=1, estadistica=False):
    random.seed(seed)
    np.random.seed(seed)
    dim = (a, b, L)
    eb = beta_muestras(n, seed)
    E = np.zeros(n)
    Mz = np.zeros(n)
    dE, dz, ev = [], [], []                                # cada depósito: energía, z, evento
    stats = {g: [0, 0, 0] for g, _ in CASCADA}             # emitidos como gamma, con interacción, con depósito total
    for i in range(n):
        p0 = (random.random() * a, random.random() * b, random.random() * L)
        dep = [(eb[i], p0[2])]
        for Eg, Y in CASCADA:
            if random.random() >= Y:                       # conversión interna: depósito local
                dep.append((Eg, p0[2]))
                continue
            sub = []
            seguir(Eg, p0, dim, sub)
            s = sum(x[0] for x in sub)
            stats[Eg][0] += 1
            stats[Eg][1] += s > 0
            stats[Eg][2] += s > Eg - 0.5
            dep += sub
        E[i] = sum(x[0] for x in dep)
        Mz[i] = sum(x[0] * x[1] for x in dep)
        for e_, z_ in dep:
            if e_ > 0:
                dE.append(e_); dz.append(z_); ev.append(i)
    if estadistica:
        for Eg, (ne, ni, nf) in stats.items():
            print(f"   {Eg:.0f} keV: P(interacción) = {ni / ne:.3f}; fracción con depósito total = {nf / max(ni, 1):.3f}")
    return E, Mz, np.array(dE), np.array(dz), np.array(ev, dtype=np.int32)


if __name__ == "__main__":
    a, b, L, n = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
    print(f"prisma {a} × {b} × {L} mm, {n} decaimientos; μ_total(511 keV) = "
          f"{xraydb.material_mu(FORMULA, 511e3, density=RHO, kind='total'):.3f} 1/cm")
    E, Mz, dE, dz, ev = correr(a, b, L, n, estadistica=True)
    np.savez(sys.argv[5], E=E, Mz=Mz, dE=dE, dz=dz, ev=ev, dim=np.array([a, b, L]))
