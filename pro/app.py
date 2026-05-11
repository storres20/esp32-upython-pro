# app.py - MHUTEMP PRO
# Código principal basado en tu boot.py original funcional

import network
import machine
import time
import dht
import onewire
import ds18x20
import json
import _thread
import gc
from machine import Pin, SoftI2C
from ws_client import WebSocket

# ============================================
# CONFIGURACIÓN DESDE config.json
# ============================================

CONFIG_FILE = "config.json"

SSID = "motog35"
PASSWORD = "12345678"
WEBSOCKET_URL = "wss://bio-data-pro-production.up.railway.app/"
USERNAME = "MHT26-PRO-01"
DISPLAY_TYPE = "SSD1306"
DISPLAY_ROTATE_180 = False
SEND_INTERVAL_MS = 2000

def load_config():
    global SSID, PASSWORD, WEBSOCKET_URL, USERNAME
    global DISPLAY_TYPE, DISPLAY_ROTATE_180, SEND_INTERVAL_MS

    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)

        SSID = cfg.get("ssid", SSID)
        PASSWORD = cfg.get("password", PASSWORD)
        WEBSOCKET_URL = cfg.get("websocket_url", WEBSOCKET_URL)
        USERNAME = cfg.get("username", USERNAME)
        DISPLAY_TYPE = cfg.get("display_type", DISPLAY_TYPE)
        DISPLAY_ROTATE_180 = cfg.get("display_rotate_180", DISPLAY_ROTATE_180)
        SEND_INTERVAL_MS = cfg.get("send_interval_ms", SEND_INTERVAL_MS)

        del cfg
        gc.collect()

        print("✅ config.json cargado")
        print("Memoria libre después config:", gc.mem_free())

    except Exception as e:
        print("⚠ Error cargando config.json:", e)
        print("Usando configuración por defecto")

load_config()


# ============================================
# IMPORTACIÓN DINÁMICA DE LIBRERÍAS
# ============================================
OLED_AVAILABLE = False
oled_driver = None

if DISPLAY_TYPE == "SSD1306":
    try:
        import ssd1306
        OLED_AVAILABLE = True
        oled_driver = "ssd1306"
        print("✓ Librería ssd1306 encontrada")
    except ImportError:
        print("⚠ Librería ssd1306 no encontrada")
elif DISPLAY_TYPE == "SH1106":
    try:
        import sh1106
        OLED_AVAILABLE = True
        oled_driver = "sh1106"
        print("✓ Librería sh1106 encontrada (SH1106G)")
    except ImportError:
        print("⚠ Librería sh1106 no encontrada")
else:
    print("⚠ DISPLAY_TYPE inválido")



# Pines
DHT22_PIN = 4
DS18B20_PIN = 5
WIFI_LED_PIN = 2
MC38_SENSOR_PIN = 15
MC38_LED_PIN = 13
OLED_SCL_PIN = 22
OLED_SDA_PIN = 21

TIMEZONE_OFFSET = -5 * 3600

time_synced = False
wifi_connected = False
door_closed = False
wlan = None
ws = None
network_thread_running = False

data_lock = _thread.allocate_lock()

# ============================================
# DISPLAY
# ============================================
oled_initialized = False
oled = None

if OLED_AVAILABLE:
    try:
        i2c = SoftI2C(scl=Pin(OLED_SCL_PIN), sda=Pin(OLED_SDA_PIN))

        if oled_driver == "ssd1306":
            oled = ssd1306.SSD1306_I2C(128, 64, i2c)
            print("✓ OLED SSD1306 inicializado (128x64)")
        elif oled_driver == "sh1106":
            if DISPLAY_ROTATE_180:
                oled = sh1106.SH1106_I2C(128, 64, i2c, rotate=180)
            else:
                oled = sh1106.SH1106_I2C(128, 64, i2c)

            print("✓ OLED SH1106G inicializado (128x64)")

        oled_initialized = True
    except Exception as e:
        oled_initialized = False
        print("⚠ Error OLED:", e)

