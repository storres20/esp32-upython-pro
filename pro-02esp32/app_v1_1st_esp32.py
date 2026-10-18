# app.py - MHUTEMP PRO - ESP32 1
# Sensores + OLED + UART hacia ESP32 2
#
# Compatible con MicroPython 1.28
#
# Funciones:
# - Lee MAX31865 + PT100 de 3 hilos.
# - Lee DHT22.
# - Lee MC-38 y controla su LED.
# - Muestra datos en OLED SH1106 o SSD1306.
# - Envía por UART una línea JSON cada send_interval_ms.
#
# Este ESP32 NO usa Wi-Fi, TLS ni WebSocket.

import time
import json
import gc
import dht

from machine import Pin, SPI, I2C, UART
from max31865 import MAX31865


# ============================================================
# CONFIGURACIÓN DESDE config.json
# ============================================================

CONFIG_FILE = "config.json"

USERNAME = "MHT26-PRO-01"
# ========================
# SH1106 - display_rotate_180 = True
# SSD1306 - display_rotate_180 = False
DISPLAY_TYPE = "SH1106"
DISPLAY_ROTATE_180 = True
# ========================
SEND_INTERVAL_MS = 2000


def load_config():
    global USERNAME
    global DISPLAY_TYPE
    global DISPLAY_ROTATE_180
    global SEND_INTERVAL_MS

    try:
        with open(CONFIG_FILE, "r") as file:
            config = json.load(file)

        USERNAME = config.get("username", USERNAME)
        DISPLAY_TYPE = str(
            config.get("display_type", DISPLAY_TYPE)
        ).upper()

        DISPLAY_ROTATE_180 = bool(
            config.get(
                "display_rotate_180",
                DISPLAY_ROTATE_180
            )
        )

        SEND_INTERVAL_MS = int(
            config.get(
                "send_interval_ms",
                SEND_INTERVAL_MS
            )
        )

        del config
        gc.collect()

        print("✅ config.json cargado")
        print("Username:", USERNAME)
        print("Display:", DISPLAY_TYPE)
        print("Rotación 180°:", DISPLAY_ROTATE_180)
        print("Intervalo UART:", SEND_INTERVAL_MS, "ms")

    except Exception as error:
        print("⚠ Error cargando config.json:", repr(error))
        print("Usando configuración por defecto")


load_config()


# ============================================================
# PINES Y PARÁMETROS
# ============================================================

DHT22_PIN = 4

# MAX31865 por SPI VSPI
MAX31865_SPI_ID = 2
MAX31865_SCK_PIN = 18
MAX31865_MISO_PIN = 19
MAX31865_MOSI_PIN = 23
MAX31865_CS_PIN = 5

PT100_NOMINAL_OHMS = 100.0
MAX31865_RREF_OHMS = 430.0
PT100_WIRES = 3
MAINS_FILTER_HZ = 60

# MC-38
MC38_SENSOR_PIN = 15
MC38_LED_PIN = 13

# OLED I2C
OLED_I2C_ID = 0
OLED_SDA_PIN = 21
OLED_SCL_PIN = 22
OLED_WIDTH = 128
OLED_HEIGHT = 64
OLED_ADDRESS = 0x3C
OLED_I2C_FREQUENCY = 400000

# UART2 hacia ESP32 2
UART_ID = 2
UART_BAUDRATE = 115200
UART_TX_PIN = 17
UART_RX_PIN = 16
UART_RX_BUFFER = 1024

SENSOR_INTERVAL_MS = 2000
DOOR_INTERVAL_MS = 100
OLED_INTERVAL_MS = 500
GC_INTERVAL_MS = 60000


# ============================================================
# ESTADO GLOBAL
# ============================================================

door_closed = False
oled = None
oled_available = False
uart = None


class SensorData:
    def __init__(self):
        self.dht_temp = 0.0
        self.dht_humidity = 0.0
        self.pt100_temp = 0.0
        self.dht_valid = False
        self.pt100_valid = False


current_data = SensorData()


# ============================================================
# SENSORES Y PERIFÉRICOS
# ============================================================

dht22 = dht.DHT22(Pin(DHT22_PIN))

max_spi = SPI(
    MAX31865_SPI_ID,
    baudrate=500000,
    polarity=0,
    phase=1,
    sck=Pin(MAX31865_SCK_PIN),
    mosi=Pin(MAX31865_MOSI_PIN),
    miso=Pin(MAX31865_MISO_PIN)
)

