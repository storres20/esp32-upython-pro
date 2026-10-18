# app.py - MHUTEMP PRO
# Versión sin OLED
# MAX31865 + PT100 de 3 hilos, DHT22, MC-38 y WebSocket SSL
# Compatible con MicroPython 1.28
# - 30/07/2026 03:50 am

import network
import time
import dht
import json
import _thread
import gc

from machine import Pin, SPI
from ws_client import WebSocket
from max31865 import MAX31865


# ============================================
# CONFIGURACIÓN DESDE config.json
# ============================================

CONFIG_FILE = "config.json"

SSID = "motog35"
PASSWORD = "12345678"

WEBSOCKET_URL = (
    "wss://bio-data-pro-production.up.railway.app/"
)

USERNAME = "MHT26-PRO-01"
SEND_INTERVAL_MS = 2000


def load_config():
    global SSID
    global PASSWORD
    global WEBSOCKET_URL
    global USERNAME
    global SEND_INTERVAL_MS

    try:
        with open(CONFIG_FILE, "r") as file:
            config = json.load(file)

        SSID = config.get(
            "ssid",
            SSID
        )

        PASSWORD = config.get(
            "password",
            PASSWORD
        )

        WEBSOCKET_URL = config.get(
            "websocket_url",
            WEBSOCKET_URL
        )

        USERNAME = config.get(
            "username",
            USERNAME
        )

        SEND_INTERVAL_MS = config.get(
            "send_interval_ms",
            SEND_INTERVAL_MS
        )

        del config
        gc.collect()

        print("✅ config.json cargado")
        print(
            "Memoria libre después config:",
            gc.mem_free()
        )

    except Exception as error:
        print(
            "⚠ Error cargando config.json:",
            repr(error)
        )

        print("Usando configuración por defecto")


load_config()


# ============================================
# PINES Y PARÁMETROS
# ============================================

DHT22_PIN = 4

# MAX31865 por SPI VSPI
MAX31865_SCK_PIN = 18
MAX31865_MISO_PIN = 19
MAX31865_MOSI_PIN = 23
MAX31865_CS_PIN = 5
MAX31865_SPI_ID = 2

PT100_NOMINAL_OHMS = 100.0
MAX31865_RREF_OHMS = 430.0
PT100_WIRES = 3
MAINS_FILTER_HZ = 60

WIFI_LED_PIN = 2

MC38_SENSOR_PIN = 15
MC38_LED_PIN = 13

TIMEZONE_OFFSET = -5 * 3600


# ============================================
# ESTADO GLOBAL
# ============================================

time_synced = False
wifi_connected = False
door_closed = False

wlan = None
ws = None

network_thread_running = False

data_lock = _thread.allocate_lock()


# ============================================
# SENSORES Y PERIFÉRICOS
# ============================================

dht22 = dht.DHT22(
    Pin(DHT22_PIN)
)

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
    Pin(
        MAX31865_CS_PIN,
        Pin.OUT
    ),
    wires=PT100_WIRES,
    rtd_nominal=PT100_NOMINAL_OHMS,
    ref_resistor=MAX31865_RREF_OHMS,
    filter_hz=MAINS_FILTER_HZ
)