# ============================================
# SENSORES
# ============================================
dht22 = dht.DHT22(Pin(DHT22_PIN))
ds_pin = Pin(DS18B20_PIN)
ds_sensor = ds18x20.DS18X20(onewire.OneWire(ds_pin))

wifi_led = Pin(WIFI_LED_PIN, Pin.OUT)
mc38_sensor = Pin(MC38_SENSOR_PIN, Pin.IN, Pin.PULL_DOWN)
mc38_led = Pin(MC38_LED_PIN, Pin.OUT)

wifi_led.off()
mc38_led.off()


class SensorData:
    def __init__(self):
        self.dht_temp = 0.0
        self.dht_humidity = 0.0
        self.ds18b20_temp = 0.0
        self.dht_valid = False
        self.ds18b20_valid = False


current_data = SensorData()
ds_devices = []


def get_wifi_signal_bars(rssi):
    if rssi >= -50:
        return 6
    elif rssi >= -60:
        return 5
    elif rssi >= -70:
        return 4
    elif rssi >= -80:
        return 3
    elif rssi >= -90:
        return 2
    else:
        return 1


def crear_barras_wifi(barras_activas):
    barras = ""
    for i in range(6):
        barras += "X" if i < barras_activas else "O"
    return barras


def sync_time():
    global time_synced

    try:
        import ntptime
        ntptime.settime()
        time_synced = True
        t = time.localtime()
        print(f"✓ Tiempo: {t[2]:02d}/{t[1]:02d}/{t[0]} {t[3]:02d}:{t[4]:02d}")
        return True
    except Exception as e:
        print("⚠ Error NTP:", e)
        return False


def init_wifi():
    global wifi_connected, wlan

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print(f"Conectando WiFi a {SSID}...")
        wlan.connect(SSID, PASSWORD)

    return wlan


def init_sensors():
    global ds_devices, door_closed

    print("\nInicializando sensores...")

    try:
        ds_devices = ds_sensor.scan()
        if ds_devices:
            print(f"✓ DS18B20: {len(ds_devices)} sensor(es)")
            for i, dev in enumerate(ds_devices):
                print(f"  Sensor {i}: {dev.hex()}")
        else:
            print("⚠ DS18B20: No encontrado")
    except Exception as e:
        print("⚠ DS18B20: Error al escanear:", e)
        ds_devices = []

    print("⏳ DHT22: Se verificará en primera lectura")

    try:
        door_closed = mc38_sensor.value()
        if door_closed:
            mc38_led.off()
            print("✓ Puerta: CERRADA (LED OFF)")
        else:
            mc38_led.on()
            print("⚠ Puerta: ABIERTA (LED ON)")
    except Exception as e:
        print("⚠ MC-38: Error:", e)
        door_closed = False


def detect_sensors():
    global ds_devices

    try:
        devices = ds_sensor.scan()

        if devices != ds_devices:
            ds_devices = devices

            if ds_devices:
                print(f"🔍 DS18B20 detectado: {len(ds_devices)} sensor(es)")
                for i, dev in enumerate(ds_devices):
                    print(f"  Sensor {i}: {dev.hex()}")
            else:
                print("⚠ DS18B20 desconectado")

    except Exception as e:
        print("Error escaneando DS18B20:", e)
        ds_devices = []


def read_sensors():
    try:
        dht22.measure()
        with data_lock:
            current_data.dht_temp = dht22.temperature()
            current_data.dht_humidity = dht22.humidity()
            current_data.dht_valid = True
    except:
        with data_lock:
            current_data.dht_valid = False

    if ds_devices:
        try:
            ds_sensor.convert_temp()
            time.sleep_ms(750)
            temp = ds_sensor.read_temp(ds_devices[0])

            if temp and temp != -127.0:
                with data_lock:
                    current_data.ds18b20_temp = temp
                    current_data.ds18b20_valid = True
            else:
                with data_lock:
                    current_data.ds18b20_valid = False
        except:
            with data_lock:
                current_data.ds18b20_valid = False
    else:
        with data_lock:
            current_data.ds18b20_valid = False