pt100_sensor = MAX31865(
    max_spi,
    Pin(MAX31865_CS_PIN, Pin.OUT),
    wires=PT100_WIRES,
    rtd_nominal=PT100_NOMINAL_OHMS,
    ref_resistor=MAX31865_RREF_OHMS,
    filter_hz=MAINS_FILTER_HZ
)

mc38_sensor = Pin(
    MC38_SENSOR_PIN,
    Pin.IN,
    Pin.PULL_DOWN
)

mc38_led = Pin(
    MC38_LED_PIN,
    Pin.OUT
)

mc38_led.off()


# ============================================================
# MAX31865
# ============================================================

def decode_max31865_fault(fault):
    faults = []

    if fault & 0x80:
        faults.append("RTD HIGH")
    if fault & 0x40:
        faults.append("RTD LOW")
    if fault & 0x20:
        faults.append("REFIN- > 0.85 VBIAS")
    if fault & 0x10:
        faults.append("REFIN- < 0.85 VBIAS")
    if fault & 0x08:
        faults.append("RTDIN- < 0.85 VBIAS")
    if fault & 0x04:
        faults.append("OV/UV")

    return ", ".join(faults) if faults else "sin detalle"


# ============================================================
# UART
# ============================================================

def init_uart():
    global uart

    uart = UART(
        UART_ID,
        baudrate=UART_BAUDRATE,
        bits=8,
        parity=None,
        stop=1,
        tx=Pin(UART_TX_PIN),
        rx=Pin(UART_RX_PIN),
        timeout=50,
        timeout_char=20,
        rxbuf=UART_RX_BUFFER
    )

    print(
        "✓ UART{} iniciado | TX GPIO{} -> ESP32 2 RX GPIO{} | {} baudios".format(
            UART_ID,
            UART_TX_PIN,
            UART_RX_PIN,
            UART_BAUDRATE
        )
    )


def build_uart_payload():
    return {
        "username": USERNAME,

        # Se conserva este nombre por compatibilidad
        # con el backend existente.
        "dsTemperature": (
            round(current_data.pt100_temp, 2)
            if current_data.pt100_valid
            else None
        ),

        "temperature": (
            round(current_data.dht_temp, 1)
            if current_data.dht_valid
            else None
        ),

        "humidity": (
            int(round(current_data.dht_humidity, 0))
            if current_data.dht_valid
            else None
        ),

        "doorStatus": (
            "closed"
            if door_closed
            else "open"
        )
    }


def send_uart_data():
    if uart is None:
        print("❌ UART no inicializado")
        return False

    try:
        data = build_uart_payload()

        # El salto de línea permite al ESP32 2 usar uart.readline().
        json_line = json.dumps(data) + "\n"

        sent = uart.write(json_line)

        if sent:
            print(
                "📤 UART | {} | PT100: {}°C | T.IN: {}°C | H: {}% | {}".format(
                    data["username"],
                    data["dsTemperature"],
                    data["temperature"],
                    data["humidity"],
                    "🚪✅" if door_closed else "🚪⚠"
                )
            )
            return True

        print("❌ No se pudo enviar por UART")
        return False

    except Exception as error:
        print("❌ Error UART:", repr(error))
        return False


# ============================================================
# OLED SH1106 / SSD1306
# ============================================================

def rotate_display_if_supported(display):
    if not DISPLAY_ROTATE_180:
        return

    try:
        display.rotate(1)
        print("✓ OLED rotado 180°")
        return
    except Exception:
        pass

    try:
        display.rotate(True)
        print("✓ OLED rotado 180°")
    except Exception:
        print(
            "⚠ La librería OLED instalada no expone rotate(); "
            "se continuará sin rotación"
        )


