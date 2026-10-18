# max31865.py
# Driver compacto para MicroPython + ESP32
# Compatible con MAX31865 y PT100/PT1000 de 2, 3 o 4 hilos.

import time
import math


class MAX31865:
    # Registros
    _REG_CONFIG = 0x00
    _REG_RTD_MSB = 0x01
    _REG_FAULT = 0x07

    # Bits de configuración
    _CFG_BIAS = 0x80
    _CFG_1SHOT = 0x20
    _CFG_3WIRE = 0x10
    _CFG_FAULT_CLEAR = 0x02
    _CFG_FILTER_50HZ = 0x01

    def __init__(
        self,
        spi,
        cs,
        wires=3,
        rtd_nominal=100.0,
        ref_resistor=430.0,
        filter_hz=60,
    ):
        if wires not in (2, 3, 4):
            raise ValueError("wires debe ser 2, 3 o 4")
        if filter_hz not in (50, 60):
            raise ValueError("filter_hz debe ser 50 o 60")

        self.spi = spi
        self.cs = cs
        self.cs.init(self.cs.OUT, value=1)
        self.wires = wires
        self.rtd_nominal = float(rtd_nominal)
        self.ref_resistor = float(ref_resistor)
        self.filter_hz = filter_hz

        # Configuración base: 3 hilos si corresponde y filtro de red.
        self._base_config = 0
        if wires == 3:
            self._base_config |= self._CFG_3WIRE
        if filter_hz == 50:
            self._base_config |= self._CFG_FILTER_50HZ

        self.clear_fault()

    def _read_registers(self, register, count):
        tx = bytearray(count + 1)
        rx = bytearray(count + 1)
        tx[0] = register & 0x7F

        self.cs.value(0)
        try:
            self.spi.write_readinto(tx, rx)
        finally:
            self.cs.value(1)

        return rx[1:]

    def _write_register(self, register, value):
        self.cs.value(0)
        try:
            self.spi.write(bytes((register | 0x80, value & 0xFF)))
        finally:
            self.cs.value(1)

    def clear_fault(self):
        self._write_register(
            self._REG_CONFIG,
            self._base_config | self._CFG_FAULT_CLEAR
        )

    def fault(self):
        return self._read_registers(self._REG_FAULT, 1)[0]

    def resistance(self):
        # 1) Habilitar corriente de polarización.
        self._write_register(
            self._REG_CONFIG,
            self._base_config | self._CFG_BIAS
        )
        time.sleep_ms(10)

        # 2) Iniciar una conversión única.
        self._write_register(
            self._REG_CONFIG,
            self._base_config | self._CFG_BIAS | self._CFG_1SHOT
        )
        time.sleep_ms(70)

        raw = self._read_registers(self._REG_RTD_MSB, 2)
        raw_value = (raw[0] << 8) | raw[1]

        # Bit 0 indica fallo; los 15 bits superiores son el código ADC.
        has_fault = raw_value & 0x01
        adc_code = raw_value >> 1

        # Apagar VBIAS para reducir autocalentamiento del PT100.
        self._write_register(self._REG_CONFIG, self._base_config)

        if has_fault:
            fault_code = self.fault()
            self.clear_fault()
            raise OSError("MAX31865 fault 0x{:02X}".format(fault_code))

        if adc_code == 0 or adc_code >= 32767:
            raise OSError("Lectura RTD fuera de rango: {}".format(adc_code))

        return (adc_code * self.ref_resistor) / 32768.0

    def temperature(self):
        resistance = self.resistance()

        # IEC 60751 / Callendar–Van Dusen para platino con alpha=0.00385.
        a = 3.9083e-3
        b = -5.775e-7

        z1 = -a
        z2 = a * a - (4.0 * b)
        z3 = (4.0 * b) / self.rtd_nominal
        z4 = 2.0 * b

        temp = (math.sqrt(z2 + (z3 * resistance)) + z1) / z4

        # Para temperaturas negativas se usa la aproximación polinómica
        # empleada habitualmente por los drivers MAX31865.
        if temp < 0:
            rpoly = (resistance / self.rtd_nominal) * 100.0
            temp = (
                -242.02
                + 2.2228 * rpoly
                + 2.5859e-3 * (rpoly ** 2)
                - 4.8260e-6 * (rpoly ** 3)
                - 2.8183e-8 * (rpoly ** 4)
                + 1.5243e-10 * (rpoly ** 5)
            )

        return temp

