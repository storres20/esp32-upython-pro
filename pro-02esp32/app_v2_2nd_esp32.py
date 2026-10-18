# app.py - MHUTEMP PRO - ESP32 2
# UART + Wi-Fi + WebSocket SSL
#
# Compatible con MicroPython 1.28
#
# Funciones:
# - Recibe por UART el JSON del ESP32 1.
# - Se conecta al Wi-Fi.
# - Sincroniza hora mediante NTP.
# - Mantiene WebSocket SSL con Railway.
# - Agrega datetime UTC.
# - Envía únicamente datos UART recientes y válidos.
# - Devuelve por UART el estado de Wi-Fi, RSSI y WebSocket.
#
# Requiere:
# - ws_client.py
# - config.json

import network
import time
import json
import _thread
import gc

from machine import Pin, UART
from ws_client import WebSocket


# ============================================================
# CONFIGURACIÓN DESDE config.json
# ============================================================

CONFIG_FILE = "config.json"

SSID = "motog35"
PASSWORD = "12345678"

WEBSOCKET_URL = (
    "wss://bio-data-pro-production.up.railway.app/"
)

# Respaldo local. Normalmente debe coincidir con el ESP32 1.
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

        SSID = config.get("ssid", SSID)
        PASSWORD = config.get("password", PASSWORD)

        WEBSOCKET_URL = config.get(
            "websocket_url",
            WEBSOCKET_URL
        )

        USERNAME = config.get(
            "username",
            USERNAME
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
        print("Username de respaldo:", USERNAME)
        print("Intervalo WebSocket:", SEND_INTERVAL_MS, "ms")

    except Exception as error:
        print("⚠ Error cargando config.json:", repr(error))
        print("Usando configuración por defecto")


load_config()


# ============================================================
# PINES Y PARÁMETROS
# ============================================================

WIFI_LED_PIN = 2
TIMEZONE_OFFSET = -5 * 3600

# UART2 desde ESP32 1
UART_ID = 2
UART_BAUDRATE = 115200
UART_RX_PIN = 16
UART_TX_PIN = 17
UART_RX_BUFFER = 2048

UART_DATA_TIMEOUT_MS = 10000
UART_CHECK_INTERVAL_MS = 20
WIFI_CHECK_INTERVAL_MS = 5000
NTP_SYNC_INTERVAL_MS = 3600000
WS_RECV_INTERVAL_MS = 500
WS_PING_INTERVAL_MS = 30000
GC_INTERVAL_MS = 60000
NETWORK_STATUS_INTERVAL_MS = 2000


# ============================================================
# ESTADO GLOBAL
# ============================================================

time_synced = False
wifi_connected = False

wlan = None
ws = None
uart = None

network_thread_running = False
uart_thread_running = False

data_lock = _thread.allocate_lock()
uart_write_lock = _thread.allocate_lock()

latest_uart_data = None
last_uart_data_ms = None
uart_data_valid = False

# Username con el que se hizo el registro actual del WebSocket.
registered_username = None


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
        "✓ UART{} iniciado | RX GPIO{} <- ESP32 1 TX GPIO{} | {} baudios".format(
            UART_ID,
            UART_RX_PIN,
            UART_TX_PIN,
            UART_BAUDRATE
        )
    )


def validate_uart_payload(data):
    if not isinstance(data, dict):
        return False

    required = (
        "username",
        "dsTemperature",
        "temperature",
        "humidity",
        "doorStatus"
    )

    for key in required:
        if key not in data:
            return False

    if data["doorStatus"] not in ("open", "closed"):
        return False

    username = data.get("username")

    if not isinstance(username, str) or not username:
        return False

    return True


