# ESP32 Monitor con WebSocket usando ws_client.py (DUAL CORE)
# Dual driver ss1306 (1.54") and sh1106 (1.3") 12/01/2026
# Codigo del ESP32 del taper

import network
import machine
import time
import dht
import onewire
import ds18x20
import json
import _thread
from machine import Pin, SoftI2C
from ws_client import WebSocket

# ============================================
# CONFIGURACIÓN DE DISPLAY (CAMBIAR AQUÍ)
# ============================================
DISPLAY_TYPE = "SSD1306"  # Opciones: "SSD1306" o "SH1106"
# DISPLAY_TYPE = "SH1106"  # Descomentar para usar SH1106

# Configuración adicional
DISPLAY_ROTATE_180 = False  # True para rotar 180° (útil para SH1106)

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
    print(f"⚠ DISPLAY_TYPE inválido: {DISPLAY_TYPE}. Usar 'SSD1306' o 'SH1106'")

# Configuración
SSID = "motog35"
PASSWORD = "12345678"
WEBSOCKET_URL = "wss://bio-data-pro-production.up.railway.app/"
USERNAME = "MHT26-PRO-01"

# Pines
DHT22_PIN = 4
DS18B20_PIN = 5
WIFI_LED_PIN = 2
MC38_SENSOR_PIN = 15
MC38_LED_PIN = 13
OLED_SCL_PIN = 22
OLED_SDA_PIN = 21

# Zona horaria (Perú UTC-5)
TIMEZONE_OFFSET = -5 * 3600

# Estado del sistema (compartido entre núcleos)
time_synced = False
wifi_connected = False
door_closed = False
wlan = None
ws = None
network_thread_running = False

# Lock para datos compartidos
data_lock = _thread.allocate_lock()

# ============================================
# INICIALIZACIÓN DEL DISPLAY
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
                print("✓ OLED SH1106G inicializado (128x64, rotado 180°)")
            else:
                oled = sh1106.SH1106_I2C(128, 64, i2c)
                print("✓ OLED SH1106G inicializado (128x64)")

        oled_initialized = True
    except Exception as e:
        oled_initialized = False
        print(f"⚠ Error OLED: {e}")

# Inicializar sensores
dht22 = dht.DHT22(Pin(DHT22_PIN))
ds_pin = Pin(DS18B20_PIN)
ds_sensor = ds18x20.DS18X20(onewire.OneWire(ds_pin))
wifi_led = Pin(WIFI_LED_PIN, Pin.OUT)
mc38_sensor = Pin(MC38_SENSOR_PIN, Pin.IN, Pin.PULL_DOWN)
mc38_led = Pin(MC38_LED_PIN, Pin.OUT)

wifi_led.off()
mc38_led.off()

# Datos de sensores
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
    """Convierte RSSI a barras (0-6)"""
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
    """Crea representación ASCII de barras WiFi"""
    barras = ""
    for i in range(6):
        barras += "X" if i < barras_activas else "O"
    return barras

def sync_time():
    """Sincronizar tiempo con NTP"""
    global time_synced
    try:
        import ntptime
        ntptime.settime()
        time_synced = True
        t = time.localtime()
        print(f"✓ Tiempo: {t[2]:02d}/{t[1]:02d}/{t[0]} {t[3]:02d}:{t[4]:02d}")
        return True
    except Exception as e:
        print(f"⚠ Error NTP: {e}")
        return False

def init_wifi():
    """Inicializa WiFi (sin bloquear)"""
    global wifi_connected, wlan
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print(f"Conectando WiFi a {SSID}...")
        wlan.connect(SSID, PASSWORD)

    return wlan

def init_sensors():
    """Inicializa sensores - Primera detección"""
    global ds_devices, door_closed
    print("\nInicializando sensores...")

    # DS18B20 - Primera detección
    try:
        ds_devices = ds_sensor.scan()
        if ds_devices:
            print(f"✓ DS18B20: {len(ds_devices)} sensor(es)")
            for i, dev in enumerate(ds_devices):
                print(f"  Sensor {i}: {dev.hex()}")
        else:
            print("⚠ DS18B20: No encontrado (se seguirá buscando)")
    except Exception as e:
        print(f"⚠ DS18B20: Error al escanear - {e}")
        ds_devices = []

    # DHT22 - Solo informar, se detectará en primera lectura
    print("⏳ DHT22: Se verificará en primera lectura")

    # MC-38
    try:
        door_closed = mc38_sensor.value()
        if door_closed:
            mc38_led.off()
            print("✓ Puerta: CERRADA (LED OFF)")
        else:
            mc38_led.on()
            print("⚠ Puerta: ABIERTA (LED ON)")
    except Exception as e:
        print(f"⚠ MC-38: Error - {e}")
        door_closed = False

