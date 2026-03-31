import socket
import threading


def safe_print(*args, **kwargs):
    with safe_print.lock:
        print(*args, **kwargs)


safe_print.lock = threading.Lock()


def recv_loop(client_sock: socket.socket, addr, clients, clients_lock):
    name = f"{addr[0]}:{addr[1]}"
    safe_print(f"[+] подключился: {name}")

    try:
        while True:
            data = client_sock.recv(4096)
            if not data:
                break

            text = data.decode("utf-8", errors="replace").rstrip("\r\n")
            msg = f"[{name}] {text}\n".encode("utf-8", errors="replace")

            with clients_lock:
                dead = []
                for s in clients:
                    if s is client_sock:
                        continue
                    try:
                        s.sendall(msg)
                    except OSError:
                        dead.append(s)
                for s in dead:
                    clients.discard(s)
                    try:
                        s.close()
                    except OSError:
                        pass
    except OSError:
        pass
    finally:
        with clients_lock:
            clients.discard(client_sock)
        try:
            client_sock.close()
        except OSError:
            pass
        safe_print(f"[-] disconnected: {name}")


def create_listening_socket(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError as e:
        s.close()
        raise e
    s.listen()
    return s


def main():
    host = input("IP сервера (пусто = 0.0.0.0): ").strip() or "0.0.0.0"

    while True:
        port_str = input("Порт сервера: ").strip()
        try:
            port = int(port_str)
        except ValueError:
            safe_print("Порт должен быть числом.")
            continue
        if 1 <= port <= 65535:
            break
        safe_print("Порт должен быть в диапазоне 1..65535.")

    try:
        server_sock = create_listening_socket(host, port)
    except OSError as e:
        safe_print(f"Не удалось занять порт {port} на {host}: {e}")
        safe_print("Выберите другой порт или закройте программу, которая его использует.")
        return

    safe_print(f"Сервер запущен на {host}:{port}")
    safe_print("Подключайтесь клиентами. Ctrl+C для выхода.")

    clients = set()
    clients_lock = threading.Lock()

    try:
        while True:
            client_sock, addr = server_sock.accept()
            with clients_lock:
                clients.add(client_sock)
            t = threading.Thread(
                target=recv_loop,
                args=(client_sock, addr, clients, clients_lock),
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        safe_print("\nОстановка...")
    finally:
        with clients_lock:
            for s in list(clients):
                try:
                    s.close()
                except OSError:
                    pass
            clients.clear()
        try:
            server_sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