def uart_reader_thread():
    global latest_uart_data
    global last_uart_data_ms
    global uart_data_valid
    global uart_thread_running

    uart_thread_running = True

    print("🔶 Receptor UART iniciado")

    buffer = b""

    try:
        while uart_thread_running:
            try:
                if uart and uart.any():
                    chunk = uart.read()

                    if chunk:
                        buffer += chunk

                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            line = line.strip()

                            if not line:
                                continue

                            try:
                                text = line.decode("utf-8")
                                data = json.loads(text)

                                if validate_uart_payload(data):
                                    with data_lock:
                                        latest_uart_data = data
                                        last_uart_data_ms = time.ticks_ms()
                                        uart_data_valid = True

                                    print(
                                        "📥 UART | {} | PT100: {}°C | "
                                        "T.IN: {}°C | H: {}% | {}".format(
                                            data["username"],
                                            data["dsTemperature"],
                                            data["temperature"],
                                            data["humidity"],
                                            (
                                                "🚪✅"
                                                if data["doorStatus"] == "closed"
                                                else "🚪⚠"
                                            )
                                        )
                                    )

                                else:
                                    print(
                                        "⚠ JSON UART incompleto o inválido:",
                                        data
                                    )

                            except Exception as error:
                                print(
                                    "⚠ Error procesando línea UART:",
                                    repr(error)
                                )

                        # Evita crecimiento ilimitado ante ruido o
                        # mensajes sin salto de línea.
                        if len(buffer) > 4096:
                            print(
                                "⚠ Buffer UART excedido; descartando"
                            )
                            buffer = b""

                # Marca los datos como vencidos.
                if last_uart_data_ms is not None:
                    if (
                        time.ticks_diff(
                            time.ticks_ms(),
                            last_uart_data_ms
                        )
                        > UART_DATA_TIMEOUT_MS
                    ):
                        if uart_data_valid:
                            print(
                                "⚠ Sin datos recientes del ESP32 1 "
                                "durante más de {} s".format(
                                    UART_DATA_TIMEOUT_MS // 1000
                                )
                            )

                        with data_lock:
                            uart_data_valid = False

            except Exception as error:
                print("⚠ Error del receptor UART:", repr(error))

            time.sleep_ms(UART_CHECK_INTERVAL_MS)

    finally:
        uart_thread_running = False
        print("🔶 Receptor UART detenido")


def get_fresh_uart_data():
    with data_lock:
        if (
            not uart_data_valid
            or latest_uart_data is None
            or last_uart_data_ms is None
        ):
            return None

        age_ms = time.ticks_diff(
            time.ticks_ms(),
            last_uart_data_ms
        )

        if age_ms > UART_DATA_TIMEOUT_MS:
            return None

        # Copia independiente para no compartir el diccionario mutable.
        return {
            "username": latest_uart_data.get(
                "username",
                USERNAME
            ),
            "dsTemperature": latest_uart_data.get(
                "dsTemperature"
            ),
            "temperature": latest_uart_data.get(
                "temperature"
            ),
            "humidity": latest_uart_data.get(
                "humidity"
            ),
            "doorStatus": latest_uart_data.get(
                "doorStatus"
            )
        }


# ============================================================
# ESTADO DE RED HACIA ESP32 1
# ============================================================

def get_wifi_signal_bars(rssi):
    """
    Convierte RSSI a 0..4 barras para la pantalla OLED.
    """
    if rssi is None:
        return 0

    try:
        rssi = int(rssi)
    except Exception:
        return 0

    if rssi >= -55:
        return 4
    if rssi >= -65:
        return 3
    if rssi >= -75:
        return 2
    if rssi >= -85:
        return 1

    return 0


def send_network_status():
    if uart is None:
        return False

    rssi = None

    if wlan and wifi_connected:
        try:
            rssi = wlan.status("rssi")
        except Exception:
            rssi = None

    status = {
        "type": "network_status",
        "wifiConnected": bool(
            wlan
            and wifi_connected
            and wlan.isconnected()
        ),
        "rssi": rssi,
        "wifiBars": get_wifi_signal_bars(rssi),
        "websocketConnected": bool(
            ws
            and ws.connected
        )
    }

    try:
        line = json.dumps(status) + "\n"

        with uart_write_lock:
            sent = uart.write(line)

        if sent:
            print(
                "📤 RED | WiFi: {} | RSSI: {} dBm | "
                "Barras: {}/4 | WS: {}".format(
                    "OK" if status["wifiConnected"] else "OFF",
                    status["rssi"],
                    status["wifiBars"],
                    "OK" if status["websocketConnected"] else "--"
                )
            )
            return True

        return False

    except Exception as error:
        print(
            "⚠ Error enviando estado de red por UART:",
            repr(error)
        )
        return False


# ============================================================
# TIEMPO Y WI-FI
# ============================================================

