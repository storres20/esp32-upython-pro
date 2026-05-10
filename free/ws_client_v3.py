# ws_client.py - Versión corregida para Railway / servidores reales
# version corregida de ws_cliente_v2.py - 02/05/2026

import socket
import ssl
import struct
import binascii
import time

class WebSocket:
    def __init__(self):
        self.sock = None
        self.connected = False

    def connect(self, url):
        try:
            # ==============================
            # Parse URL
            # ==============================
            if url.startswith("wss://"):
                host = url[6:].split("/")[0]
                port = 443
                use_ssl = True
                path = "/"
            elif url.startswith("ws://"):
                host = url[5:].split("/")[0]
                port = 80
                use_ssl = False
                path = "/"
            else:
                raise ValueError("URL inválida")

            if ":" in host:
                host, port = host.split(":")
                port = int(port)

            print(f"Conectando a {host}:{port} (SSL: {use_ssl})")

            # ==============================
            # Socket TCP
            # ==============================
            addr = socket.getaddrinfo(host, port)[0][-1]
            self.sock = socket.socket()
            self.sock.settimeout(10)
            self.sock.connect(addr)

            print("Socket conectado")

            # ==============================
            # SSL
            # ==============================
            if use_ssl:
                print("Aplicando SSL...")
                try:
                    self.sock = ssl.wrap_socket(self.sock, server_hostname=host)
                    print("SSL con SNI OK")
                except:
                    self.sock = ssl.wrap_socket(self.sock)
                    print("SSL sin SNI OK")

            # ==============================
            # Handshake WebSocket
            # ==============================
            print("Enviando handshake WebSocket...")

            key = binascii.b2a_base64(b'0123456789ABCDEF')[:-1]

            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key.decode()}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            )

            self.sock.send(request.encode())

            # ==============================
            # Leer respuesta completa
            # ==============================
            response = b""
            start = time.ticks_ms()

            while time.ticks_diff(time.ticks_ms(), start) < 5000:
                try:
                    chunk = self.sock.recv(1024)
                    if chunk:
                        response += chunk
                        if b"\r\n\r\n" in response:
                            break
                except:
                    time.sleep_ms(50)

            # ==============================
            # VALIDACIÓN CORRECTA 🔥
            # ==============================
            response_text = response.decode("utf-8", "ignore").lower()

            if "101 switching protocols" not in response_text:
                print("❌ Handshake fallido")
                print(response[:300])
                raise Exception("Handshake inválido")

            print("✅ Handshake WebSocket exitoso")

            # Non-blocking
            self.sock.setblocking(False)

            self.connected = True
            return True

        except Exception as e:
            print(f"Error en connect(): {e}")

            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass

            self.sock = None
            self.connected = False
            return False

    # ============================================
    # SEND
    # ============================================
    def send(self, data):
        if not self.connected or not self.sock:
            return False

        try:
            if isinstance(data, str):
                data = data.encode()

            frame = bytearray()
            frame.append(0x81)

            length = len(data)

            if length < 126:
                frame.append(0x80 | length)
            elif length < 65536:
                frame.append(0x80 | 126)
                frame.extend(struct.pack(">H", length))
            else:
                frame.append(0x80 | 127)
                frame.extend(struct.pack(">Q", length))

            mask = b'\x00\x00\x00\x00'
            frame.extend(mask)
            frame.extend(data)

            self.sock.send(frame)
            return True

        except Exception as e:
            print(f"Error send(): {e}")
            self.connected = False
            return False

    # ============================================
    # RECV (con PING/PONG)
    # ============================================
    def recv(self):
        if not self.connected or not self.sock:
            return None

        try:
            first = self.sock.recv(1)
            if not first:
                return None

            opcode = first[0] & 0x0F

            second = self.sock.recv(1)
            if not second:
                return None

            length = second[0] & 0x7F

            if length == 126:
                length = struct.unpack(">H", self.sock.recv(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self.sock.recv(8))[0]

            payload = b""
            while len(payload) < length:
                chunk = self.sock.recv(length - len(payload))
                if not chunk:
                    break
                payload += chunk

            if opcode == 0x1:
                return payload.decode()

            elif opcode == 0x9:  # PING
                self._send_pong(payload)
                return None

            elif opcode == 0xA:  # PONG
                return None

            elif opcode == 0x8:
                print("Servidor cerró conexión")
                self.connected = False
                return None

        except OSError as e:
            if e.args[0] == 11:
                return None
            print("Error recv:", e)
            self.connected = False
            return None

        except Exception as e:
            print("Error recv:", e)
            self.connected = False
            return None

    def _send_pong(self, data):
        try:
            frame = bytearray()
            frame.append(0x8A)

            length = len(data)
            frame.append(0x80 | length)

            mask = b'\x00\x00\x00\x00'
            frame.extend(mask)
            frame.extend(data)

            self.sock.send(frame)

        except:
            self.connected = False

    def close(self):
        if self.sock:
            try:
                self.sock.send(b'\x88\x00')
                self.sock.close()
            except:
                pass

        self.sock = None
        self.connected = False