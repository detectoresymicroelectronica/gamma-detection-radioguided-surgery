#!/usr/bin/env python3
"""
=============================================================================
  BIAS DC POR DEBAJO DE V_br PARA MEDICIÓN DE RUIDO — EVB-TIA v0
=============================================================================
  Proyecto: Gamma Tracker — Caracterización de la EVB-TIA v0
  Fecha de creación: 2026-11-05
  Basado en: bias_sipm_needles.py (2026-08-25)

  Descripción:
    Aplica un bias DC FIJO POR DEBAJO DE V_br (por defecto 20 V) al SiPM de
    la EVB-TIA a través del Keithley 2450, para medir el ruido del TIA con el
    SiPM polarizado pero SIN avalanchas (bloque A / M4 de la sesión 05/11).

    Sirve además como control: a esta tensión no tiene que haber pulsos ni
    corriente apreciable. Si los hay, entra luz o algo está mal.

    El script:
      1. Checklist de conexiones de la EVB
      2. Pide V_bias (default 20 V) y BLOQUEA cualquier valor cercano a V_br
      3. Configura y verifica el instrumento por readback
      4. Chequeo de oscuridad a 0 V (fotocorriente)
      5. Rampa gradual hasta V_bias
      6. Monitorea la corriente y la GUARDA EN CSV (trazabilidad)
      7. Apagado seguro ante cualquier error o Ctrl+C

  Configuración de bias: Configuración C — EVB-TIA v0
    ┌───────────────────────────────────────────────────────────┐
    │  Keithley 2450              EVB-TIA                       │
    │  FORCE HI (+) ──→ J1 (+) ──→ R1–R2–R3 ──→ CÁTODO   +Vbias  │
    │  FORCE LO (−) ──→ J1 GND ──────────────→ ÁNODO     GND     │
    └───────────────────────────────────────────────────────────┘
    El Keithley sourcea voltaje POSITIVO: V_source = +V_bias.

  Dispositivo: onsemi MICROFC-60035-SMT soldado en la EVB-TIA v0
  V_br: NO MEDIDO para este SiPM. Datasheet: 24.2–24.7 V a 21 °C.

  Instrumento: Keithley 2450 SMU vía USB (USBTMC)

  Uso:
    python bias_ruido_evb.py

  ADVERTENCIA:
    El bias debe aplicarse con los +5 V de la EVB YA ENCENDIDOS.
=============================================================================
"""

import pyvisa
import time
import sys
import signal
import csv
from datetime import datetime

# =============================================================================
#  PARÁMETROS DEL DISPOSITIVO
# =============================================================================

DEVICE_ID = "EVB-02"           # Placa medida
V_BR_MIN_DATASHEET = 24.2      # V_br mínima del datasheet a 21 °C [V]
I_MAX_ABSOLUTE = 20e-3         # Corriente máxima absoluta del SiPM [A]

# =============================================================================
#  PARÁMETROS DE LA MEDICIÓN Y PROTECCIÓN
# =============================================================================

V_BIAS_DEFAULT = 20.0          # Bias por defecto [V]
MARGEN_BAJO_VBR = 2.0          # Margen mínimo por debajo de V_br [V]
V_BIAS_MAX_HARD = V_BR_MIN_DATASHEET - MARGEN_BAJO_VBR   # = 22.2 V (bloqueo)

I_COMPLIANCE = 100e-6          # Compliance [A] (100 µA, igual que needles)
I_WARN_0V = 1e-6               # > 1 µA a 0 V → fotocorriente (luz)
I_WARN_BIAS = 1e-6             # > 1 µA por debajo de V_br → luz o avalancha

NPLC = 1.0                     # Integración ~20 ms (50 Hz)

V_RAMP_STEP = 1.0              # Paso de la rampa [V]
V_RAMP_DELAY = 0.3             # Delay entre pasos [s]
SETTLING_TIME = 2.0            # Estabilización post-rampa [s]
MONITOR_INTERVAL = 2.0         # Intervalo de monitoreo y registro [s]

# =============================================================================
#  VARIABLES GLOBALES PARA SHUTDOWN SEGURO
# =============================================================================

_inst_global = None
_v_current_global = 0.0
_csv_file_global = None


# =============================================================================
#  FUNCIONES AUXILIARES
# =============================================================================