def init_oled():
    global oled
    global oled_available

    try:
        i2c = I2C(
            OLED_I2C_ID,
            scl=Pin(OLED_SCL_PIN),
            sda=Pin(OLED_SDA_PIN),
            freq=OLED_I2C_FREQUENCY
        )

        detected = i2c.scan()

        print(
            "I2C detectado:",
            ["0x{:02X}".format(address) for address in detected]
        )

        if OLED_ADDRESS not in detected:
            print(
                "⚠ No se detectó OLED en 0x{:02X}".format(
                    OLED_ADDRESS
                )
            )

        if DISPLAY_TYPE == "SSD1306":
            import ssd1306

            oled = ssd1306.SSD1306_I2C(
                OLED_WIDTH,
                OLED_HEIGHT,
                i2c,
                addr=OLED_ADDRESS
            )

        elif DISPLAY_TYPE == "SH1106":
            import sh1106

            # Compatibilidad con las firmas más usadas de SH1106_I2C.
            try:
                oled = sh1106.SH1106_I2C(
                    OLED_WIDTH,
                    OLED_HEIGHT,
                    i2c,
                    None,
                    OLED_ADDRESS
                )
            except TypeError:
                try:
                    oled = sh1106.SH1106_I2C(
                        OLED_WIDTH,
                        OLED_HEIGHT,
                        i2c,
                        addr=OLED_ADDRESS
                    )
                except TypeError:
                    oled = sh1106.SH1106_I2C(
                        OLED_WIDTH,
                        OLED_HEIGHT,
                        i2c
                    )

            try:
                oled.sleep(False)
            except Exception:
                pass

        else:
            raise ValueError(
                "display_type debe ser SH1106 o SSD1306"
            )

        rotate_display_if_supported(oled)

        oled.fill(0)
        oled.text(USERNAME, 0, 8)
        oled.text("INICIANDO...", 16, 28)
        oled.text("UART -> ESP32 2", 4, 46)
        oled.show()

        oled_available = True

        print(
            "✓ OLED {} iniciado | SDA GPIO{} / SCL GPIO{}".format(
                DISPLAY_TYPE,
                OLED_SDA_PIN,
                OLED_SCL_PIN
            )
        )

        time.sleep_ms(1000)
        return True

    except Exception as error:
        oled = None
        oled_available = False

        print("⚠ Error inicializando OLED:", repr(error))
        return False


def update_oled():
    if not oled_available or oled is None:
        return

    try:
        pt100_text = (
            "{:.2f}C".format(current_data.pt100_temp)
            if current_data.pt100_valid
            else "--"
        )

        dht_text = (
            "{:.1f}C".format(current_data.dht_temp)
            if current_data.dht_valid
            else "--"
        )

        humidity_text = (
            "{}%".format(
                int(round(current_data.dht_humidity, 0))
            )
            if current_data.dht_valid
            else "--"
        )

        door_text = (
            "CERRADA"
            if door_closed
            else "ABIERTA"
        )

        oled.fill(0)

        # Identificación visible del nodo.
        oled.text(USERNAME, 0, 0)
        oled.hline(0, 10, OLED_WIDTH, 1)

        oled.text("PT100:", 0, 16)
        oled.text(pt100_text, 56, 16)

        oled.text("T.IN :", 0, 28)
        oled.text(dht_text, 56, 28)

        oled.text("HUM  :", 0, 40)
        oled.text(humidity_text, 56, 40)

        oled.text("P:", 0, 52)
        oled.text(door_text, 20, 52)

        oled.show()

    except Exception as error:
        print("⚠ Error actualizando OLED:", repr(error))


# ============================================================
# SENSORES
# ============================================================

def init_sensors():
    global door_closed

    print("\nInicializando sensores...")

    try:
        temperature = pt100_sensor.temperature()
        resistance = pt100_sensor.resistance()

        current_data.pt100_temp = temperature
        current_data.pt100_valid = True

        print(
            "✓ MAX31865 + PT100: {:.2f} °C | {:.3f} ohm".format(
                temperature,
                resistance
            )
        )

    except Exception as error:
        current_data.pt100_valid = False
        print("⚠ MAX31865/PT100:", repr(error))

        try:
            fault = pt100_sensor.fault()

            print(
                "  Fault 0x{:02X}: {}".format(
                    fault,
                    decode_max31865_fault(fault)
                )
            )

            pt100_sensor.clear_fault()
        except Exception:
            pass

    print("⏳ DHT22: se verificará en la primera lectura")

    try:
        door_closed = bool(mc38_sensor.value())

        if door_closed:
            mc38_led.off()
            print("✓ Puerta: CERRADA (LED OFF)")
        else:
            mc38_led.on()
            print("⚠ Puerta: ABIERTA (LED ON)")

    except Exception as error:
        door_closed = False
        mc38_led.on()
        print("⚠ MC-38:", repr(error))