def check_door():
    global door_closed

    state = mc38_sensor.value()

    if state != door_closed:
        door_closed = state

        if door_closed:
            mc38_led.off()
            print("\n🚪 PUERTA CERRADA - LED OFF")
        else:
            mc38_led.on()
            print("\n⚠️  PUERTA ABIERTA - LED ON")


def update_oled():
    if not oled_initialized:
        return

    try:
        oled.fill(0)

        use_small_text = (
            oled_driver == "ssd1306"
            and hasattr(oled, "text_small")
        )

        if time_synced:
            t = time.localtime(time.time() + TIMEZONE_OFFSET)
            fecha_str = f"{t[2]:02d}/{t[1]:02d}/{t[0]%100:02d} {t[3]:02d}:{t[4]:02d}"
        else:
            fecha_str = "NO SYNC"

        if use_small_text:
            oled.text_small(fecha_str, 0, 0, "small")
        else:
            oled.text(fecha_str, 0, 0)

        y_pos = 9 if use_small_text else 10
        oled.text(USERNAME, 0, y_pos)

        with data_lock:
            y_pos = 27 if use_small_text else 20
            if current_data.ds18b20_valid:
                temp_str = f"T.OUT:{current_data.ds18b20_temp:.1f}C"
            else:
                temp_str = "T.OUT:ERROR"

            if use_small_text:
                oled.text_small(temp_str, 0, y_pos, "small")
            else:
                oled.text(temp_str, 0, y_pos)

            y_pos = 36 if use_small_text else 30
            if current_data.dht_valid:
                temp_str = f"T.IN:{current_data.dht_temp:.1f}C"
            else:
                temp_str = "T.IN:ERROR"

            if use_small_text:
                oled.text_small(temp_str, 0, y_pos, "small")
            else:
                oled.text(temp_str, 0, y_pos)

            y_pos = 45 if use_small_text else 40
            if current_data.dht_valid:
                hum_str = f"Hum:{current_data.dht_humidity:.0f}%"
            else:
                hum_str = "Hum:ERROR"

            if use_small_text:
                oled.text_small(hum_str, 0, y_pos, "small")
            else:
                oled.text(hum_str, 0, y_pos)

        y_pos = 54 if use_small_text else 50

        if wlan and wifi_connected:
            rssi = wlan.status("rssi")
            bars = get_wifi_signal_bars(rssi)
            barras_visual = crear_barras_wifi(bars)
            ws_status = "OK" if (ws and ws.connected) else "--"

            if use_small_text:
                status_str = f"WiFi:{barras_visual} {bars}/6 WS:{ws_status}"
                oled.text_small(status_str, 0, y_pos, "small")
            else:
                status_str = f"W:{barras_visual} WS:{ws_status}"
                oled.text(status_str, 0, y_pos)
        else:
            if use_small_text:
                oled.text_small("WiFi: DESCONECTADO", 0, y_pos, "small")
            else:
                oled.text("WiFi:OFF", 0, y_pos)

        oled.show()

    except Exception as e:
        print("Error OLED:", e)