wifi_led = Pin(
    WIFI_LED_PIN,
    Pin.OUT
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

wifi_led.off()
mc38_led.off()


class SensorData:

    def __init__(self):
        self.dht_temp = 0.0
        self.dht_humidity = 0.0
        self.pt100_temp = 0.0

        self.dht_valid = False
        self.pt100_valid = False


current_data = SensorData()


# ============================================
# TIEMPO Y WI-FI
# ============================================

def sync_time():
    global time_synced

    try:
        import ntptime

        ntptime.settime()
        time_synced = True

        local_time = time.localtime(
            time.time() + TIMEZONE_OFFSET
        )

        print(
            "✓ Tiempo local: "
            "{:02d}/{:02d}/{} {:02d}:{:02d}".format(
                local_time[2],
                local_time[1],
                local_time[0],
                local_time[3],
                local_time[4]
            )
        )

        return True

    except Exception as error:
        time_synced = False

        print(
            "⚠ Error NTP:",
            repr(error)
        )

        return False


def init_wifi():
    global wlan

    wlan = network.WLAN(
        network.STA_IF
    )

    wlan.active(True)

    if not wlan.isconnected():
        print(
            "Conectando WiFi a {}...".format(
                SSID
            )
        )

        wlan.connect(
            SSID,
            PASSWORD
        )

    return wlan


# ============================================
# MAX31865
# ============================================

def decode_max31865_fault(fault):
    faults = []

    if fault & 0x80:
        faults.append("RTD HIGH")

    if fault & 0x40:
        faults.append("RTD LOW")

    if fault & 0x20:
        faults.append(
            "REFIN- > 0.85 VBIAS"
        )

    if fault & 0x10:
        faults.append(
            "REFIN- < 0.85 VBIAS"
        )

    if fault & 0x08:
        faults.append(
            "RTDIN- < 0.85 VBIAS"
        )

    if fault & 0x04:
        faults.append("OV/UV")

    if faults:
        return ", ".join(faults)

    return "sin detalle"


# ============================================
# SENSORES
# ============================================

def init_sensors():
    global door_closed

    print("\nInicializando sensores...")

    # ----------------------------------------
    # MAX31865 + PT100
    # ----------------------------------------

    try:
        temperature = (
            pt100_sensor.temperature()
        )

        resistance = (
            pt100_sensor.resistance()
        )

        with data_lock:
            current_data.pt100_temp = (
                temperature
            )

            current_data.pt100_valid = True

        print(
            "✓ MAX31865 + PT100: "
            "{:.2f} °C | {:.3f} ohm".format(
                temperature,
                resistance
            )
        )

    except Exception as error:
        with data_lock:
            current_data.pt100_valid = False

        print(
            "⚠ MAX31865/PT100:",
            repr(error)
        )

        try:
            fault = pt100_sensor.fault()

            print(
                "  Fault 0x{:02X}: {}".format(
                    fault,
                    decode_max31865_fault(
                        fault
                    )
                )
            )

            pt100_sensor.clear_fault()

        except Exception:
            pass

    # ----------------------------------------
    # DHT22
    # ----------------------------------------

    print(
        "⏳ DHT22: se verificará "
        "en la primera lectura"
    )

    # ----------------------------------------
    # MC-38
    # ----------------------------------------

    try:
        door_closed = bool(
            mc38_sensor.value()
        )

        if door_closed:
            mc38_led.off()

            print(
                "✓ Puerta: CERRADA "
                "(LED OFF)"
            )

        else:
            mc38_led.on()

            print(
                "⚠ Puerta: ABIERTA "
                "(LED ON)"
            )

    except Exception as error:
        print(
            "⚠ MC-38:",
            repr(error)
        )

        door_closed = False


def read_sensors():

    # ----------------------------------------
    # DHT22
    # ----------------------------------------

    try:
        dht22.measure()

        dht_temp = (
            dht22.temperature()
        )

        dht_humidity = (
            dht22.humidity()
        )

        with data_lock:
            current_data.dht_temp = (
                dht_temp
            )

            current_data.dht_humidity = (
                dht_humidity
            )

            current_data.dht_valid = True

    except Exception as error:
        with data_lock:
            current_data.dht_valid = False

        print(
            "⚠ Error DHT22:",
            repr(error)
        )

    # ----------------------------------------
    # MAX31865 + PT100
    # ----------------------------------------

    try:
        temperature = (
            pt100_sensor.temperature()
        )

        if -200.0 <= temperature <= 850.0:
            with data_lock:
                current_data.pt100_temp = (
                    temperature
                )

                current_data.pt100_valid = True

        else:
            with data_lock:
                current_data.pt100_valid = False

            print(
                "⚠ PT100 fuera de rango:",
                temperature
            )

    except Exception as error:
        with data_lock:
            current_data.pt100_valid = False

        print(
            "❌ Error leyendo PT100:",
            repr(error)
        )

        try:
            fault = pt100_sensor.fault()

            print(
                "Fault 0x{:02X}: {}".format(
                    fault,
                    decode_max31865_fault(
                        fault
                    )
                )
            )

            pt100_sensor.clear_fault()

        except Exception:
            pass


def check_door():
    global door_closed

    try:
        state = bool(
            mc38_sensor.value()
        )

        if state != door_closed:
            door_closed = state

            if door_closed:
                mc38_led.off()

                print(
                    "\n🚪 PUERTA CERRADA "
                    "- LED OFF"
                )

            else:
                mc38_led.on()

                print(
                    "\n⚠ PUERTA ABIERTA "
                    "- LED ON"
                )

    except Exception as error:
        print(
            "⚠ Error leyendo MC-38:",
            repr(error)
        )


# ============================================
# WEBSOCKET
# ============================================

def close_websocket():
    global ws

    if ws is not None:
        try:
            ws.close()
        except Exception:
            pass

        try:
            ws.connected = False
        except Exception:
            pass


def connect_websocket_initial():
    global ws

    print(
        "\n🔌 Conexión inicial WebSocket..."
    )

    close_websocket()

    ws = None
    gc.collect()

    ws = WebSocket()

    gc.collect()

    print(
        "Memoria libre antes WS:",
        gc.mem_free()
    )

    try:
        connect_start = time.ticks_ms()

        if not ws.connect(
            WEBSOCKET_URL
        ):
            print(
                "❌ WebSocket inicial "
                "no conectado"
            )

            close_websocket()
            ws = None

            gc.collect()

            return False

        connect_time = time.ticks_diff(
            time.ticks_ms(),
            connect_start
        )

        print(
            "✓ WebSocket conectado "
            "en {} ms".format(
                connect_time
            )
        )

        time.sleep_ms(300)

        intro = json.dumps({
            "username": USERNAME
        })

        if ws.send(intro):
            print(
                "✓ Username enviado:",
                USERNAME
            )

        else:
            print(
                "⚠ No se pudo enviar "
                "el username inicial"
            )

        del intro

        gc.collect()

        print(
            "Memoria libre después WS:",
            gc.mem_free()
        )

        return True

    except Exception as error:
        print(
            "❌ Error conexión inicial "
            "WebSocket:",
            repr(error)
        )

        close_websocket()
        ws = None

        gc.collect()

        return False


def recreate_and_connect_websocket():
    global ws

    close_websocket()

    ws = None

    gc.collect()
    time.sleep_ms(1000)

    ws = WebSocket()

    gc.collect()

    try:
        connect_start = time.ticks_ms()

        print(
            "Memoria libre antes WS:",
            gc.mem_free()
        )

        if not ws.connect(
            WEBSOCKET_URL
        ):
            close_websocket()

            ws = None

            gc.collect()

            return False

        connect_time = time.ticks_diff(
            time.ticks_ms(),
            connect_start
        )

        print(
            "✓ WebSocket reconectado "
            "en {} ms".format(
                connect_time
            )
        )

        time.sleep_ms(300)

        intro = json.dumps({
            "username": USERNAME
        })

        if not ws.send(intro):
            print(
                "⚠ No se pudo reenviar "
                "el username"
            )

            close_websocket()

            ws = None

            gc.collect()

            return False

        print(
            "✓ Username reenviado:",
            USERNAME
        )

        del intro

        gc.collect()

        print(
            "Memoria libre después WS:",
            gc.mem_free()
        )

        return True

    except Exception as error:
        print(
            "❌ Error reconectando "
            "WebSocket:",
            repr(error)
        )

        close_websocket()

        ws = None

        gc.collect()

        return False


# ============================================
# HILO DE RED
# ============================================

def network_thread():
    global wifi_connected
    global ws
    global wlan
    global network_thread_running
    global time_synced

    network_thread_running = True

    print(
        "🔷 Gestor de red iniciado"
    )

    if ws is None:
        ws = WebSocket()

    last_wifi_check = (
        time.ticks_ms()
    )

    last_send = (
        time.ticks_ms()
    )

    last_ping = (
        time.ticks_ms()
    )

    last_ntp_sync = (
        time.ticks_ms()
    )

    last_recv = (
        time.ticks_ms()
    )

    last_ws_attempt = (
        time.ticks_ms()
    )

    reconnect_attempts = 0
    ws_reconnect_delay = 30000

    wifi_was_connected = (
        wifi_connected
    )

    try:
        while network_thread_running:
            now = time.ticks_ms()

            # ====================================
            # VERIFICACIÓN DE WI-FI
            # ====================================

            if (
                time.ticks_diff(
                    now,
                    last_wifi_check
                )
                >= 5000
            ):
                try:
                    if (
                        wlan
                        and wlan.isconnected()
                    ):
                        if not wifi_connected:
                            wifi_connected = True
                            wifi_led.on()

                            reconnect_attempts = 0

                            print(
                                "\n✅ WiFi RECONECTADO"
                            )

                            print(
                                "   IP:",
                                wlan.ifconfig()[0]
                            )

                            try:
                                print(
                                    "   RSSI: {} dBm".format(
                                        wlan.status(
                                            "rssi"
                                        )
                                    )
                                )

                            except Exception:
                                pass

                            if not time_synced:
                                sync_time()

                            if wifi_was_connected:
                                close_websocket()

                        wifi_was_connected = True

                    else:
                        if wifi_connected:
                            print(
                                "\n⚠ WiFi DESCONECTADO"
                            )

                            wifi_connected = False

                            wifi_led.off()

                            close_websocket()

                        wifi_was_connected = False

                        print(
                            "🔄 Reintentando WiFi..."
                        )

                        if wlan:
                            try:
                                wlan.disconnect()

                                time.sleep_ms(200)

                            except Exception:
                                pass

                            wlan.connect(
                                SSID,
                                PASSWORD
                            )

                except Exception as error:
                    print(
                        "Error verificando WiFi:",
                        repr(error)
                    )

                last_wifi_check = now

            # ====================================
            # SINCRONIZACIÓN NTP
            # ====================================

            if (
                wifi_connected
                and time.ticks_diff(
                    now,
                    last_ntp_sync
                )
                >= 3600000
            ):
                sync_time()

                last_ntp_sync = now

            # ====================================
            # WEBSOCKET
            # ====================================

            if wifi_connected:
                ws_connected = bool(
                    ws
                    and ws.connected
                )

                # --------------------------------
                # Reconexión
                # --------------------------------

                if not ws_connected:
                    if (
                        time.ticks_diff(
                            now,
                            last_ws_attempt
                        )
                        >= ws_reconnect_delay
                    ):
                        reconnect_attempts += 1

                        last_ws_attempt = now

                        print(
                            "\n🔌 Reconectando "
                            "WebSocket "
                            "(intento {})...".format(
                                reconnect_attempts
                            )
                        )

                        if (
                            recreate_and_connect_websocket()
                        ):
                            reconnect_attempts = 0
                            ws_reconnect_delay = 30000

                            last_ping = (
                                time.ticks_ms()
                            )

                            last_send = (
                                time.ticks_ms()
                            )

                            last_recv = (
                                time.ticks_ms()
                            )

                        else:
                            print(
                                "❌ WebSocket no conectado "
                                "(intento {})".format(
                                    reconnect_attempts
                                )
                            )

                            if reconnect_attempts >= 5:
                                ws_reconnect_delay = 60000

                                print(
                                    "⏳ Próximo intento "
                                    "en 60 segundos"
                                )

                            elif reconnect_attempts >= 3:
                                ws_reconnect_delay = 30000

                                print(
                                    "⏳ Próximo intento "
                                    "en 30 segundos"
                                )

                            else:
                                ws_reconnect_delay = 30000

                # --------------------------------
                # Recepción
                # --------------------------------

                if (
                    ws
                    and ws.connected
                    and time.ticks_diff(
                        now,
                        last_recv
                    )
                    >= 500
                ):
                    try:
                        message = ws.recv()

                        if message:
                            print(
                                "📥 Servidor:",
                                message
                            )

                            try:
                                parsed = json.loads(
                                    message
                                )

                                if (
                                    parsed.get("type")
                                    == "pong"
                                ):
                                    print(
                                        "📶 PONG recibido"
                                    )

                                del parsed

                            except Exception:
                                pass

                    except OSError as error:
                        if (
                            not error.args
                            or error.args[0] != 11
                        ):
                            print(
                                "⚠ Error recv "
                                "OSError:",
                                repr(error)
                            )

                    except Exception as error:
                        if (
                            "timeout"
                            not in str(
                                error
                            ).lower()
                        ):
                            print(
                                "⚠ Error en recv:",
                                repr(error)
                            )

                    last_recv = now

                # --------------------------------
                # Envío de datos
                # --------------------------------

                if (
                    ws
                    and ws.connected
                    and time.ticks_diff(
                        now,
                        last_send
                    )
                    >= SEND_INTERVAL_MS
                ):
                    try:
                        utc_time = time.gmtime()

                        datetime_utc = (
                            "{:04d}-{:02d}-{:02d}T"
                            "{:02d}:{:02d}:{:02d}Z"
                        ).format(
                            utc_time[0],
                            utc_time[1],
                            utc_time[2],
                            utc_time[3],
                            utc_time[4],
                            utc_time[5]
                        )

                        with data_lock:
                            data = {
                                "username": USERNAME,

                                # Se conserva este
                                # nombre por compatibilidad
                                # con el backend.
                                "dsTemperature": (
                                    round(
                                        current_data.pt100_temp,
                                        2
                                    )
                                    if current_data.pt100_valid
                                    else None
                                ),

                                "temperature": (
                                    round(
                                        current_data.dht_temp,
                                        1
                                    )
                                    if current_data.dht_valid
                                    else None
                                ),

                                "humidity": (
                                    int(
                                        round(
                                            current_data.dht_humidity,
                                            0
                                        )
                                    )
                                    if current_data.dht_valid
                                    else None
                                ),

                                "datetime": datetime_utc,

                                "doorStatus": (
                                    "closed"
                                    if door_closed
                                    else "open"
                                )
                            }

                        json_str = json.dumps(
                            data
                        )

                        if ws.send(json_str):
                            print(
                                "📤 WS | PT100: {}°C | "
                                "T.IN: {}°C | "
                                "H: {}% | {}".format(
                                    data[
                                        "dsTemperature"
                                    ],
                                    data[
                                        "temperature"
                                    ],
                                    data[
                                        "humidity"
                                    ],
                                    (
                                        "🚪✅"
                                        if door_closed
                                        else "🚪⚠"
                                    )
                                )
                            )

                        else:
                            print(
                                "❌ Error enviando datos"
                            )

                            close_websocket()

                        del data
                        del json_str

                    except Exception as error:
                        print(
                            "❌ Error en envío:",
                            repr(error)
                        )

                        close_websocket()

                    last_send = now

                # --------------------------------
                # Ping
                # --------------------------------

                if (
                    ws
                    and ws.connected
                    and time.ticks_diff(
                        now,
                        last_ping
                    )
                    >= 30000
                ):
                    try:
                        if ws.send(
                            '{"type":"ping"}'
                        ):
                            print(
                                "📶 Ping enviado"
                            )

                        else:
                            close_websocket()

                    except Exception:
                        close_websocket()

                    last_ping = now

            else:
                if (
                    ws
                    and ws.connected
                ):
                    close_websocket()

                    print(
                        "⚠ WebSocket desconectado "
                        "por falta de Wi-Fi"
                    )

            time.sleep_ms(100)

    except Exception as error:
        print(
            "💥 Error crítico en "
            "gestor de red:",
            repr(error)
        )

    finally:
        network_thread_running = False

        close_websocket()

        print(
            "🔷 Gestor de red detenido"
        )


# ============================================
# MAIN
# ============================================

def main():
    global wlan
    global wifi_connected
    global network_thread_running

    print(
        "\n" + "=" * 50
    )

    print(
        "ESP32 Monitor MHUTEMP PRO"
    )

    print(
        "VERSIÓN SIN OLED"
    )

    print(
        "=" * 50
    )

    print(
        "URL:",
        WEBSOCKET_URL
    )

    print(
        "Username:",
        USERNAME
    )

    print(
        "DHT22: GPIO{}".format(
            DHT22_PIN
        )
    )

    print(
        "MAX31865 SPI -> "
        "SCK:GPIO{} MISO:GPIO{} "
        "MOSI:GPIO{} CS:GPIO{}".format(
            MAX31865_SCK_PIN,
            MAX31865_MISO_PIN,
            MAX31865_MOSI_PIN,
            MAX31865_CS_PIN
        )
    )

    print(
        "PT100: {} hilos | "
        "Rref={} ohm".format(
            PT100_WIRES,
            MAX31865_RREF_OHMS
        )
    )

    print(
        "MC-38: GPIO{} "
        "(LED: GPIO{})".format(
            MC38_SENSOR_PIN,
            MC38_LED_PIN
        )
    )

    print(
        "=" * 50 + "\n"
    )

    # ========================================
    # INICIALIZACIÓN DE SENSORES
    # ========================================

    init_sensors()
    read_sensors()

    gc.collect()

    print(
        "Memoria después de sensores:",
        gc.mem_free()
    )

    # ========================================
    # CONEXIÓN WI-FI
    # ========================================

    wlan = init_wifi()

    print(
        "Esperando conexión WiFi inicial..."
    )

    timeout = 20

    while (
        not wlan.isconnected()
        and timeout > 0
    ):
        time.sleep(1)
        timeout -= 1

    if wlan.isconnected():
        wifi_connected = True

        wifi_led.on()

        print(
            "✓ WiFi conectado:",
            wlan.ifconfig()[0]
        )

        try:
            print(
                "✓ RSSI: {} dBm".format(
                    wlan.status("rssi")
                )
            )

        except Exception:
            pass

        sync_time()

        time.sleep(2)

        gc.collect()

        print(
            "Memoria antes de "
            "conexión WebSocket:",
            gc.mem_free()
        )

        # Primer TLS/WebSocket en
        # el hilo principal.
        connect_websocket_initial()

    else:
        wifi_connected = False

        wifi_led.off()

        print(
            "⚠ WiFi no conectado "
            "inicialmente"
        )

    # ========================================
    # HILO SECUNDARIO
    # ========================================

    try:
        _thread.stack_size(8192)

        print(
            "✓ Stack del hilo de red: "
            "8192 bytes"
        )

    except Exception as error:
        print(
            "⚠ No se pudo configurar "
            "stack del hilo:",
            repr(error)
        )

    gc.collect()

    print(
        "Memoria antes de iniciar "
        "hilo de red:",
        gc.mem_free()
    )

    print(
        "\n🚀 Iniciando gestor de red..."
    )

    _thread.start_new_thread(
        network_thread,
        ()
    )

    time.sleep(1)

    print(
        "\n" + "=" * 50
    )

    print(
        "Sistema iniciado - "
        "Presiona Ctrl+C para detener"
    )

    print(
        "🔶 Principal: sensores"
    )

    print(
        "🔷 Secundario: Wi-Fi y WebSocket"
    )

    print(
        "=" * 50 + "\n"
    )

    last_sensor = time.ticks_ms()
    last_door = time.ticks_ms()
    last_gc = time.ticks_ms()

    try:
        while True:
            now = time.ticks_ms()

            if (
                time.ticks_diff(
                    now,
                    last_sensor
                )
                >= 2000
            ):
                read_sensors()

                last_sensor = now

            if (
                time.ticks_diff(
                    now,
                    last_door
                )
                >= 100
            ):
                check_door()

                last_door = now

            if (
                time.ticks_diff(
                    now,
                    last_gc
                )
                >= 60000
            ):
                gc.collect()

                print(
                    "🧹 Memoria libre:",
                    gc.mem_free()
                )

                last_gc = now

            time.sleep_ms(20)

    except KeyboardInterrupt:
        print(
            "\n\n" + "=" * 50
        )

        print(
            "Deteniendo sistema..."
        )

        print(
            "=" * 50
        )

        network_thread_running = False

        time.sleep(1)

        close_websocket()

        wifi_led.off()
        mc38_led.off()

        print(
            "Sistema detenido"
        )


if __name__ == "__main__":
    main()
