import socket
import threading
import os

# Server configuration
HOST = "127.0.0.1"
CHAT_PORT = 2003
FILE_PORT = 2007

# Keep track of connected clients: username -> chat_socket
clients = {}
# Keep track of file sockets separately: username -> file_socket
file_clients = {}
groups = {}

clients_lock = threading.Lock()
file_clients_lock = threading.Lock()

# --- Chat Handling ---
def handle_client(conn, addr):
    username = None
    try:
        # First, receive the username
        username = conn.recv(1024).decode().strip()
        if not username:
            conn.close()
            return

        with clients_lock:
            clients[username] = conn

        conn.send(f"Welcome, {username}!\n".encode())
        print(f"[NEW CONNECTION] {username} connected from {addr}")

        while True:
            data = conn.recv(4096)
            if not data:
                break
            # The strip is to remove any trailing newline or spaces
            msg = data.decode().strip()
            # To make sure we have a valid command, we split by space and check the first part, ensuring it's lowercase
            command = msg.split(" ", 1)[0].lower()
            # Application-level ACK from a receiver client:
            if msg.startswith("FILE_RECEIVED|"):
                # format: FILE_RECEIVED|original_sender|filename
                parts = msg.split("|", 2)
                if len(parts) == 3:
                    original_sender = parts[1]
                    filename = parts[2]
                    with clients_lock:
                        if original_sender in clients:
                            try:
                                clients[original_sender].sendall(f"[ACK] {username} received file '{filename}'\n".encode())
                            except:
                                print(f"Could not deliver ACK to {original_sender}")
                continue
            # For sending private messages, broadcasting, listing users, joining groups, and sending group messages:
            # For sending private messages
            if command.startswith("msg"):
                parts = msg.split(" ", 2)
                if len(parts) < 3:
                    conn.send(b"Usage: msg <username> <message>\n")
                    continue
                target_user, message = parts[1], parts[2]
                send_private(username, target_user, message)
            # For broadcasting messages to all connected users
            elif command.startswith("broadcast"):
                message = msg.split(" ",1)[1]
                broadcast(username, message)
            # For listing connected users, excluding the sender
            elif command.startswith("list"):
                user_list = list_connected_users(username)
                conn.send(f"Connected users: {', '.join(user_list)}\n".encode())
            # For joining a group
            elif command.startswith("join"):
                group_name = msg.split(" ",1)[1]
                join_group(username, group_name)
            # For sending messages to a group
            elif command.startswith("send_group"):
                parts = msg.split(" ",2)
                if len(parts) < 3:
                    conn.send(b"Usage: send_group <group_name> <message>\n")
                    continue
                group_name, message = parts[1], parts[2]
                send_group_message(username, group_name, message)
            # If the command is not recognized, send an error message
            else:
                conn.send(b"Unknown command. Use msg/broadcast/list/join/send_group\n")
            # Echo the message for the server log
            print(f"[{username}] {msg}")

    except Exception as e:
        print(f"[ERROR] {addr} -> {e}")
    
    finally:
        # cleanup chat socket
        with clients_lock:
            if username in clients:
                try:
                    clients[username].close()
                except:
                    pass
                del clients[username]
        # cleanup file socket (if any)
        with file_clients_lock:
            if username in file_clients:
                try:
                    file_clients[username].close()
                except:
                    pass
                del file_clients[username]
        conn.close()
        print(f"[DISCONNECTED] {username}")
# --- Chat Helpers ---
# Broadcast a message to all connected users except the sender
def broadcast(sender, message):
    with clients_lock:
        for user, conn in list(clients.items()):
            if user != sender:
                try:
                    conn.sendall(f"[BROADCAST from {sender}]: {message}\n".encode())
                except:
                    try:
                        conn.close()
                    except:
                        pass
                    del clients[user]
# Send a private message to a specific user
def send_private(sender, target_user, message):
    with clients_lock:
        if target_user in clients:
            try:
                clients[target_user].sendall(f"[PRIVATE from {sender}]: {message}\n".encode())
            except:
                print(f"Error sending to {target_user}")
        else:
            if sender in clients:
                clients[sender].sendall(f"User {target_user} not found.\n".encode())
# List all connected users except the current user
def list_connected_users(current_user):
    with clients_lock:
        return [u for u in clients.keys() if u != current_user]
# Join a group or create it if it doesn't exist
def join_group(username, group_name):
    if group_name not in groups:
        groups[group_name] = []
    if username not in groups[group_name]:
        groups[group_name].append(username)
    with clients_lock:
        if username in clients:
            clients[username].sendall(f"You joined group '{group_name}'\n".encode())
# Send a message to a group
def send_group_message(sender, group_name, message):
    if group_name not in groups or sender not in groups[group_name]:
        with clients_lock:
            if sender in clients:
                clients[sender].sendall(f"You are not in group '{group_name}'\n".encode())
        return
    with clients_lock:
        for user in groups[group_name]:
            if user != sender and user in clients:
                try:
                    clients[user].sendall(f"[GROUP {group_name}] {sender}: {message}\n".encode())
                except:
                    pass

# --- File Handling helpers ---
# Receive a line of bytes until newline, decode it, and return without the newline.
# Returns None if the connection is closed.
def recv_line(conn):
    """Read bytes until newline and return decoded string without newline, or None if closed."""
    data = bytearray()
    while True:
        b = conn.recv(1)
        if not b:
            return None
        data += b
        if data.endswith(b'\n'):
            return data[:-1].decode(errors='ignore')
