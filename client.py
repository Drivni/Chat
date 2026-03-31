import socket
import threading


def safe_print(*args, **kwargs):
    with safe_print.lock:
        print(*args, **kwargs)


safe_print.lock = threading.Lock()


def recv_loop(sock: socket.socket):
    try:
        while True:
            data = sock.recv(4096)
            if not data:
                safe_print("[сервер] соединение закрыто")
                break
            safe_print(data.decode("utf-8", errors="replace").rstrip("\r\n"))
    except OSError:
        pass


def read_port(prompt: str) -> int:
    while True:
        s = input(prompt).strip()
        try:
            port = int(s)
        except ValueError:
            safe_print("Порт должен быть числом.")
            continue
        if 1 <= port <= 65535:
            return port
        safe_print("Порт должен быть в диапазоне 1..65535.")


def main():
    host = input("IP сервера (по умолчанию 127.0.0.1): ").strip() or "127.0.0.1"
    if host == "0.0.0.0":
        safe_print("Адрес 0.0.0.0 используется для прослушивания на сервере.")
        safe_print("Для подключения укажите конкретный адрес, например 127.0.0.1.")
        host = "127.0.0.1"

    port = read_port("Порт сервера: ")
    nickname = input("Ваше имя (можно пусто): ").strip()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
    except OSError as e:
        safe_print(f"Не удалось подключиться к {host}:{port}: {e}")
        return

    safe_print("Подключено. Пишите сообщения и нажимайте Enter. Команда: /quit")

    t = threading.Thread(target=recv_loop, args=(sock,), daemon=True)
    t.start()

    try:
        while True:
            line = input()
            if line.strip() == "/quit":
                break
            if nickname:
                line = f"{nickname}: {line}"
            try:
                sock.sendall((line + "\n").encode("utf-8", errors="replace"))
            except OSError:
                safe_print("[сервер] не удалось отправить (соединение потеряно)")
                break
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
