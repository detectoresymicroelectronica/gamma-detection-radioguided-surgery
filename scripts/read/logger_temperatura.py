"""
logger_temperatura.py — Registro de temperatura del banco BETA (bloque 1: lazo abierto)

Lee las lineas que imprime el Arduino por serie (sketch Temp-PID-PWM_V01:
"tiempo(s) tempC pidOutput PWM") y las guarda en un CSV junto con la hora de la PC.

Uso:
    python logger_temperatura.py escalon_1A
    (el argumento es una etiqueta para el nombre del archivo; Ctrl+C para terminar)

Requisitos:
    pip install pyserial
    Cerrar el Monitor Serie del Arduino IDE antes de correrlo (el puerto no se puede compartir).
"""

import sys
import csv
import time
from datetime import datetime

import serial

PUERTO = "COM6"      # el que muestra el Arduino IDE abajo a la derecha
BAUDIOS = 9600

etiqueta = sys.argv[1] if len(sys.argv) > 1 else "prueba"
nombre = f"BETA_{etiqueta}_{datetime.now():%Y-%m-%d_%H%M%S}.csv"

with serial.Serial(PUERTO, BAUDIOS, timeout=2) as ser, open(nombre, "w", newline="") as f:
    time.sleep(2)              # el Arduino se reinicia al abrir el puerto
    ser.reset_input_buffer()

    f.write(f"# BETA - registro de temperatura\n")
    f.write(f"# Etiqueta: {etiqueta}\n")
    f.write(f"# Inicio: {datetime.now().isoformat(timespec='seconds')}\n")
    escritor = csv.writer(f)
    escritor.writerow(["t_pc_s", "t_arduino_s", "temp_C", "pid_output", "pwm"])

    t0 = time.time()
    print(f"Guardando en {nombre} (Ctrl+C para terminar)")
    try:
        while True:
            linea = ser.readline().decode(errors="ignore").strip()
            campos = linea.split()
            if len(campos) != 4:
                continue           # descarta encabezados o lineas incompletas
            t_pc = round(time.time() - t0, 2)
            escritor.writerow([t_pc] + campos)
            f.flush()
            print(f"{t_pc:8.1f} s   {campos[1]} °C")
    except KeyboardInterrupt:
        print(f"\nTerminado. Archivo: {nombre}")
