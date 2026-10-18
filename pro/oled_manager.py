# oled_manager.py - MHUTEMP PRO
# Driver modular para OLED SH1106
# Este archivo NO debe importarse antes del primer TLS/WebSocket.

import time
import gc

from machine import Pin, SoftI2C

oled = None
i2c = None
initialized = False


def init(
    sda_pin=21,
    scl_pin=22,
    width=128,
    height=64,
    rotate_180=False
):
    global oled
    global i2c
    global initialized

    if initialized and oled is not None:
        return True

    try:
        # Importación local: el driver se carga solo
        # cuando esta función se ejecuta.
        import sh1106

        gc.collect()

        i2c = SoftI2C(
            sda=Pin(sda_pin),
            scl=Pin(scl_pin),
            freq=400000
        )

        # Escaneo opcional para diagnóstico.
        devices = i2c.scan()

        if not devices:
            print(
                "⚠ OLED: no se detectaron "
                "dispositivos I2C"
            )
        else:
            print(
                "✓ I2C detectado:",
                [
                    "0x{:02X}".format(address)
                    for address in devices
                ]
            )

        # La mayoría de drivers SH1106 aceptan
        # rotate=180. Si tu driver no lo acepta,
        # se usa la variante sin ese argumento.
        try:
            oled = sh1106.SH1106_I2C(
                width,
                height,
                i2c,
                rotate=(
                    180
                    if rotate_180
                    else 0
                )
            )

        except TypeError:
            oled = sh1106.SH1106_I2C(
                width,
                height,
                i2c
            )

            if rotate_180:
                try:
                    oled.rotate(1)
                except Exception:
                    pass

        initialized = True

        oled.fill(0)
        oled.show()

        print("✓ OLED SH1106 inicializado")
        return True

    except Exception as error:
        print(
            "⚠ Error inicializando OLED:",
            repr(error)
        )

        oled = None
        i2c = None
        initialized = False

        gc.collect()
        return False


def _safe_text(text, max_chars=16):
    if text is None:
        return "--"

    text = str(text)

    if len(text) > max_chars:
        return text[:max_chars]

    return text


def _format_temp(value, decimals=1):
    if value is None:
        return "--"

    try:
        format_string = "{:." + str(decimals) + "f}"
        return format_string.format(value)

    except Exception:
        return "--"


def show_status(line1, line2="", line3=""):
    if not initialized or oled is None:
        return False

    try:
        oled.fill(0)

        oled.text(
            _safe_text(line1),
            0,
            0
        )

        oled.text(
            _safe_text(line2),
            0,
            20
        )

        oled.text(
            _safe_text(line3),
            0,
            40
        )

        oled.show()
        return True

    except Exception as error:
        print(
            "⚠ Error mostrando estado OLED:",
            repr(error)
        )
        return False


def update(
    username,
    pt100_temp,
    ambient_temp,
    humidity,
    door_closed,
    wifi_ok,
    websocket_ok,
    timezone_offset=-18000
):
    if not initialized or oled is None:
        return False

    try:
        local_time = time.localtime(
            time.time() + timezone_offset
        )

        line_time = (
            "{:02d}/{:02d} {:02d}:{:02d}"
        ).format(
            local_time[2],
            local_time[1],
            local_time[3],
            local_time[4]
        )

        line_pt100 = "PT100:{}C".format(
            _format_temp(
                pt100_temp,
                2
            )
        )

        line_ambient = "T:{}C H:{}%".format(
            _format_temp(
                ambient_temp,
                1
            ),
            (
                str(int(round(humidity)))
                if humidity is not None
                else "--"
            )
        )

        line_door = (
            "PUERTA:CERRADA"
            if door_closed
            else "PUERTA:ABIERTA"
        )

        line_network = "WF:{} WS:{}".format(
            "OK" if wifi_ok else "NO",
            "OK" if websocket_ok else "NO"
        )

        oled.fill(0)

        # 6 líneas x 10 píxeles = 60 píxeles.
        oled.text(
            _safe_text(username),
            0,
            0
        )

        oled.text(
            line_time,
            0,
            10
        )

        oled.text(
            line_pt100,
            0,
            20
        )

        oled.text(
            line_ambient,
            0,
            30
        )

        oled.text(
            line_door,
            0,
            40
        )

        oled.text(
            line_network,
            0,
            50
        )

        oled.show()
        return True

    except Exception as error:
        print(
            "⚠ Error actualizando OLED:",
            repr(error)
        )
        return False


def deinit():
    global oled
    global i2c
    global initialized

    try:
        if oled is not None:
            try:
                oled.fill(0)
                oled.show()
            except Exception:
                pass

            # Algunos drivers SH1106 incorporan sleep().
            try:
                oled.sleep(True)
            except Exception:
                pass

    finally:
        oled = None
        i2c = None
        initialized = False

        gc.collect()

    return True