def detect_sensors():
    """Detecta sensores conectados dinámicamente"""
    global ds_devices

    # Detectar DS18B20
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
        print(f"Error escaneando DS18B20: {e}")
        ds_devices = []

def read_sensors():
    """Lee sensores (no bloqueante)"""
    # DHT22
    try:
        dht22.measure()
        with data_lock:
            current_data.dht_temp = dht22.temperature()
            current_data.dht_humidity = dht22.humidity()
            current_data.dht_valid = True
    except:
        with data_lock:
            current_data.dht_valid = False

    # DS18B20
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
        # Si no hay dispositivos, marcar como inválido
        with data_lock:
            current_data.ds18b20_valid = False

def check_door():
    """Verifica estado de la puerta"""
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

# ============================================
# FUNCIÓN UNIFICADA PARA ACTUALIZAR OLED
# ============================================
def update_oled():
    """Actualiza pantalla OLED (compatible con SSD1306 y SH1106)"""
    if not oled_initialized:
        return

    try:
        oled.fill(0)

        # Determinar si usar text() o text_small() según driver
        # SSD1306 tiene text_small(), SH1106 solo tiene text()
        use_small_text = (oled_driver == "ssd1306" and hasattr(oled, 'text_small'))

        # Línea 1: Fecha y hora
        if time_synced:
            t = time.localtime(time.time() + TIMEZONE_OFFSET)
            fecha_str = f"{t[2]:02d}/{t[1]:02d}/{t[0]%100:02d} {t[3]:02d}:{t[4]:02d}"
            if use_small_text:
                oled.text_small(fecha_str, 0, 0, 'small')
            else:
                oled.text(fecha_str, 0, 0)
        else:
            if use_small_text:
                oled.text_small("NO SYNC", 0, 0, 'small')
            else:
                oled.text("NO SYNC", 0, 0)

        # Línea 2: ID
        y_pos = 9 if use_small_text else 10
        oled.text(USERNAME, 0, y_pos)

        # Línea 3: Temp OUT
        y_pos = 27 if use_small_text else 20
        with data_lock:
            if current_data.ds18b20_valid:
                temp_str = f"T.OUT:{current_data.ds18b20_temp:.1f}C" if not use_small_text else f"T.OUT: {current_data.ds18b20_temp:.1f}C"
                if use_small_text:
                    oled.text_small(temp_str, 0, y_pos, 'small')
                else:
                    oled.text(temp_str, 0, y_pos)
            else:
                error_str = "T.OUT:ERROR" if not use_small_text else "T.OUT: ERROR"
                if use_small_text:
                    oled.text_small(error_str, 0, y_pos, 'small')
                else:
                    oled.text(error_str, 0, y_pos)

            # Línea 4: Temp IN
            y_pos = 36 if use_small_text else 30
            if current_data.dht_valid:
                temp_str = f"T.IN:{current_data.dht_temp:.1f}C" if not use_small_text else f"T.IN: {current_data.dht_temp:.1f}C"
                if use_small_text:
                    oled.text_small(temp_str, 0, y_pos, 'small')
                else:
                    oled.text(temp_str, 0, y_pos)
            else:
                error_str = "T.IN:ERROR" if not use_small_text else "T.IN: ERROR"
                if use_small_text:
                    oled.text_small(error_str, 0, y_pos, 'small')
                else:
                    oled.text(error_str, 0, y_pos)

            # Línea 5: Humedad
            y_pos = 45 if use_small_text else 40
            if current_data.dht_valid:
                hum_str = f"Hum:{current_data.dht_humidity:.0f}%" if not use_small_text else f"Hum: {current_data.dht_humidity:.0f}%"
                if use_small_text:
                    oled.text_small(hum_str, 0, y_pos, 'small')
                else:
                    oled.text(hum_str, 0, y_pos)
            else:
                error_str = "Hum:ERROR" if not use_small_text else "Hum: ERROR"
                if use_small_text:
                    oled.text_small(error_str, 0, y_pos, 'small')
                else:
                    oled.text(error_str, 0, y_pos)

        # Línea 6: Estado WiFi y WebSocket
        y_pos = 54 if use_small_text else 50
        if wlan and wifi_connected:
            rssi = wlan.status('rssi')
            bars = get_wifi_signal_bars(rssi)
            barras_visual = crear_barras_wifi(bars)
            ws_status = "OK" if (ws and ws.connected) else "--"

            if use_small_text:
                status_str = f"WiFi:{barras_visual} {bars}/6 WS:{ws_status}"
                oled.text_small(status_str, 0, y_pos, 'small')
            else:
                status_str = f"W:{barras_visual} WS:{ws_status}"
                oled.text(status_str, 0, y_pos)
        else:
            if use_small_text:
                oled.text_small("WiFi: DESCONECTADO", 0, y_pos, 'small')
            else:
                oled.text("WiFi:OFF", 0, y_pos)

        oled.show()
    except Exception as e:
        print(f"Error OLED: {e}")