def network_thread():
    global wifi_connected, ws, wlan, network_thread_running, time_synced

    network_thread_running = True
    print("🔷 Núcleo de Red iniciado (Core 0)")

    ws = WebSocket()

    last_wifi_check = time.ticks_ms()
    last_send = time.ticks_ms()
    last_ping = time.ticks_ms()
    last_ntp_sync = time.ticks_ms()
    last_recv = time.ticks_ms()

    reconnect_attempts = 0
    ws_reconnect_delay = 5000
    last_ws_attempt = 0
    wifi_was_connected = False

    try:
        while network_thread_running:
            now = time.ticks_ms()

            if time.ticks_diff(now, last_wifi_check) >= 5000:
                try:
                    if wlan and wlan.isconnected():
                        if not wifi_connected:
                            print("\n✅ WiFi RECONECTADO")
                            print(f"   IP: {wlan.ifconfig()[0]}")
                            print(f"   RSSI: {wlan.status('rssi')} dBm")
                            wifi_connected = True
                            wifi_led.on()
                            reconnect_attempts = 0

                            if wifi_was_connected:
                                print("🔄 Recreando WebSocket después de reconexión WiFi...")
                                try:
                                    ws.close()
                                except:
                                    pass
                                time.sleep_ms(500)
                                ws = WebSocket()
                                print("✓ WebSocket recreado")

                            if not time_synced:
                                sync_time()

                        wifi_was_connected = True

                    else:
                        if wifi_connected:
                            print("\n⚠️  WiFi DESCONECTADO")
                            wifi_connected = False
                            wifi_was_connected = False
                            wifi_led.off()

                            if ws:
                                try:
                                    ws.close()
                                except:
                                    pass
                                ws.connected = False

                        print("🔄 Reintentando WiFi...")

                        if wlan:
                            try:
                                wlan.disconnect()
                                time.sleep_ms(200)
                            except:
                                pass

                            wlan.connect(SSID, PASSWORD)

                except Exception as e:
                    print("Error verificando WiFi:", e)

                last_wifi_check = now

            if wifi_connected and time.ticks_diff(now, last_ntp_sync) >= 3600000:
                sync_time()
                last_ntp_sync = now

            if wifi_connected:
                if not ws.connected:
                    if time.ticks_diff(now, last_ws_attempt) >= ws_reconnect_delay:
                        reconnect_attempts += 1
                        print(f"\n🔌 Conectando WebSocket (intento {reconnect_attempts})...")
                        last_ws_attempt = now

                        try:
                            connect_start = time.ticks_ms()

                            gc.collect()
                            print("Memoria libre antes WS:", gc.mem_free())

                            if ws.connect(WEBSOCKET_URL):
                                connect_time = time.ticks_diff(time.ticks_ms(), connect_start)
                                print(f"✓ WebSocket conectado en {connect_time}ms")

                                time.sleep_ms(500)
                                intro = json.dumps({"username": USERNAME})
                                ws.send(intro)
                                print(f"✓ Username enviado: {USERNAME}")

                                reconnect_attempts = 0
                                ws_reconnect_delay = 5000
                                last_ping = now
                                last_send = now
                                last_recv = now
                            else:
                                print(f"❌ WebSocket no conectado (intento {reconnect_attempts})")

                                if reconnect_attempts >= 5:
                                    ws_reconnect_delay = 60000
                                    print("⏳ Esperando 60s antes del próximo intento...")
                                elif reconnect_attempts >= 3:
                                    ws_reconnect_delay = 30000
                                    print("⏳ Esperando 30s antes del próximo intento...")
                                else:
                                    ws_reconnect_delay = 5000

                                if reconnect_attempts >= 3:
                                    print("🔄 Recreando WebSocket por múltiples fallos...")
                                    try:
                                        ws.close()
                                    except:
                                        pass
                                    time.sleep_ms(500)
                                    ws = WebSocket()

                        except Exception as e:
                            print("❌ Error conectando WebSocket:", e)
                            ws.connected = False

                            try:
                                ws.close()
                            except:
                                pass

                            time.sleep_ms(500)
                            ws = WebSocket()

                if ws.connected and time.ticks_diff(now, last_recv) >= 500:
                    try:
                        msg = ws.recv()
                        if msg:
                            print("📥 Servidor:", msg)

                            try:
                                parsed = json.loads(msg)
                                if parsed.get("type") == "pong":
                                    print("📶 PONG recibido del servidor")
                            except:
                                pass

                    except OSError as e:
                        if e.args[0] != 11:
                            print("⚠️ Error recv OSError:", e)

                    except Exception as e:
                        if "timeout" not in str(e).lower():
                            print("⚠️ Error en recv:", e)

                    last_recv = now

                if ws.connected and time.ticks_diff(now, last_send) >= 2000:
                    try:
                        t = time.gmtime()
                        datetime_utc = f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}T{t[3]:02d}:{t[4]:02d}:{t[5]:02d}Z"

                        with data_lock:
                            data = {
                                "username": USERNAME,
                                "dsTemperature": round(current_data.ds18b20_temp, 1) if current_data.ds18b20_valid else None,
                                "temperature": round(current_data.dht_temp, 1) if current_data.dht_valid else None,
                                "humidity": int(round(current_data.dht_humidity, 0)) if current_data.dht_valid else None,
                                "datetime": datetime_utc,
                                "doorStatus": "closed" if door_closed else "open"
                            }

                        json_str = json.dumps(data)

                        if ws.send(json_str):
                            print(
                                "📤 WS | T.OUT: {}°C | T.IN: {}°C | H: {}% | {}".format(
                                    data["dsTemperature"],
                                    data["temperature"],
                                    data["humidity"],
                                    "🚪✅" if door_closed else "🚪⚠️"
                                )
                            )
                        else:
                            print("❌ Error enviando datos")
                            ws.connected = False

                    except Exception as e:
                        print("❌ Error en envío:", e)
                        ws.connected = False

                    last_send = now

                if ws.connected and time.ticks_diff(now, last_ping) >= 30000:
                    try:
                        ws.send('{"type":"ping"}')
                        print("📶 Ping enviado")
                    except:
                        ws.connected = False

                    last_ping = now

            else:
                if ws and ws.connected:
                    ws.connected = False
                    print("⚠️ WebSocket marcado como desconectado (sin WiFi)")

            time.sleep_ms(100)

    except Exception as e:
        print("💥 Error crítico en núcleo de red:", e)

    finally:
        network_thread_running = False
        print("🔷 Núcleo de Red detenido")


