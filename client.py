import socket
import threading
import os

SERVER = "127.0.0.1"
CHAT_PORT = 2003
FILE_PORT = 2007

username = input("Enter username: ").strip()

# --- Chat Socket ---
chat_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
chat_sock.connect((SERVER, CHAT_PORT))
chat_sock.sendall(username.encode())

# --- File Socket ---
file_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
file_sock.connect((SERVER, FILE_PORT))
file_sock.sendall(username.encode())

# consume server's initial file-socket ACK so receive_file doesn't parse it as a header
try:
    file_sock.settimeout(2.0)
    try:
        _ack = file_sock.recv(1024).decode()
        # ignore ack contents (server sends "FILE_SOCKET_CONNECTED\n")
    except socket.timeout:
        pass
    finally:
        file_sock.settimeout(None)
except:
    pass

# --- Receive chat ---
def receive_chat():
    while True:
        try:
            msg = chat_sock.recv(4096).decode()
            if not msg:
                print("Chat connection closed.")
                break
            # print as-is (server includes newline when needed)
            print(msg, end='' if msg.endswith('\n') else '\n')
        except Exception as e:
            print(f"Chat connection error: {e}")
            break

# --- Receive file ---
def receive_file():
    while True:
        try:
            # read header until newline
            header_bytes = bytearray()
            while True:
                b = file_sock.recv(1)
                if not b:
                    return
                header_bytes.extend(b)
                if header_bytes.endswith(b'\n'):
                    break
            header = header_bytes[:-1].decode(errors='ignore')
            parts = header.strip().split("|")

            # Accept either forwarded header (sender|filename|filesize)
            # or full header (sender|target|filename|filesize)
            if len(parts) == 3:
                sender, filename, filesize_s = parts
            elif len(parts) == 4:
                sender, target, filename, filesize_s = parts
            else:
                # invalid header (or stray ACK) — report and continue
                print(f"[FILE RECEIVE ERROR] Invalid header: {header}")
                continue

            try:
                filesize = int(filesize_s)
            except:
                print(f"[FILE RECEIVE ERROR] invalid filesize {filesize_s}")
                continue

            # Save file as: received_from_<sender>_<originalfilename>
            save_name = f"received_from_{sender}_{filename}"
            tmp_name = save_name + ".tmp"

            # write to tmp file
            received = 0
            with open(tmp_name, "wb") as f:
                while received < filesize:
                    chunk = file_sock.recv(min(4096, filesize - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)

            # atomically move to final name
            try:
                os.replace(tmp_name, save_name)
            except Exception:
                try:
                    os.rename(tmp_name, save_name)
                except Exception:
                    # fallback: if rename fails, keep tmp_name as final
                    save_name = tmp_name

            # print a single FILE RECEIVED message (no duplicate)
            print(f"[FILE RECEIVED] {save_name} ({filesize} bytes) from {sender}")

            # send application-level ACK to server (so sender knows we saved it)
            try:
                chat_sock.sendall(f"FILE_RECEIVED|{sender}|{filename}".encode())
            except Exception as e:
                print(f"[FILE ACK ERROR] {e}")

            

        except Exception as e:
            print(f"[FILE RECEIVE ERROR] {e}")
            break

# --- Send chat ---
def send_chat():
    while True:
        msg = input("")
        if msg.startswith("send_file "):
            parts = msg.split(" ", 2)
            if len(parts) < 3:
                print("Usage: send_file <username/group> <filepath>")
                continue
            send_file(parts[1], parts[2])
        else:
            try:
                chat_sock.sendall(msg.encode())
            except Exception as e:
                print(f"Chat send error: {e}")
                break

# --- Send file ---
def send_file(target, filepath):
    if not os.path.isfile(filepath):
        print("File does not exist")
        return

    filesize = os.path.getsize(filepath)
    filename = os.path.basename(filepath)
    header = f"{username}|{target}|{filename}|{filesize}\n"

    try:
        file_sock.sendall(header.encode())
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                file_sock.sendall(chunk)
        print(f"[FILE SENT SUCCESSFULLY] {filepath} -> {target}")
    except Exception as e:
        print(f"[FILE SEND ERROR] {e}")

# --- Start threads ---
threading.Thread(target=receive_chat, daemon=True).start()
threading.Thread(target=receive_file, daemon=True).start()
send_chat()
