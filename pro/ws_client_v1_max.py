# ws_client.py
# MHUTEMP PRO
# WebSocket compatible con Railway y MicroPython 1.27
#
# Correcciones:
# - Un solo intento TLS por socket
# - Uso preferente de SSLContext
# - SNI obligatorio para Railway
# - Cierre completo después de errores TLS
# - Recolección de memoria
# - Máscara WebSocket aleatoria
# - Lectura controlada de encabezados y payloads

import socket
import ssl
import struct
import binascii
import time
import gc

try:
    import os
except ImportError:
    os = None


class WebSocket:

    def __init__(self):
        self.sock = None
        self.raw_sock = None
        self.connected = False

    # ============================================
    # UTILIDADES
    # ============================================

    def _close_socket(self):
        self.connected = False

        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass

        if self.raw_sock is not None and self.raw_sock is not self.sock:
            try:
                self.raw_sock.close()
            except Exception:
                pass

        self.sock = None
        self.raw_sock = None

        gc.collect()

    def _parse_url(self, url):
        if url.startswith("wss://"):
            remainder = url[6:]
            use_ssl = True
            port = 443

        elif url.startswith("ws://"):
            remainder = url[5:]
            use_ssl = False
            port = 80

        else:
            raise ValueError("URL WebSocket inválida")

        slash_position = remainder.find("/")

        if slash_position >= 0:
            host_port = remainder[:slash_position]
            path = remainder[slash_position:]

            if not path:
                path = "/"
        else:
            host_port = remainder
            path = "/"

        if ":" in host_port:
            host, port_text = host_port.rsplit(":", 1)
            port = int(port_text)
        else:
            host = host_port

        return host, port, path, use_ssl

    def _create_mask(self):
        try:
            if os is not None and hasattr(os, "urandom"):
                return os.urandom(4)
        except Exception:
            pass

        value = time.ticks_us()

        return bytes((
            value & 0xFF,
            (value >> 8) & 0xFF,
            (value >> 16) & 0xFF,
            (value >> 24) & 0xFF
        ))

    def _send_all(self, data):
        total_sent = 0
        data_length = len(data)

        while total_sent < data_length:
            sent = self.sock.send(data[total_sent:])

            if sent is None:
                sent = 0

            if sent <= 0:
                raise OSError("Socket cerrado durante envío")

            total_sent += sent

    def _recv_exact(self, length):
        data = bytearray()

        while len(data) < length:
            chunk = self.sock.recv(length - len(data))

            if not chunk:
                raise OSError("Socket cerrado durante recepción")

            data.extend(chunk)

        return bytes(data)

    # ============================================
    # TLS
    # ============================================

    def _apply_tls(self, raw_sock, host):
        print("Aplicando TLS con SNI...")

        # Método recomendado para MicroPython actual
        if hasattr(ssl, "SSLContext") and hasattr(
            ssl,
            "PROTOCOL_TLS_CLIENT"
        ):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

            # No se valida el certificado porque el ESP32 no tiene
            # instalada actualmente una CA raíz.
            if hasattr(context, "verify_mode") and hasattr(
                ssl,
                "CERT_NONE"
            ):
                try:
                    context.verify_mode = ssl.CERT_NONE
                except Exception:
                    pass

            tls_sock = context.wrap_socket(
                raw_sock,
                server_hostname=host
            )

            print("TLS establecido mediante SSLContext")
            return tls_sock

        # Compatibilidad con compilaciones antiguas
        tls_sock = ssl.wrap_socket(
            raw_sock,
            server_hostname=host
        )

        print("TLS establecido mediante ssl.wrap_socket")
        return tls_sock

    # ============================================
    # CONEXIÓN
    # ============================================

    def connect(self, url):
        self._close_socket()
        gc.collect()

        try:
            host, port, path, use_ssl = self._parse_url(url)

            print(
                "Conectando a {}:{} (SSL: {})".format(
                    host,
                    port,
                    use_ssl
                )
            )

            print("Memoria antes de DNS:", gc.mem_free())

            address_info = socket.getaddrinfo(
                host,
                port,
                0,
                socket.SOCK_STREAM
            )

            if not address_info:
                raise OSError("No se pudo resolver el servidor")

            address = address_info[0][-1]

            del address_info
            gc.collect()

            # ====================================
            # SOCKET TCP
            # ====================================

            self.raw_sock = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            )

            # Timeout amplio para TCP y TLS
            self.raw_sock.settimeout(30)

            print("Memoria antes de TCP:", gc.mem_free())

            self.raw_sock.connect(address)

            print("Socket TCP conectado")
            print("Memoria antes de TLS:", gc.mem_free())

            # ====================================
            # TLS
            # ====================================

            if use_ssl:
                self.sock = self._apply_tls(
                    self.raw_sock,
                    host
                )
            else:
                self.sock = self.raw_sock

            print("Memoria después de TLS:", gc.mem_free())

            # Timeout del handshake HTTP WebSocket
            self.sock.settimeout(10)

            # ====================================
            # HANDSHAKE WEBSOCKET
            # ====================================

            print("Enviando handshake WebSocket...")

            websocket_key_raw = self._create_mask() + self._create_mask()

            websocket_key = binascii.b2a_base64(
                websocket_key_raw
            ).strip()

            request = (
                "GET {} HTTP/1.1\r\n"
                "Host: {}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: {}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "User-Agent: MHUTEMP-PRO-ESP32\r\n"
                "\r\n"
            ).format(
                path,
                host,
                websocket_key.decode()
            )

            self._send_all(request.encode())

            response = bytearray()
            start_time = time.ticks_ms()

            while b"\r\n\r\n" not in response:
                if time.ticks_diff(
                    time.ticks_ms(),
                    start_time
                ) > 10000:
                    raise OSError(
                        "Timeout esperando handshake WebSocket"
                    )

                chunk = self.sock.recv(512)

                if not chunk:
                    raise OSError(
                        "Servidor cerró durante handshake WebSocket"
                    )

                response.extend(chunk)

                if len(response) > 4096:
                    raise OSError(
                        "Respuesta HTTP demasiado grande"
                    )

            response_text = bytes(response).decode(
                "utf-8",
                "ignore"
            )

            status_line = response_text.split(
                "\r\n",
                1
            )[0]

            print("Respuesta servidor:", status_line)

            if (
                " 101 " not in status_line
                and not status_line.endswith(" 101")
            ):
                print("Handshake WebSocket rechazado:")
                print(response_text[:500])

                raise OSError(
                    "Handshake WebSocket inválido"
                )

            print("✅ Handshake WebSocket exitoso")

            # Después del handshake se utiliza modo no bloqueante
            self.sock.setblocking(False)

            self.connected = True

            del response
            del response_text
            del request
            gc.collect()

            print(
                "Memoria al completar conexión:",
                gc.mem_free()
            )

            return True

        except Exception as error:
            print(
                "Error en connect():",
                repr(error)
            )

            self._close_socket()
            time.sleep_ms(300)
            gc.collect()

            print(
                "Memoria después de cerrar TLS:",
                gc.mem_free()
            )

            return False

    # ============================================
    # CREACIÓN DE TRAMAS
    # ============================================

    def _build_frame(self, opcode, payload=b""):
        if isinstance(payload, str):
            payload = payload.encode()

        payload_length = len(payload)
        mask = self._create_mask()

        frame = bytearray()
        frame.append(0x80 | opcode)

        if payload_length < 126:
            frame.append(0x80 | payload_length)

        elif payload_length < 65536:
            frame.append(0x80 | 126)
            frame.extend(
                struct.pack(">H", payload_length)
            )

        else:
            frame.append(0x80 | 127)
            frame.extend(
                struct.pack(">Q", payload_length)
            )

        frame.extend(mask)

        for index in range(payload_length):
            frame.append(
                payload[index] ^ mask[index % 4]
            )

        return frame

    # ============================================
    # SEND
    # ============================================

    def send(self, data):
        if not self.connected or self.sock is None:
            return False

        try:
            frame = self._build_frame(
                0x01,
                data
            )

            self._send_all(frame)

            del frame
            return True

        except Exception as error:
            print(
                "Error send():",
                repr(error)
            )

            self._close_socket()
            return False

    # ============================================
    # PONG
    # ============================================

    def _send_pong(self, payload):
        try:
            frame = self._build_frame(
                0x0A,
                payload
            )

            self._send_all(frame)
            del frame

        except Exception as error:
            print(
                "Error enviando PONG:",
                repr(error)
            )

            self._close_socket()

    # ============================================
    # RECV
    # ============================================

    def recv(self):
        if not self.connected or self.sock is None:
            return None

        try:
            first_byte = self.sock.recv(1)

            if not first_byte:
                return None

            second_byte = self._recv_exact(1)

            opcode = first_byte[0] & 0x0F
            masked = bool(second_byte[0] & 0x80)
            payload_length = second_byte[0] & 0x7F

            if payload_length == 126:
                payload_length = struct.unpack(
                    ">H",
                    self._recv_exact(2)
                )[0]

            elif payload_length == 127:
                payload_length = struct.unpack(
                    ">Q",
                    self._recv_exact(8)
                )[0]

            mask = None

            if masked:
                mask = self._recv_exact(4)

            payload = bytearray(
                self._recv_exact(payload_length)
            )

            if masked and mask is not None:
                for index in range(payload_length):
                    payload[index] ^= mask[index % 4]

            if opcode == 0x01:
                return bytes(payload).decode(
                    "utf-8",
                    "ignore"
                )

            if opcode == 0x09:
                self._send_pong(bytes(payload))
                return None

            if opcode == 0x0A:
                return None

            if opcode == 0x08:
                print("Servidor cerró la conexión WebSocket")
                self._close_socket()
                return None

            return None

        except OSError as error:
            error_number = (
                error.args[0]
                if error.args
                else None
            )

            # EAGAIN: no existen datos disponibles todavía
            if error_number in (11, 35, 110):
                return None

            print(
                "Error recv():",
                repr(error)
            )

            self._close_socket()
            return None

        except Exception as error:
            print(
                "Error recv():",
                repr(error)
            )

            self._close_socket()
            return None

    # ============================================
    # CLOSE
    # ============================================

    def close(self):
        if self.connected and self.sock is not None:
            try:
                close_frame = self._build_frame(
                    0x08,
                    b""
                )

                self._send_all(close_frame)
            except Exception:
                pass

        self._close_socket()