def find_keithley(rm):
    """Busca el Keithley 2450 entre los recursos USB. Retorna el resource o None."""
    resources = rm.list_resources()
    if not resources:
        return None

    print("  Recursos VISA encontrados:")
    for res in resources:
        if "USB" not in res.upper():
            continue
        try:
            inst = rm.open_resource(res)
            inst.timeout = 5000
            inst.write_termination = '\n'
            inst.read_termination = '\n'
            idn = inst.query("*IDN?").strip()
            print(f"    {res}")
            print(f"    → {idn}")
            inst.close()
            if "2450" in idn:
                return res
        except Exception as e:
            print(f"    {res} → Error: {e}")
    return None


def setup_instrument(inst, i_compliance, nplc):
    """Fuente de voltaje positiva, medición de corriente, 2 hilos, frontales."""
    inst.write("*RST")
    inst.write("*CLS")
    time.sleep(1)

    inst.write(":SOUR:FUNC VOLT")
    inst.write(":SOUR:VOLT:RANG 200")      # rango 200 V (el de 20 V no alcanza con margen)
    inst.write(":SOUR:VOLT:RANG:AUTO OFF")
    inst.write(":SOUR:VOLT 0")

    inst.write(":SENS:FUNC 'CURR'")
    inst.write(":SENS:CURR:RANG:AUTO ON")
    inst.write(f":SOUR:VOLT:ILIM {i_compliance}")
    inst.write(f":SENS:CURR:NPLC {nplc}")

    inst.write(":ROUT:TERM FRON")
    inst.write(":SENS:CURR:RSEN OFF")
    inst.write(":OUTP OFF")


def verify_instrument_config(inst, i_compliance, nplc):
    """Readback de la configuración. Retorna True si no hay errores."""
    errors = []

    def check(cond, ok_msg, err_msg):
        if cond:
            print(f"    ✓ {ok_msg}")
        else:
            errors.append(err_msg)

    sour = inst.query(":SOUR:FUNC?").strip()
    check("VOLT" in sour.upper(), f"Fuente: {sour}", f"Fuente esperada VOLT, leída {sour}")

    sens = inst.query(":SENS:FUNC?").strip()
    check("CURR" in sens.upper(), f"Medición: {sens}", f"Medición esperada CURR, leída {sens}")

    v_range = float(inst.query(":SOUR:VOLT:RANG?").strip())
    check(v_range >= 200, f"Rango voltaje: {v_range:.0f} V", f"Rango esperado 200 V, leído {v_range}")

    ilim = float(inst.query(":SOUR:VOLT:ILIM?").strip())
    check(abs(ilim - i_compliance) / i_compliance < 0.05,
          f"Compliance: {ilim*1e6:.1f} µA",
          f"Compliance esperado {i_compliance*1e6:.1f} µA, leído {ilim*1e6:.1f} µA")

    nplc_read = float(inst.query(":SENS:CURR:NPLC?").strip())
    if abs(nplc_read - nplc) < 0.01:
        print(f"    ✓ NPLC: {nplc_read}")
    else:
        print(f"    ⚠ NPLC esperado {nplc}, leído {nplc_read}")

    term = inst.query(":ROUT:TERM?").strip()
    check("FRON" in term.upper(), f"Terminales: {term}", f"Terminales esperadas FRON, leídas {term}")

    outp = inst.query(":OUTP?").strip()
    check(outp == "0" or "OFF" in outp.upper(), "Salida: OFF", f"Salida debería estar OFF, leída {outp}")

    v_set = float(inst.query(":SOUR:VOLT?").strip())
    check(abs(v_set) < 0.01, f"Voltaje seteado: {v_set:.6f} V", f"Voltaje debería ser 0 V, leído {v_set}")

    for e in errors:
        print(f"    ✖ {e}")
    return len(errors) == 0