def main():
    global wlan, wifi_connected

    print("\n" + "=" * 50)
    print("ESP32 Monitor DUAL CORE con WebSocket SSL")
    print("=" * 50)
    print(f"URL: {WEBSOCKET_URL}")
    print(f"Username: {USERNAME}")
    print(f"DHT22: GPIO{DHT22_PIN}")
    print(f"DS18B20: GPIO{DS18B20_PIN}")
    print(f"MC-38: GPIO{MC38_SENSOR_PIN} (LED: GPIO{MC38_LED_PIN})")
    print("=" * 50 + "\n")

    init_sensors()

    wlan = init_wifi()

    print("Esperando conexión WiFi inicial...")
    timeout = 15

    while not wlan.isconnected() and timeout > 0:
        time.sleep(1)
        timeout -= 1

    if wlan.isconnected():
        wifi_connected = True
        print(f"✓ WiFi conectado: {wlan.ifconfig()[0]}")
        wifi_led.on()
        sync_time()
    else:
        print("⚠ WiFi no conectado inicialmente (se reintentará)")

    print("\n🚀 Iniciando núcleo de red...")
    _thread.start_new_thread(network_thread, ())
    time.sleep(1)

    print("\n" + "=" * 50)
    print("Sistema iniciado - Presiona Ctrl+C para detener")
    print("🔶 Core 1: Sensores y Display")
    print("🔷 Core 0: WiFi y WebSocket")
    print("=" * 50 + "\n")

    last_sensor = time.ticks_ms()
    last_door = time.ticks_ms()
    last_oled = time.ticks_ms()
    last_detect = time.ticks_ms()

    try:
        while True:
            now = time.ticks_ms()

            if time.ticks_diff(now, last_detect) >= 5000:
                detect_sensors()
                last_detect = now

            if time.ticks_diff(now, last_sensor) >= 2000:
                read_sensors()
                last_sensor = now

            if time.ticks_diff(now, last_door) >= 100:
                check_door()
                last_door = now

            if time.ticks_diff(now, last_oled) >= 1000:
                update_oled()
                last_oled = now

            time.sleep_ms(10)

    except KeyboardInterrupt:
        print("\n\n" + "=" * 50)
        print("Deteniendo sistema...")
        print("=" * 50)

        global network_thread_running
        network_thread_running = False
        time.sleep(1)

        if ws:
            ws.close()

        wifi_led.off()
        mc38_led.off()

        if oled_initialized:
            oled.fill(0)

            if hasattr(oled, "text_small"):
                oled.text_small("Sistema", 0, 20, "small")
                oled.text_small("Detenido", 0, 32, "small")
            else:
                oled.text("Sistema", 0, 20)
                oled.text("Detenido", 0, 32)

            oled.show()
            time.sleep(1)

        print("Sistema detenido")


if __name__ == "__main__":
    main()