# ============================================
# NÚCLEO 0: Red y WebSocket (bloqueante OK)
# ============================================
def network_thread():
    """Hilo que maneja WiFi y WebSocket en núcleo separado"""
    global wifi_connected, ws, wlan, network_thread_running, time_synced

    network_thread_running = True
    print("🔷 Núcleo de Red iniciado (Core 0)")

    # Crear WebSocket
    ws = WebSocket()

    last_wifi_check = time.ticks_ms()
    last_send = time.ticks_ms()
    last_ping = time.ticks_ms()
    last_ntp_sync = time.ticks_ms()
    last_recv = time.ticks_ms()  # ⬅️ AGREGADO
    reconnect_attempts = 0
    ws_reconnect_delay = 5000
    last_ws_attempt = 0
    wifi_was_connected = False

    try:
        while network_thread_running:
            now = time.ticks_ms()

            # ===== VERIFICAR WIFI ===== (sin cambios)
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
                    print(f"Error verificando WiFi: {e}")

                last_wifi_check = now

            # ===== SINCRONIZAR NTP ===== (sin cambios)
            if wifi_connected and time.ticks_diff(now, last_ntp_sync) >= 3600000:
                sync_time()
                last_ntp_sync = now

            # ===== WEBSOCKET =====
            if wifi_connected:
                # Conectar WebSocket si está desconectado (sin cambios)
                if not ws.connected:
                    if time.ticks_diff(now, last_ws_attempt) >= ws_reconnect_delay:
                        reconnect_attempts += 1
                        print(f"\n🔌 Conectando WebSocket (intento {reconnect_attempts})...")
                        last_ws_attempt = now

                        try:
                            connect_start = time.ticks_ms()

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
                                last_recv = now  # ⬅️ AGREGADO: Resetear recv
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
                            print(f"❌ Error conectando WebSocket: {e}")
                            ws.connected = False

                            try:
                                ws.close()
                            except:
                                pass
                            time.sleep_ms(500)
                            ws = WebSocket()

                # ⬇️⬇️⬇️ NUEVO BLOQUE: RECIBIR MENSAJES ⬇️⬇️⬇️
                if ws.connected and time.ticks_diff(now, last_recv) >= 500:
                    try:
                        msg = ws.recv()
                        if msg:
                            print(f"📥 Servidor: {msg}")
                            try:
                                parsed = json.loads(msg)
                                if parsed.get('type') == 'pong':
                                    print("📶 PONG recibido del servidor")
                            except:
                                pass
                    except OSError as e:
                        if e.args[0] != 11:  # 11 = EAGAIN
                            print(f"⚠️ Error recv OSError: {e}")
                    except Exception as e:
                        if "timeout" not in str(e).lower():
                            print(f"⚠️ Error en recv: {e}")

                    last_recv = now
                # ⬆️⬆️⬆️ FIN NUEVO BLOQUE ⬆️⬆️⬆️

                # Enviar datos cada 2s (sin cambios)
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
                            ds_temp_str = f"{data['dsTemperature']}°C" if data['dsTemperature'] is not None else "ERROR"
                            dht_temp_str = f"{data['temperature']}°C" if data['temperature'] is not None else "ERROR"
                            humidity_str = f"{data['humidity']}%" if data['humidity'] is not None else "ERROR"
                            door_icon = "🚪✅" if door_closed else "🚪⚠️"

                            print(f"📤 WS | T.OUT: {ds_temp_str} | T.IN: {dht_temp_str} | H: {humidity_str} | {door_icon}")
                        else:
                            print("❌ Error enviando datos")
                            ws.connected = False

                    except Exception as e:
                        print(f"❌ Error en envío: {e}")
                        ws.connected = False

                    last_send = now

                # Ping cada 30s (sin cambios)
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
        print(f"💥 Error crítico en núcleo de red: {e}")
    finally:
        network_thread_running = False
        print("🔷 Núcleo de Red detenido")

