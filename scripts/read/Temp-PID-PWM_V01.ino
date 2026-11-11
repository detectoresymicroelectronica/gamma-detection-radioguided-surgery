// Librerías necesarias
#include <OneWire.h>
#include <DallasTemperature.h>

// Configuración del pin de datos
const int ONE_WIRE_BUS = 2;  // Pin del sensor de temperatura
const int DEVICE_PIN = 5;    // Pin del dispositivo (relevador, ventilador, etc.)

// Instancia de OneWire para comunicación con los sensores
OneWire oneWire(ONE_WIRE_BUS);

// Instancia de DallasTemperature para manejar los sensores
DallasTemperature sensors(&oneWire);

// Configuración inicial
const int TEMPERATURE_RESOLUTION = 12; // Resolución en bits
const float SETPOINT = 10.0;           // Temperatura deseada (°C)
const float CONTROL_THRESHOLD = 10.0;  // Umbral para la lógica *on/off*

// Constantes del PID
const float KP = 100.0;  // Ganancia proporcional
const float KI = 0.5;  // Ganancia integral
const float KD = 0.0;  // Ganancia derivativa

// Variables del PID
float previousError = 0; // Error del ciclo anterior
float integral = 0;      // Acumulador de la integral
float previousTime = 0;  // Tiempo anterior para cálculo del derivativo

/*
   Configuración inicial del Arduino
*/
void setup() {
  Serial.begin(9600);
  while (!Serial) {
    delay(10);
  }

  pinMode(DEVICE_PIN, OUTPUT);
  digitalWrite(DEVICE_PIN, LOW); // Inicia apagado

  sensors.begin();
  sensors.setResolution(TEMPERATURE_RESOLUTION);

  Serial.print("# Controlador PID On/Off iniciado.\n");
  Serial.print("# Resolución configurada: ");
  Serial.print(TEMPERATURE_RESOLUTION);
  Serial.print("\n");
  Serial.print("# Setpoint: ");
  Serial.print(SETPOINT);
  Serial.print("\n");
}

/*
   Bucle principal
*/
void loop() {
  // Solicitar temperaturas a todos los sensores
  sensors.requestTemperatures();

  // Obtener la temperatura del primer sensor (índice 0)
  float tempC = sensors.getTempCByIndex(0);

  if (tempC != DEVICE_DISCONNECTED_C) {
    // Calcular el error actual
    float error = SETPOINT - tempC;

    // Obtener el tiempo actual y calcular deltaTime
    float currentTime = millis() / 1000.0; // Tiempo en segundos
    float deltaTime = currentTime - previousTime;

    // Calcular los términos del PID
    float proportional = KP * error;
    integral += KI * error * deltaTime;
    float derivative = KD * (error - previousError) / deltaTime;

    // Calcular la salida del PID
    float pidOutput = proportional + integral + derivative;

    int PWM = int(-pidOutput);
    if (PWM < 0) PWM = 0;
    if (PWM > 255) PWM = 255;

    analogWrite(DEVICE_PIN, PWM);
    
    // Mostrar valores en el monitor serie
    Serial.print(currentTime, 0);
    Serial.print("\t");
    Serial.print(tempC, 3);
    Serial.print("\t");
    Serial.print(pidOutput, 3);
    Serial.print("\t");
    Serial.print(PWM);
    Serial.print("\n");

    // Actualizar variables para el próximo ciclo
    previousError = error;
    previousTime = currentTime;
  } else {
    Serial.println("# Error: No se pudo leer la temperatura.");
  }

  // Esperar antes de la próxima lectura
  delay(1000);
}