def read_sensors():
    try:
        dht22.measure()

        current_data.dht_temp = dht22.temperature()
        current_data.dht_humidity = dht22.humidity()
        current_data.dht_valid = True

    except Exception as error:
        current_data.dht_valid = False
        print("⚠ Error DHT22:", repr(error))

    try:
        temperature = pt100_sensor.temperature()

        if -200.0 <= temperature <= 850.0:
            current_data.pt100_temp = temperature
            current_data.pt100_valid = True
        else:
            current_data.pt100_valid = False
            print("⚠ PT100 fuera de rango:", temperature)

    except Exception as error:
        current_data.pt100_valid = False
        print("❌ Error leyendo PT100:", repr(error))

        try:
            fault = pt100_sensor.fault()

            print(
                "Fault 0x{:02X}: {}".format(
                    fault,
                    decode_max31865_fault(fault)
                )
            )

            pt100_sensor.clear_fault()
        except Exception:
            pass


def check_door():
    global door_closed

    try:
        state = bool(mc38_sensor.value())

        if state != door_closed:
            door_closed = state

            if door_closed:
                mc38_led.off()
                print("\n🚪 PUERTA CERRADA - LED OFF")
            else:
                mc38_led.on()
                print("\n⚠ PUERTA ABIERTA - LED ON")

    except Exception as error:
        print("⚠ Error leyendo MC-38:", repr(error))


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "=" * 52)
    print("MHUTEMP PRO - ESP32 1")
    print("SENSORES + OLED + UART")
    print("=" * 52)

    print("Username:", USERNAME)
    print("Display:", DISPLAY_TYPE)
    print("DHT22: GPIO{}".format(DHT22_PIN))

    print(
        "MAX31865 -> SCK:{} MISO:{} MOSI:{} CS:{}".format(
            MAX31865_SCK_PIN,
            MAX31865_MISO_PIN,
            MAX31865_MOSI_PIN,
            MAX31865_CS_PIN
        )
    )

    print(
        "OLED -> SDA:{} SCL:{}".format(
            OLED_SDA_PIN,
            OLED_SCL_PIN
        )
    )

    print(
        "UART{} -> TX:{} RX:{} | {} baudios".format(
            UART_ID,
            UART_TX_PIN,
            UART_RX_PIN,
            UART_BAUDRATE
        )
    )

    print("=" * 52)

    init_uart()
    init_sensors()

    # El DHT22 necesita un breve tiempo antes de la primera medición.
    time.sleep_ms(2000)

    read_sensors()
    init_oled()
    update_oled()

    gc.collect()

    print("Memoria libre:", gc.mem_free())

    now = time.ticks_ms()

    last_sensor = now
    last_door = now
    last_oled = now

    # Permite enviar inmediatamente la primera lectura.
    last_uart = time.ticks_add(now, -SEND_INTERVAL_MS)

    last_gc = now

    print("\n" + "=" * 52)
    print("Sistema iniciado - Presiona Ctrl+C para detener")
    print("🔶 Sensores + OLED")
    print("📤 UART hacia ESP32 2")
    print("=" * 52 + "\n")

    try:
        while True:
            now = time.ticks_ms()

            if (
                time.ticks_diff(now, last_door)
                >= DOOR_INTERVAL_MS
            ):
                check_door()
                last_door = now

            if (
                time.ticks_diff(now, last_sensor)
                >= SENSOR_INTERVAL_MS
            ):
                read_sensors()
                last_sensor = now

            if (
                time.ticks_diff(now, last_oled)
                >= OLED_INTERVAL_MS
            ):
                update_oled()
                last_oled = now

            if (
                time.ticks_diff(now, last_uart)
                >= SEND_INTERVAL_MS
            ):
                send_uart_data()
                last_uart = now

            if (
                time.ticks_diff(now, last_gc)
                >= GC_INTERVAL_MS
            ):
                gc.collect()
                print("🧹 Memoria libre:", gc.mem_free())
                last_gc = now

            time.sleep_ms(20)

    except KeyboardInterrupt:
        print("\nDeteniendo ESP32 1...")

        mc38_led.off()

        if oled_available and oled is not None:
            try:
                oled.fill(0)
                oled.text(USERNAME, 0, 14)
                oled.text("DETENIDO", 28, 34)
                oled.show()
            except Exception:
                pass

        try:
            if uart is not None:
                uart.deinit()
        except Exception:
            pass

        try:
            max_spi.deinit()
        except Exception:
            pass

        print("ESP32 1 detenido")


if __name__ == "__main__":
    main()