def ramp_voltage(inst, v_start, v_target, step_size, delay):
    """
    Rampa gradual. Aborta si se alcanza el compliance.
    Retorna (éxito, voltaje_final).
    """
    global _v_current_global

    delta = v_target - v_start
    if abs(delta) < 0.001:
        return True, v_start

    direction = 1 if delta > 0 else -1
    n_steps = max(1, int(abs(delta) / step_size))
    voltages = []
    for i in range(1, n_steps + 1):
        v = v_start + direction * step_size * i
        v = min(v, v_target) if direction > 0 else max(v, v_target)
        voltages.append(round(v, 6))
    if abs(voltages[-1] - v_target) > 0.001:
        voltages.append(v_target)

    for idx, v in enumerate(voltages):
        inst.write(f":SOUR:VOLT {v:.6f}")
        _v_current_global = v
        time.sleep(delay)

        abs_current = abs(float(inst.query(":READ?").strip()))
        pct = (idx + 1) / len(voltages) * 100
        print(f"\r  Rampa [{pct:5.1f}%]: V = {v:+8.3f} V | "
              f"|I| = {abs_current*1e9:10.2f} nA", end="", flush=True)

        if abs_current >= I_COMPLIANCE * 0.95:
            print(f"\n\n  ✖ COMPLIANCE ALCANZADO durante la rampa "
                  f"(V = {v:+.3f} V, I = {abs_current*1e6:.2f} µA)")
            print("    Por debajo de V_br esto NO es normal: polaridad invertida,")
            print("    cortocircuito o SiPM dañado. ABORTANDO.")
            return False, v

    print()
    return True, v_target


def safe_shutdown(inst, current_voltage):
    """Rampa a 0 V y salida OFF. Se llama siempre al salir."""
    global _v_current_global

    print("\n  ┌─────────────────────────────────────┐")
    print("  │  APAGADO SEGURO                     │")
    print("  └─────────────────────────────────────┘")
    try:
        if abs(current_voltage) > 0.5:
            print(f"  Rampa: {current_voltage:+.3f} V → 0 V...")
            ramp_voltage(inst, current_voltage, 0.0, V_RAMP_STEP, 0.1)
        inst.write(":SOUR:VOLT 0")
        time.sleep(0.2)
        _v_current_global = 0.0
        inst.write(":OUTP OFF")
        time.sleep(0.2)
        outp = inst.query(":OUTP?").strip()
        if outp == "0" or "OFF" in outp.upper():
            print("  ✓ Salida APAGADA")
        else:
            print(f"  ⚠ Estado de salida: {outp} — verificar manualmente")
        print(f"  ✓ Voltaje: {float(inst.query(':SOUR:VOLT?').strip()):.6f} V")
    except Exception as e:
        print(f"  ⚠ Error durante apagado seguro: {e}")
        try:
            inst.write(":OUTP OFF")
            print("  ⚠ Salida forzada OFF (sin rampa)")
        except Exception:
            print("  ✖ NO SE PUDO APAGAR LA SALIDA — APAGAR MANUALMENTE EL KEITHLEY")


def close_csv():
    global _csv_file_global
    if _csv_file_global is not None:
        try:
            _csv_file_global.close()
        except Exception:
            pass
        _csv_file_global = None


def signal_handler(signum, frame):
    """Ctrl+C: apagado seguro y salida."""
    print("\n\n  *** Ctrl+C detectado ***")
    if _inst_global is not None:
        safe_shutdown(_inst_global, _v_current_global)
    close_csv()
    sys.exit(0)


# =============================================================================
#  FUNCIÓN PRINCIPAL
# =============================================================================