wifi_led = Pin(
    WIFI_LED_PIN,
    Pin.OUT
)

wifi_led.off()


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
        print("⚠ Error NTP:", repr(error))
        return False


def init_wifi():
    global wlan

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Conectando WiFi a {}...".format(SSID))
        wlan.connect(SSID, PASSWORD)

    return wlan


# ============================================================
# WEBSOCKET
# ============================================================

def close_websocket():
    global ws
    global registered_username

    if ws is not None:
        try:
            ws.close()
        except Exception:
            pass

        try:
            ws.connected = False
        except Exception:
            pass

    registered_username = None


def current_node_username():
    data = get_fresh_uart_data()

    if data:
        return data.get("username", USERNAME)

    return USERNAME


def send_username_intro():
    global registered_username

    if not ws or not ws.connected:
        return False

    username = current_node_username()

    try:
        intro = json.dumps({
            "username": username
        })

        if ws.send(intro):
            registered_username = username

            print(
                "✓ Username enviado:",
                username
            )

            del intro
            return True

        print("⚠ No se pudo enviar el username")
        return False

    except Exception as error:
        print("⚠ Error enviando username:", repr(error))
        return False


def connect_websocket_initial():
    global ws

    print("\n🔌 Conexión inicial WebSocket...")

    close_websocket()
    ws = None
    gc.collect()

    ws = WebSocket()

    gc.collect()

    print("Memoria libre antes WS:", gc.mem_free())

    try:
        connect_start = time.ticks_ms()

        if not ws.connect(WEBSOCKET_URL):
            print("❌ WebSocket inicial no conectado")

            close_websocket()
            ws = None
            gc.collect()
            return False

        connect_time = time.ticks_diff(
            time.ticks_ms(),
            connect_start
        )

        print(
            "✓ WebSocket conectado en {} ms".format(
                connect_time
            )
        )

        time.sleep_ms(300)

        if not send_username_intro():
            close_websocket()
            ws = None
            gc.collect()
            return False

        gc.collect()

        print(
            "Memoria libre después WS:",
            gc.mem_free()
        )

        return True

    except Exception as error:
        print(
            "❌ Error conexión inicial WebSocket:",
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

        print("Memoria libre antes WS:", gc.mem_free())

        if not ws.connect(WEBSOCKET_URL):
            close_websocket()
            ws = None
            gc.collect()
            return False

        connect_time = time.ticks_diff(
            time.ticks_ms(),
            connect_start
        )

        print(
            "✓ WebSocket reconectado en {} ms".format(
                connect_time
            )
        )

        time.sleep_ms(300)

        if not send_username_intro():
            close_websocket()
            ws = None
            gc.collect()
            return False

        gc.collect()

        print(
            "Memoria libre después WS:",
            gc.mem_free()
        )

        return True

    except Exception as error:
        print(
            "❌ Error reconectando WebSocket:",
            repr(error)
        )

        close_websocket()
        ws = None
        gc.collect()
        return False


# ============================================================
# GESTOR DE RED
# ============================================================

def network_thread():
    global wifi_connected
    global ws
    global wlan
    global network_thread_running
    global time_synced
    global registered_username

    network_thread_running = True

    print("🔷 Gestor de red iniciado")

    if ws is None:
        ws = WebSocket()

    now = time.ticks_ms()

    last_wifi_check = now
    last_send = now
    last_ping = now
    last_ntp_sync = now
    last_recv = now
    last_ws_attempt = now
    last_network_status = time.ticks_add(
        now,
        -NETWORK_STATUS_INTERVAL_MS
    )

    reconnect_attempts = 0
    ws_reconnect_delay = 30000
    wifi_was_connected = wifi_connected

    try:
        while network_thread_running:
            now = time.ticks_ms()

            # ------------------------------------------------
            # Wi-Fi
            # ------------------------------------------------

            if (
                time.ticks_diff(now, last_wifi_check)
                >= WIFI_CHECK_INTERVAL_MS
            ):
                try:
                    if wlan and wlan.isconnected():
                        if not wifi_connected:
                            wifi_connected = True
                            wifi_led.on()
                            reconnect_attempts = 0

                            print("\n✅ WiFi RECONECTADO")
                            print("   IP:", wlan.ifconfig()[0])

                            try:
                                print(
                                    "   RSSI: {} dBm".format(
                                        wlan.status("rssi")
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
                            print("\n⚠ WiFi DESCONECTADO")

                            wifi_connected = False
                            wifi_led.off()
                            close_websocket()

                        wifi_was_connected = False

                        print("🔄 Reintentando WiFi...")

                        if wlan:
                            try:
                                wlan.disconnect()
                                time.sleep_ms(200)
                            except Exception:
                                pass

                            wlan.connect(SSID, PASSWORD)

                except Exception as error:
                    print(
                        "Error verificando WiFi:",
                        repr(error)
                    )

                last_wifi_check = now

            # ------------------------------------------------
            # NTP
            # ------------------------------------------------

            if (
                wifi_connected
                and time.ticks_diff(now, last_ntp_sync)
                >= NTP_SYNC_INTERVAL_MS
            ):
                sync_time()
                last_ntp_sync = now

            # ------------------------------------------------
            # WebSocket
            # ------------------------------------------------

            if wifi_connected:
                ws_connected = bool(
                    ws and ws.connected
                )

                if not ws_connected:
                    if (
                        time.ticks_diff(now, last_ws_attempt)
                        >= ws_reconnect_delay
                    ):
                        reconnect_attempts += 1
                        last_ws_attempt = now

                        print(
                            "\n🔌 Reconectando WebSocket "
                            "(intento {})...".format(
                                reconnect_attempts
                            )
                        )

                        if recreate_and_connect_websocket():
                            reconnect_attempts = 0
                            ws_reconnect_delay = 30000

                            last_ping = time.ticks_ms()
                            last_send = time.ticks_ms()
                            last_recv = time.ticks_ms()

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
                                    "⏳ Próximo intento en 60 segundos"
                                )
                            else:
                                ws_reconnect_delay = 30000

                # Recepción
                if (
                    ws
                    and ws.connected
                    and time.ticks_diff(now, last_recv)
                    >= WS_RECV_INTERVAL_MS
                ):
                    try:
                        message = ws.recv()

                        if message:
                            print("📥 Servidor:", message)

                            try:
                                parsed = json.loads(message)

                                if parsed.get("type") == "pong":
                                    print("📶 PONG recibido")

                                del parsed
                            except Exception:
                                pass

                    except OSError as error:
                        if (
                            not error.args
                            or error.args[0] != 11
                        ):
                            print(
                                "⚠ Error recv OSError:",
                                repr(error)
                            )

                    except Exception as error:
                        if "timeout" not in str(error).lower():
                            print(
                                "⚠ Error en recv:",
                                repr(error)
                            )

                    last_recv = now

                # Envío
                if (
                    ws
                    and ws.connected
                    and time.ticks_diff(now, last_send)
                    >= SEND_INTERVAL_MS
                ):
                    data = get_fresh_uart_data()

                    if data is None:
                        print(
                            "⏸ WS sin envío: no hay datos UART recientes"
                        )

                    else:
                        try:
                            node_username = data.get(
                                "username",
                                USERNAME
                            )

                            # Si cambia el nodo/username, se registra
                            # antes de enviar el siguiente paquete.
                            if registered_username != node_username:
                                print(
                                    "🔄 Cambio de username detectado:",
                                    node_username
                                )

                                if not send_username_intro():
                                    close_websocket()
                                    last_send = now
                                    time.sleep_ms(100)
                                    continue

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

                            data["datetime"] = datetime_utc

                            json_str = json.dumps(data)

                            if ws.send(json_str):
                                print(
                                    "📤 WS | {} | PT100: {}°C | "
                                    "T.IN: {}°C | H: {}% | {}".format(
                                        data["username"],
                                        data["dsTemperature"],
                                        data["temperature"],
                                        data["humidity"],
                                        (
                                            "🚪✅"
                                            if data["doorStatus"] == "closed"
                                            else "🚪⚠"
                                        )
                                    )
                                )

                            else:
                                print("❌ Error enviando datos")
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

                # Ping
                if (
                    ws
                    and ws.connected
                    and time.ticks_diff(now, last_ping)
                    >= WS_PING_INTERVAL_MS
                ):
                    try:
                        if ws.send('{"type":"ping"}'):
                            print("📶 Ping enviado")
                        else:
                            close_websocket()
                    except Exception:
                        close_websocket()

                    last_ping = now

            else:
                if ws and ws.connected:
                    close_websocket()

                    print(
                        "⚠ WebSocket desconectado por falta de Wi-Fi"
                    )

            # Informa al ESP32 1 sobre Wi-Fi, RSSI y WebSocket.
            if (
                time.ticks_diff(now, last_network_status)
                >= NETWORK_STATUS_INTERVAL_MS
            ):
                send_network_status()
                last_network_status = now

            time.sleep_ms(100)

    except Exception as error:
        print(
            "💥 Error crítico en gestor de red:",
            repr(error)
        )

    finally:
        network_thread_running = False
        close_websocket()
        print("🔷 Gestor de red detenido")


# ============================================================
# MAIN
# ============================================================

def main():
    global wlan
    global wifi_connected
    global network_thread_running
    global uart_thread_running

    print("\n" + "=" * 52)
    print("MHUTEMP PRO - ESP32 2")
    print("UART + WI-FI + WEBSOCKET")
    print("=" * 52)

    print("URL:", WEBSOCKET_URL)
    print("Username de respaldo:", USERNAME)

    print(
        "UART{} -> RX:{} TX:{} | {} baudios".format(
            UART_ID,
            UART_RX_PIN,
            UART_TX_PIN,
            UART_BAUDRATE
        )
    )

    print("=" * 52 + "\n")

    init_uart()

    # Inicia primero el receptor UART para intentar obtener
    # el username real del ESP32 1 antes del WebSocket.
    try:
        _thread.stack_size(6144)
    except Exception:
        pass

    _thread.start_new_thread(
        uart_reader_thread,
        ()
    )

    print(
        "Esperando brevemente datos iniciales del ESP32 1..."
    )

    wait_start = time.ticks_ms()

    while (
        get_fresh_uart_data() is None
        and time.ticks_diff(
            time.ticks_ms(),
            wait_start
        ) < 5000
    ):
        time.sleep_ms(100)

    first_data = get_fresh_uart_data()

    if first_data:
        print(
            "✓ Nodo UART identificado:",
            first_data["username"]
        )
    else:
        print(
            "⚠ No llegó UART inicial; se usará username de respaldo"
        )

    gc.collect()

    # Wi-Fi inicial
    wlan = init_wifi()

    print("Esperando conexión WiFi inicial...")

    timeout = 20

    while not wlan.isconnected() and timeout > 0:
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
            "Memoria antes de conexión WebSocket:",
            gc.mem_free()
        )

        connect_websocket_initial()

    else:
        wifi_connected = False
        wifi_led.off()

        print(
            "⚠ WiFi no conectado inicialmente"
        )

    # Hilo de red
    try:
        _thread.stack_size(8192)

        print(
            "✓ Stack del hilo de red: 8192 bytes"
        )

    except Exception as error:
        print(
            "⚠ No se pudo configurar stack del hilo:",
            repr(error)
        )

    gc.collect()

    print(
        "Memoria antes de iniciar hilo de red:",
        gc.mem_free()
    )

    _thread.start_new_thread(
        network_thread,
        ()
    )

    time.sleep(1)

    print("\n" + "=" * 52)
    print("Sistema iniciado - Presiona Ctrl+C para detener")
    print("🔶 UART: recibe sensores y devuelve estado de red")
    print("🔷 Hilo de red: Wi-Fi + WebSocket")
    print("=" * 52 + "\n")

    last_gc = time.ticks_ms()

    try:
        while True:
            now = time.ticks_ms()

            if (
                time.ticks_diff(now, last_gc)
                >= GC_INTERVAL_MS
            ):
                gc.collect()

                print(
                    "🧹 Memoria libre:",
                    gc.mem_free()
                )

                last_gc = now

            time.sleep_ms(100)

    except KeyboardInterrupt:
        print("\nDeteniendo ESP32 2...")

        uart_thread_running = False
        network_thread_running = False

        time.sleep(1)

        close_websocket()
        wifi_led.off()

        try:
            if uart is not None:
                uart.deinit()
        except Exception:
            pass

        print("ESP32 2 detenido")


if __name__ == "__main__":
    main()
