# MicroPython SSD1306 OLED driver mejorado con fuentes múltiples
from micropython import const
import framebuf

# register definitions
SET_CONTRAST = const(0x81)
SET_ENTIRE_ON = const(0xA4)
SET_NORM_INV = const(0xA6)
SET_DISP = const(0xAE)
SET_MEM_ADDR = const(0x20)
SET_COL_ADDR = const(0x21)
SET_PAGE_ADDR = const(0x22)
SET_DISP_START_LINE = const(0x40)
SET_SEG_REMAP = const(0xA0)
SET_MUX_RATIO = const(0xA8)
SET_COM_OUT_DIR = const(0xC0)
SET_DISP_OFFSET = const(0xD3)
SET_COM_PIN_CFG = const(0xDA)
SET_DISP_CLK_DIV = const(0xD5)
SET_PRECHARGE = const(0xD9)
SET_VCOM_DESEL = const(0xDB)
SET_CHARGE_PUMP = const(0x8D)

# Fuente pequeña 5x7 píxeles - Solo caracteres esenciales para tu proyecto
SMALL_FONT_5x7 = {
    '0': [0x3E, 0x51, 0x49, 0x45, 0x3E],
    '1': [0x00, 0x42, 0x7F, 0x40, 0x00],
    '2': [0x42, 0x61, 0x51, 0x49, 0x46],
    '3': [0x21, 0x41, 0x45, 0x4B, 0x31],
    '4': [0x18, 0x14, 0x12, 0x7F, 0x10],
    '5': [0x27, 0x45, 0x45, 0x45, 0x39],
    '6': [0x3C, 0x4A, 0x49, 0x49, 0x30],
    '7': [0x01, 0x71, 0x09, 0x05, 0x03],
    '8': [0x36, 0x49, 0x49, 0x49, 0x36],
    '9': [0x06, 0x49, 0x49, 0x29, 0x1E],
    ':': [0x00, 0x36, 0x36, 0x00, 0x00],
    '/': [0x20, 0x10, 0x08, 0x04, 0x02],
    '-': [0x08, 0x08, 0x08, 0x08, 0x08],
    '.': [0x00, 0x60, 0x60, 0x00, 0x00],
    ' ': [0x00, 0x00, 0x00, 0x00, 0x00],
    'A': [0x7E, 0x11, 0x11, 0x11, 0x7E],
    'B': [0x7F, 0x49, 0x49, 0x49, 0x36],
    'C': [0x3E, 0x41, 0x41, 0x41, 0x22],
    'D': [0x7F, 0x41, 0x41, 0x22, 0x1C],
    'E': [0x7F, 0x49, 0x49, 0x49, 0x41],
    'F': [0x7F, 0x09, 0x09, 0x09, 0x01],
    'G': [0x3E, 0x41, 0x49, 0x49, 0x7A],
    'H': [0x7F, 0x08, 0x08, 0x08, 0x7F],
    'I': [0x00, 0x41, 0x7F, 0x41, 0x00],
    'J': [0x20, 0x40, 0x41, 0x3F, 0x01],
    'K': [0x7F, 0x08, 0x14, 0x22, 0x41],
    'L': [0x7F, 0x40, 0x40, 0x40, 0x40],
    'M': [0x7F, 0x02, 0x0C, 0x02, 0x7F],
    'N': [0x7F, 0x04, 0x08, 0x10, 0x7F],
    'O': [0x3E, 0x41, 0x41, 0x41, 0x3E],
    'P': [0x7F, 0x09, 0x09, 0x09, 0x06],
    'Q': [0x3E, 0x41, 0x51, 0x21, 0x5E],
    'R': [0x7F, 0x09, 0x19, 0x29, 0x46],
    'S': [0x46, 0x49, 0x49, 0x49, 0x31],
    'T': [0x01, 0x01, 0x7F, 0x01, 0x01],
    'U': [0x3F, 0x40, 0x40, 0x40, 0x3F],
    'V': [0x1F, 0x20, 0x40, 0x20, 0x1F],
    'W': [0x3F, 0x40, 0x38, 0x40, 0x3F],
    'X': [0x63, 0x14, 0x08, 0x14, 0x63],
    'Y': [0x07, 0x08, 0x70, 0x08, 0x07],
    'Z': [0x61, 0x51, 0x49, 0x45, 0x43],
    '%': [0x46, 0x26, 0x10, 0x08, 0x32],
}

# Fuente muy pequeña 4x6 píxeles - Solo números
TINY_FONT_4x6 = {
    '0': [0x3E, 0x41, 0x41, 0x3E],
    '1': [0x42, 0x7F, 0x40, 0x00],
    '2': [0x62, 0x51, 0x49, 0x46],
    '3': [0x22, 0x49, 0x49, 0x36],
    '4': [0x18, 0x14, 0x7F, 0x10],
    '5': [0x27, 0x45, 0x45, 0x39],
    '6': [0x3E, 0x49, 0x49, 0x30],
    '7': [0x01, 0x71, 0x09, 0x07],
    '8': [0x36, 0x49, 0x49, 0x36],
    '9': [0x06, 0x49, 0x49, 0x3E],
    '.': [0x60, 0x60, 0x00, 0x00],
    ':': [0x36, 0x36, 0x00, 0x00],
    'C': [0x3E, 0x41, 0x41, 0x22],
    '%': [0x46, 0x26, 0x32, 0x31],
    ' ': [0x00, 0x00, 0x00, 0x00],
    '-': [0x08, 0x08, 0x08, 0x08],
}