def main():
    global _inst_global, _v_current_global, _csv_file_global

    signal.signal(signal.SIGINT, signal_handler)

    print()
    print("=" * 70)
    print("  BIAS POR DEBAJO DE V_br — MEDICIÓN DE RUIDO DEL TIA (M4)")
    print(f"  Placa: {DEVICE_ID} — Configuración C: cátodo = +Vbias, ánodo = GND")
    print(f"  Bloqueo: V_bias ≤ {V_BIAS_MAX_HARD:.1f} V "
          f"(V_br mín. datasheet {V_BR_MIN_DATASHEET} V − {MARGEN_BAJO_VBR} V)")
    print("=" * 70)

    # ── PASO 1: conexiones ──
    print("\n" + "─" * 70)
    print("  PASO 1: VERIFICACIÓN DE CONEXIONES")
    print("─" * 70)
    print("""
  CHECKLIST — verificar cada punto antes de continuar:

    [ ] 1. Keithley FORCE HI (rojo)  → J1 (+)   → cátodo
    [ ] 2. Keithley FORCE LO (negro) → J1 GND   → ánodo
    [ ] 3. Keithley TERMINALS en FRONT, SENSE sin conectar
    [ ] 4. Rigol canal +6V: 5,00 V / 0,030 A, ENCENDIDA, consumo ~6 mA
    [ ] 5. J3 (SMA) → LeCroy CH1 (DC1M)
    [ ] 6. Placa TAPADA (caja + caja + telas blackout)
""")
    try:
        input("  Presionar ENTER cuando TODO esté verificado...")
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelado.")
        return

    # ── PASO 2: V_bias ──
    print("\n" + "─" * 70)
    print("  PASO 2: TENSIÓN DE BIAS (por debajo de V_br)")
    print("─" * 70)
    while True:
        try:
            s = input(f"\n  V_bias [V] (ENTER = {V_BIAS_DEFAULT:.1f} V, "
                      f"máx. {V_BIAS_MAX_HARD:.1f} V): ").strip()
            v_bias = V_BIAS_DEFAULT if s == "" else float(s)
        except ValueError:
            print("  ⚠ Valor inválido. Ingresar un número (ej: 20)")
            continue
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelado.")
            return

        if v_bias <= 0:
            print("  ✖ V_bias debe ser positivo (Configuración C).")
            continue
        if v_bias > V_BIAS_MAX_HARD:
            print(f"  ✖ V_bias = {v_bias:.2f} V está demasiado cerca de V_br. "
                  f"Máximo: {V_BIAS_MAX_HARD:.1f} V.")
            continue
        break

    v_source = +v_bias

    # ── PASO 3: confirmación ──
    print(f"""
  ╔═════════════════════════════════════════════════╗
  ║  Se va a aplicar al SiPM de la {DEVICE_ID}:          ║
  ║    V_source = {v_source:>+10.3f} V  (por debajo de V_br)   ║
  ║    I_limit  = {I_COMPLIANCE*1e6:>10.1f} µA                     ║
  ║  Rampa: {V_RAMP_STEP:.0f} V/paso, {V_RAMP_DELAY:.1f} s/paso                    ║
  ║  Ctrl+C en cualquier momento = apagado seguro   ║
  ╚═════════════════════════════════════════════════╝
""")
    try:
        if input("  Escribir 'SI' (mayúsculas) para aplicar el bias: ").strip() != "SI":
            print("  Cancelado por el usuario.")
            return
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelado.")
        return

    # ── PASO 4: instrumento ──
    print("\n" + "─" * 70)
    print("  PASO 4: CONEXIÓN AL INSTRUMENTO")
    print("─" * 70)
    rm = pyvisa.ResourceManager('@py')
    resource = find_keithley(rm)
    if resource is None:
        print("\n  ✖ No se encontró el Keithley 2450 (USB, encendido, driver Zadig).")
        rm.close()
        return

    inst = rm.open_resource(resource)
    inst.timeout = 10000
    inst.write_termination = '\n'
    inst.read_termination = '\n'
    _inst_global = inst

    idn = inst.query("*IDN?").strip()
    print(f"  Instrumento: {idn}")
    if "2450" not in idn:
        print("  ✖ El instrumento no parece ser un Keithley 2450.")
        inst.close(); rm.close(); _inst_global = None
        return

    setup_instrument(inst, I_COMPLIANCE, NPLC)
    print("\n  Verificando configuración (readback):")
    if not verify_instrument_config(inst, I_COMPLIANCE, NPLC):
        print("\n  ✖ La configuración no coincide. Abortando.")
        inst.close(); rm.close(); _inst_global = None
        return
    print("\n  ✓ Instrumento configurado y verificado.")

    # ── PASO 5: chequeo de oscuridad a 0 V ──
    print("\n" + "─" * 70)
    print("  PASO 5: CHEQUEO DE OSCURIDAD A 0 V")
    print("─" * 70)
    inst.write(":OUTP ON")
    time.sleep(0.5)
    outp = inst.query(":OUTP?").strip()
    if not (outp == "1" or "ON" in outp.upper()):
        print(f"  ✖ No se pudo encender la salida. Estado: {outp}")
        inst.close(); rm.close(); _inst_global = None
        return

    time.sleep(1.0)
    i_0v = abs(float(inst.query(":READ?").strip()))
    print(f"  Corriente a 0 V: {i_0v*1e9:.2f} nA")
    if i_0v > I_WARN_0V:
        print(f"  ⚠ {i_0v*1e6:.2f} µA a 0 V: en la EVB el único camino DC es el SiPM,")
        print("     así que esto es FOTOCORRIENTE → entra luz. Mejorar la oscuridad.")
        try:
            resp = input("  ¿Continuar de todas formas? (s/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            resp = 'n'
        if resp != 's':
            safe_shutdown(inst, 0.0)
            inst.close(); rm.close(); _inst_global = None
            return
    else:
        print("  ✓ Oscuridad OK")

    # ── PASO 6: rampa ──
    print("\n" + "─" * 70)
    print("  PASO 6: APLICANDO BIAS")
    print("─" * 70)
    print(f"\n  Rampa: 0 V → {v_source:+.3f} V...")
    success, v_final = ramp_voltage(inst, 0.0, v_source, V_RAMP_STEP, V_RAMP_DELAY)
    if not success:
        safe_shutdown(inst, v_final)
        inst.close(); rm.close(); _inst_global = None
        return

    print(f"\n  Estabilizando ({SETTLING_TIME:.0f} s)...", end="", flush=True)
    time.sleep(SETTLING_TIME)
    print(" OK")

    i_stable = abs(float(inst.query(":READ?").strip()))
    print(f"\n  |I| a {v_source:.3f} V: {i_stable*1e9:.2f} nA")
    if i_stable > I_WARN_BIAS:
        print(f"  ⚠ Corriente alta por debajo de V_br ({i_stable*1e6:.2f} µA):")
        print("     luz, o V_br de este SiPM más baja de lo esperado. Revisar antes de medir.")
    else:
        print("  ✓ Corriente esperada (nA): sin avalancha")

    # ── PASO 7: monitoreo + CSV ──
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_name = f"{ts}_{DEVICE_ID}_M4_ruido_V{v_bias:.0f}_corriente.csv"
    _csv_file_global = open(csv_name, "w", newline="")
    writer = csv.writer(_csv_file_global)
    _csv_file_global.write(f"# {DEVICE_ID} | M4 ruido | V_source = {v_source:+.3f} V | "
                           f"compliance = {I_COMPLIANCE*1e6:.0f} uA | NPLC = {NPLC} | "
                           f"I(0 V) = {i_0v:.4e} A\n")
    writer.writerow(["timestamp", "t_s", "V_source_V", "I_A"])

    print("\n" + "─" * 70)
    print("  PASO 7: BIAS APLICADO — MEDIR RUIDO EN EL LeCroy")
    print("─" * 70)
    print("""
  En el LeCroy (bloque A del plan):
    1. Verificar que NO hay pulsos.
    2. Trigger AUTO, 20 µs/div, BW 200 MHz, Single.
    3. Guardar 3 trazas C1 (.trc) y anotar sdev(C1).
    4. Al terminar: Ctrl+C aquí (rampa a 0 V y salida OFF).
""")
    print(f"  Registrando corriente en: {csv_name}\n")
    print(f"  {'Tiempo':>8s}  {'V_source [V]':>13s}  {'|I| [nA]':>12s}  {'Estado':>8s}")
    print(f"  {'─'*8}  {'─'*13}  {'─'*12}  {'─'*8}")

    t_start = time.time()
    try:
        while True:
            i_now = float(inst.query(":READ?").strip())
            elapsed = time.time() - t_start
            writer.writerow([datetime.now().isoformat(timespec="seconds"),
                             f"{elapsed:.1f}", f"{v_source:.3f}", f"{i_now:.6e}"])
            _csv_file_global.flush()

            if abs(i_now) >= I_COMPLIANCE * 0.95:
                estado = "COMPL."
            elif abs(i_now) > I_WARN_BIAS:
                estado = "ALTO"
            else:
                estado = "OK"
            m, s = int(elapsed // 60), int(elapsed % 60)
            print(f"  {m:4d}:{s:02d}  {v_source:>+11.3f} V  {abs(i_now)*1e9:>10.2f} nA  {estado:>8s}")
            time.sleep(MONITOR_INTERVAL)
    except KeyboardInterrupt:
        print("\n\n  *** Ctrl+C — Iniciando apagado seguro ***")

    safe_shutdown(inst, v_source)
    close_csv()
    inst.close()
    rm.close()
    _inst_global = None
    print(f"\n  Corriente registrada en: {csv_name}")
    print(f"  Sesión finalizada: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    main()