# ============================================
# NÚCLEO 1: Sensores y Display (Main)
# ============================================
def main():
    global wlan

    print("\n" + "="*50)
    print("ESP32 Monitor DUAL CORE con WebSocket SSL")
    print("="*50)
    print(f"URL: {WEBSOCKET_URL}")
    print(f"Username: {USERNAME}")
    print(f"DHT22: GPIO{DHT22_PIN}")
    print(f"DS18B20: GPIO{DS18B20_PIN}")
    print(f"MC-38: GPIO{MC38_SENSOR_PIN} (LED: GPIO{MC38_LED_PIN})")
    print("="*50 + "\n")

    # Inicializar sensores
    init_sensors()

    # Inicializar WiFi
    wlan = init_wifi()

    # Esperar conexión inicial WiFi (máximo 15s)
    print("Esperando conexión WiFi inicial...")
    timeout = 15
    while not wlan.isconnected() and timeout > 0:
        time.sleep(1)
        timeout -= 1

    if wlan.isconnected():
        global wifi_connected
        wifi_connected = True
        print(f"✓ WiFi conectado: {wlan.ifconfig()[0]}")
        wifi_led.on()
        sync_time()
    else:
        print("⚠ WiFi no conectado inicialmente (se reintentará)")

    # Iniciar hilo de red en Core 0
    print("\n🚀 Iniciando núcleo de red...")
    _thread.start_new_thread(network_thread, ())
    time.sleep(1)

    print("\n" + "="*50)
    print("Sistema iniciado - Presiona Ctrl+C para detener")
    print("🔶 Core 1: Sensores y Display")
    print("🔷 Core 0: WiFi y WebSocket")
    print("="*50 + "\n")

    # Timers para Core 1
    last_sensor = time.ticks_ms()
    last_door = time.ticks_ms()
    last_oled = time.ticks_ms()
    last_detect = time.ticks_ms()

    try:
        while True:
            now = time.ticks_ms()

            # Detectar sensores cada 5 segundos
            if time.ticks_diff(now, last_detect) >= 5000:
                detect_sensors()
                last_detect = now

            # Leer sensores cada 2s
            if time.ticks_diff(now, last_sensor) >= 2000:
                read_sensors()
                last_sensor = now

            # Puerta cada 100ms
            if time.ticks_diff(now, last_door) >= 100:
                check_door()
                last_door = now

            # OLED cada 1s
            if time.ticks_diff(now, last_oled) >= 1000:
                update_oled()
                last_oled = now

            time.sleep_ms(10)

    except KeyboardInterrupt:
        print("\n\n" + "="*50)
        print("Deteniendo sistema...")
        print("="*50)

        # Detener hilo de red
        global network_thread_running
        network_thread_running = False
        time.sleep(1)

        # Limpiar
        if ws:
            ws.close()
        wifi_led.off()
        mc38_led.off()

        if oled_initialized:
            oled.fill(0)
            oled.text_small("Sistema", 0, 20, 'small')
            oled.text_small("Detenido", 0, 32, 'small')
            oled.show()
            time.sleep(1)

        print("Sistema detenido")

if __name__ == "__main__":
    main()