# `recv_exact` reads exactly `size` bytes from the connection, or fewer if the connection closes.
# It returns the bytes read, which may be less than `size` if the connection is closed.
# This is useful for receiving file data where the size is known.
def recv_exact(conn, size):
    """Read exactly size bytes (or fewer if connection closes)."""
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(min(4096, size - len(data)))
        if not chunk:
            break
        data += chunk
    return bytes(data)

def handle_file_client(conn, addr):
    """
    Handle incoming file transfers.
    Protocol for upload from client to server:
      sender|target|filename|filesize\n  followed by exactly filesize bytes.
    Server forwards to recipient(s) using:
      sender|<original filename>|filesize\n  followed by exactly filesize bytes.
    """
    username = None
    try:
        # First message on this socket is the username (no newline required)
        raw = conn.recv(1024)
        if not raw:
            conn.close()
            return
        username = raw.decode().strip()
        with file_clients_lock:
            file_clients[username] = conn

        # send an ACK that file socket is ready (client will discard it)
        try:
            conn.sendall(b"FILE_SOCKET_CONNECTED\n")
        except:
            pass

        print(f"[FILE SOCKET] {username} connected for files from {addr}")

        while True:
            header = recv_line(conn)
            if header is None:
                break
            parts = header.split("|")
            if len(parts) != 4:
                print(f"[FILE ERROR] Invalid header from {addr}: {header}")
                continue
            sender, target, filename, filesize_s = parts
            try:
                filesize = int(filesize_s)
            except ValueError:
                print(f"[FILE ERROR] Invalid filesize from {addr}: {filesize_s}")
                continue

            # receive data
            filedata = recv_exact(conn, filesize)

            # server-side saved name (used for storage only)
            server_saved = f"received_from_{sender}_{filename}"
            try:
                with open(server_saved, "wb") as f:
                    f.write(filedata)
            except Exception as e:
                print(f"[FILE WRITE ERROR] {e}")
                # still continue

            print(f"[FILE RECEIVED] {server_saved} from {sender} (intended for {target})")

            # If the sender targeted themself, notify once and do not forward
            if target == sender:
                with clients_lock:
                    if sender in clients:
                        try:
                            clients[sender].sendall(f"[FILE] You sent file '{filename}' (saved as {server_saved}).\n".encode())
                        except:
                            pass
                continue

            # Forward to group
            if target.startswith("GROUP:"):
                group_name = target[6:]
                if group_name in groups:
                    for user in groups[group_name]:
                        if user != sender:
                            # notify recipient via chat (if available)
                            with clients_lock:
                                if user in clients:
                                    try:
                                        clients[user].sendall(f"[FILE] {sender} sent file '{filename}' to group '{group_name}'.\n".encode())
                                    except:
                                        pass
                            # forward file on their file socket (if available), forward original filename
                            with file_clients_lock:
                                fc = file_clients.get(user)
                            if fc:
                                try:
                                    send_file_to_client(server_saved, sender, fc, display_name=filename)
                                except Exception as e:
                                    print(f"[FILE FORWARD ERROR] {user} -> {e}")
                continue

            # Private forward
            with clients_lock:
                targ_chat = clients.get(target)
            with file_clients_lock:
                targ_file = file_clients.get(target)

            if targ_chat:
                try:
                    targ_chat.sendall(f"[FILE] {sender} sent you file '{filename}' ({filesize} bytes). Incoming on file socket.\n".encode())
                except:
                    pass

            if targ_file:
                try:
                    send_file_to_client(server_saved, sender, targ_file, display_name=filename)
                except Exception as e:
                    print(f"[FILE FORWARD ERROR] {target} -> {e}")
            else:
                # inform sender that recipient has no file socket (do not notify recipient)
                with clients_lock:
                    if sender in clients:
                        try:
                            clients[sender].sendall(f"User {target} has no file socket; file saved as {server_saved}\n".encode())
                        except:
                            pass

    except Exception as e:
        print(f"[FILE ERROR] {addr} -> {e}")
    finally:
        try:
            conn.close()
        except:
            pass
        with file_clients_lock:
            if username and username in file_clients and file_clients[username] is conn:
                del file_clients[username]
        print(f"[FILE SOCKET CLOSED] {addr}")

def send_file_to_client(file_path, sender, conn, display_name=None):
    """Send a file to a client using the protocol: sender|display_name|filesize\n then raw bytes"""
    try:
        filesize = os.path.getsize(file_path)
    except OSError:
        print(f"[FILE SEND ERROR] missing file {file_path}")
        return
    if display_name is None:
        display_name = os.path.basename(file_path)
    header = f"{sender}|{display_name}|{filesize}\n"
    try:
        conn.sendall(header.encode())
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                conn.sendall(chunk)
    except Exception as e:
        try:
            peer = conn.getpeername()
        except:
            peer = "<unknown>"
        print(f"[FILE SEND ERROR] {peer} -> {e}")

# --- Start Servers ---

def start_chat_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, CHAT_PORT))
    server.listen()
    print(f"[CHAT SERVER] Listening on {CHAT_PORT}")

    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

def start_file_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, FILE_PORT))
    server.listen()
    print(f"[FILE SERVER] Listening on {FILE_PORT}")

    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_file_client, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    threading.Thread(target=start_chat_server, daemon=True).start()
    start_file_server()