class SSD1306(framebuf.FrameBuffer):
    def __init__(self, width, height, external_vcc):
        self.width = width
        self.height = height
        self.external_vcc = external_vcc
        self.pages = self.height // 8
        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self.init_display()

    def init_display(self):
        for cmd in (
            SET_DISP | 0x00,  # off
            SET_MEM_ADDR, 0x00,  # horizontal
            SET_DISP_START_LINE | 0x00,
            SET_SEG_REMAP | 0x01,  # column addr 127 mapped to SEG0
            SET_MUX_RATIO, self.height - 1,
            SET_COM_OUT_DIR | 0x08,  # scan from COM[N] to COM0
            SET_DISP_OFFSET, 0x00,
            SET_COM_PIN_CFG, 0x02 if self.width > 2 * self.height else 0x12,
            SET_DISP_CLK_DIV, 0x80,
            SET_PRECHARGE, 0x22 if self.external_vcc else 0xF1,
            SET_VCOM_DESEL, 0x30,  # 0.83*Vcc
            SET_CONTRAST, 0xFF,  # maximum
            SET_ENTIRE_ON,  # output follows RAM contents
            SET_NORM_INV,  # not inverted
            SET_CHARGE_PUMP, 0x10 if self.external_vcc else 0x14,
            SET_DISP | 0x01):  # on
            self.write_cmd(cmd)
        self.fill(0)
        self.show()

    def poweroff(self):
        self.write_cmd(SET_DISP | 0x00)

    def poweron(self):
        self.write_cmd(SET_DISP | 0x01)

    def contrast(self, contrast):
        self.write_cmd(SET_CONTRAST)
        self.write_cmd(contrast)

    def invert(self, invert):
        self.write_cmd(SET_NORM_INV | (invert & 1))

    def show(self):
        x0 = 0
        x1 = self.width - 1
        if self.width == 64:
            x0 += 32
            x1 += 32
        self.write_cmd(SET_COL_ADDR)
        self.write_cmd(x0)
        self.write_cmd(x1)
        self.write_cmd(SET_PAGE_ADDR)
        self.write_cmd(0)
        self.write_cmd(self.pages - 1)
        self.write_data(self.buffer)

    # ============ NUEVAS FUNCIONES PARA FUENTES PEQUEÑAS ============

    def draw_char_small(self, char, x, y, font_size='small'):
        """Dibuja un carácter con fuente pequeña
        font_size: 'small' (5x7) o 'tiny' (4x6)
        """
        if font_size == 'small':
            font = SMALL_FONT_5x7
            width = 5
            height = 7
        elif font_size == 'tiny':
            font = TINY_FONT_4x6
            width = 4
            height = 6
        else:
            return x  # Retorna x sin cambios si font_size no es válido

        if char.upper() in font:
            bitmap = font[char.upper()]
            for col in range(width):
                if col < len(bitmap):
                    byte = bitmap[col]
                    for row in range(height):
                        if byte & (1 << row):
                            self.pixel(x + col, y + row, 1)

        return x + width + 1  # Retorna siguiente posición x

    def text_small(self, text, x, y, font_size='small'):
        """Dibuja texto con fuente pequeña
        font_size: 'small' (5x7) o 'tiny' (4x6)
        """
        current_x = x
        for char in str(text):
            current_x = self.draw_char_small(char, current_x, y, font_size)
        return current_x

    def text_auto(self, text, x, y, max_width=None):
        """Selecciona automáticamente el mejor tamaño de fuente"""
        text_str = str(text)

        # Si no se especifica ancho máximo, usar el ancho de la pantalla
        if max_width is None:
            max_width = self.width - x

        # Calcular ancho estimado con diferentes fuentes
        tiny_width = len(text_str) * 5  # 4 + 1 espacio
        small_width = len(text_str) * 6  # 5 + 1 espacio
        normal_width = len(text_str) * 8  # 8 píxeles por char

        # Seleccionar la fuente más grande que quepa
        if normal_width <= max_width:
            self.text(text_str, x, y)
        elif small_width <= max_width:
            self.text_small(text_str, x, y, 'small')
        elif tiny_width <= max_width:
            self.text_small(text_str, x, y, 'tiny')
        else:
            # Si ni siquiera la fuente tiny cabe, truncar el texto
            max_chars = max_width // 5
            truncated = text_str[:max_chars-1] + "." if len(text_str) > max_chars else text_str
            self.text_small(truncated, x, y, 'tiny')

class SSD1306_I2C(SSD1306):
    def __init__(self, width, height, i2c, addr=0x3C, external_vcc=False):
        self.i2c = i2c
        self.addr = addr
        self.temp = bytearray(2)
        self.write_list = [b"\x40", None]  # Co=0, D/C#=1
        super().__init__(width, height, external_vcc)

    def write_cmd(self, cmd):
        self.temp[0] = 0x80  # Co=1, D/C#=0
        self.temp[1] = cmd
        self.i2c.writeto(self.addr, self.temp)

    def write_data(self, buf):
        self.write_list[1] = buf
        self.i2c.writevto(self.addr, self.write_list)
