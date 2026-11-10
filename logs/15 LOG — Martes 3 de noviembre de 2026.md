# 15 LOG — Martes 3 de noviembre de 2026

## Armado de la placa del SiPM para BETA: pasivos soldados y verificados

**Tipo:** armado de PCB (soldadura) y verificación eléctrica con multímetro. Sin SiPM.
**Continúa:** LOG 14 (diseño de la placa, fresada en la CNC).

**Estado al cierre:** R1–R3 y C1–C4 soldados y verificados. Faltan J1, J2 (pines), J3 (SMA) y U1 (SiPM).

---

## 1. Método

- **Estación:** Yihua 852 (aire caliente + soldador).
- **Pasivos:** pasta y aire caliente.
- **Datos a completar:** aleación de la pasta, ajuste de HEATER y AIR, distancia de la boquilla y encapsulado de los componentes usados.

## 2. Componentes montados

Los valores se eligieron según lo disponible en el laboratorio.

| Ref. | Diseño (LOG 14) | Montado | Criterio |
|---|---|---|---|
| R1, R2 | 49,9 Ω | **51 Ω** (código `510`) | Son el filtro del bias, no adaptan impedancia: sirve cualquier valor entre ~22 y 100 Ω. La caída en la I-V es despreciable (µV cerca de V_br) |
| R3 | 10 Ω | **8,2 Ω** | No había de 10 Ω que entrara en los pads. Rango aceptable 8,2–12 Ω: con el osciloscopio a 50 Ω, τ ≈ 24 ns en lugar de ~28 ns (−15 %). La I-V no depende de R3 |
| C1, C3 | 10 nF, 50 V | 10 nF | — |
| C2 | 100 nF, 50 V | 100 nF | — |
| C4 | 10 pF, 50 V | **12 pF** | No había de 10 pF. En paralelo con 110 nF su efecto es despreciable |

> [!IMPORTANT]
> La placa de control (sin SiPM) tiene que llevar **los mismos valores y, si se puede, componentes de las mismas bolsas**. Si no, deja de representar la fuga de esta placa.

## 3. Verificación con multímetro

| Entre | Medido | Esperado | Estado |
|---|---|---|---|
| J1 – pin 3 (cátodo) | 101,5 Ω | R1 + R2 = 102 Ω | ✓ |
| Pin 1 (ánodo) – GND | 8,2 Ω | R3 | ✓ |
| GND – extremos de los capacitores y pin 4 | 0 Ω | Plano continuo | ✓ |
| J1 – GND | ~27 MΩ y luego OL | Carga de los capacitores, sin corto | ✓ |
| Pin 1 – pin 3 | OL | Sin puente entre pads del SiPM | ✓ |
| Capacidad del bias (cátodo – GND y J1 – GND) | **113 nF** (final) | ~120 nF (C1 + C2 + C3 + C4) | ✓ |

**Nota sobre la capacidad:** desde el cátodo también se ve C1 a través de R2 (51 Ω no lo aísla a la frecuencia de medición del multímetro). Por eso las dos mediciones dan lo mismo.

---

## Problemas identificados

| Problema | Causa | Solución |
|---|---|---|
| Capacidad del bias de **202 nF** (esperado ~120 nF) | Un capacitor de 100 nF colocado en lugar de uno de 10 nF (en C1 o C3). **Inferencia:** el total coincide con 100 + 100 + 10 nF ≈ 210 nF, y al reemplazar uno el valor volvió a lo esperado | Reemplacé uno por uno de 10 nF → **113 nF** |
| Una lectura intermedia de 101 nF | Muy probablemente un mal contacto de las puntas al medir: al repetir dio 113 nF | Repetir siempre una medición dudosa antes de sacar conclusiones |
| Una resistencia demasiado chica para los pads | Los pads 1206 HandSolder dejan ~1,8 mm entre sí: una 0603 (1,6 mm) no los alcanza; una 0805 (2,0 mm) entra justa | Usé otra resistencia |

**Lección:** medir la capacidad total del bias detecta errores de colocación de capacitores que no se ven a simple vista (los MLCC no tienen marca).

---

## Decisiones técnicas de la sesión

| Decisión | Justificación | Impacto futuro |
|---|---|---|
| R1 = R2 = 51 Ω | Filtro del bias; el valor exacto no importa | Igual en la placa de control |
| R3 = 8,2 Ω | Dentro de 8,2–12 Ω; cambio de τ de −15 % | Tenerlo en cuenta al comparar los pulsos de Sout con la GEVB / AND9782 |
| C4 = 12 pF | Sin 10 pF disponibles; efecto despreciable | Igual en la placa de control |

---

## Pendientes

- [ ] Limpiar el flux (alcohol isopropílico al 20 % en esta placa, porque va a llevar el SiPM) y dejar secar
- [ ] J1 y J2: pin macho recto de 2,54 mm acostado, sin el separador de plástico, apuntando al borde; soldador a ~350 °C (más en J2, unido al plano); gota de epoxi para que el dupont no levante el pad
- [ ] Marcar con fibrón "+" en J1 y "−" en J2
- [ ] Verificar después de los pines: J1 – pin 3 ≈ 101,5 Ω; J2 – GND ≈ 0 Ω; J1 – J2 abierto
- [ ] J3 (SMA): definir cómo se suelda el pasante en simple faz
- [ ] Practicar la soldadura con aire caliente en la placa de control antes del SiPM; si hay termopar, medir la temperatura real en la placa para el ajuste y la distancia elegidos (límite AND9788: 260 °C durante ≤ 10 s)
- [ ] Soldar U1 (SiPM del lote #01–#10), con curva I-V antes y después
- [ ] Fresar y armar la placa de control con los mismos valores

---

*Log generado al finalizar la sesión del 3 de noviembre de 2026.*
