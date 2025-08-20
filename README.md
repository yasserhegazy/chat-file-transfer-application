# Lab Report: Python TCP Chat & File Transfer System

## 1. Introduction
This lab implements a multi-client chat system with file transfer using Python’s `socket` and `threading` libraries. The system supports private messaging, broadcast, groups, and sending files to users or groups over TCP. A dedicated chat socket handles control/messages, and a separate file socket streams binary payloads reliably.

**Goals**
- Practice TCP socket programming and concurrency with threads.
- Design a simple text protocol for chat commands and file transfer headers.
- Ensure safe concurrent access to shared state (clients, file sockets, groups) via locks.
- Provide basic reliability feedback using application-level ACKs for received files.

---

## 2. System Overview
### 2.1 Components
- **Server**
  - **Chat server** (TCP port `2003`): authenticates username, handles commands (`msg`, `broadcast`, `list`, `join`, `send_group`) and forwards ACKs.
  - **File server** (TCP port `2007`): accepts file uploads and forwards them to recipients.
- **Client**
  - Maintains **two persistent connections**: one to the chat port and one to the file port.
  - Provides a command-line interface for sending messages and files.

### 2.2 Data Structures (Server)
- `clients: Dict[str, socket]` — username → chat socket.
- `file_clients: Dict[str, socket]` — username → file socket.
- `groups: Dict[str, List[str]]` — group name → member usernames.
- `threading.Lock()` for `clients_lock` and `file_clients_lock` ensure thread-safe mutations.

### 2.3 Design Rationale
- **Two sockets** separate control flow from bulk data, preventing large file transfers from blocking chat.
- **Simple line-based headers** for file transfer enable straightforward framing, then raw bytes are streamed.
- **Application ACKs** (`FILE_RECEIVED|<sender>|<filename>`) inform senders that recipients finished writing the file.

---

## 3. Environment & Setup
- Python 3.x
- Run server and clients on localhost or the same LAN.

```bash
# Terminal 1: start the server
python server.py

# Terminal 2+: start clients
python client.py
```

On client startup you’ll be prompted for a username. The client then opens both chat and file sockets to the server and discards the file socket’s initial greeting (`FILE_SOCKET_CONNECTED\n`).

---

## 4. Protocols
### 4.1 Chat Commands (sent over chat socket)
- **Private message**: `msg <username> <message>`
- **Broadcast**: `broadcast <message>`
- **List users**: `list`
- **Join group**: `join <group_name>`
- **Send group message**: `send_group <group_name> <message>`

### 4.2 File Transfer
- **Client → Server upload header** (text line):
  ```
  sender|target|filename|filesize\n
  # followed by exactly <filesize> raw bytes
  ```
  - `target` is either a username or `GROUP:<group_name>`.
- **Server → Recipient forward header** (text line):
  ```
  sender|filename|filesize\n
  # followed by exactly <filesize> raw bytes
  ```
- **Application ACK** (chat socket), sent by recipient after file is fully written:
  ```
  FILE_RECEIVED|<original_sender>|<filename>
  ```
  The server relays an `[ACK] <recipient> received file '<filename>'` line to the original sender.

- **Receive-side atomic write** (client): data is written to `received_from_<sender>_<filename>.tmp` and then atomically renamed to `received_from_<sender>_<filename>` using `os.replace` for durability.

---

## 5. Step-by-Step: Running & Using the System
### 5.1 Start Server
1. Run `server.py`.
2. The server starts two listeners: `[CHAT SERVER] Listening on 2003` and `[FILE SERVER] Listening on 2007`.

### 5.2 Start Clients
1. Run `client.py` and enter a unique username (e.g., `yasser`, `ahmad`).
2. You should see `Welcome, <username>!`.

### 5.3 List Connected Users
```
list
Connected users: ahmad, sara
```

### 5.4 Private Messaging
```
msg ahmad Hi Ahmad!
[PRIVATE from yasser]: Hi Ahmad!
```

### 5.5 Broadcast Messaging
```
broadcast Hello everyone!
[BROADCAST from yasser]: Hello everyone!
```

### 5.6 Groups
```
join devs
You joined group 'devs'

send_group devs Standup at 10 AM.
[GROUP devs] yasser: Standup at 10 AM.
```

### 5.7 Send a File to a User
```
send_file ahmad report.pdf
[FILE] yasser sent you file 'report.pdf' (12345 bytes). Incoming on file socket.
[FILE RECEIVED] received_from_yasser_report.pdf (12345 bytes) from yasser
[ACK] ahmad received file 'report.pdf'
```

### 5.8 Send a File to a Group
```
join team
# (have others join 'team' as well)

send_file GROUP:team slides.pptx
# Each member sees a chat notification and receives the file on the file socket.
```

---

## 6. Testing Scenarios & Results
Use the following tests to validate behavior.

1. **Concurrent clients**: Start 3–5 clients. Send broadcasts and private messages concurrently. Expect no server crashes; each message delivered once to intended recipients.
2. **File transfer (small text)**: Send a `.py` or `.txt` file. Confirm the client does not display code as chat. Expected: file notice on chat socket, binary on file socket, saved to `received_from_<sender>_<filename>`.
3. **File transfer (large binary)**: Send images/PDFs. Observe smooth transfer and a single `[FILE RECEIVED]` line followed by one `[ACK]` at sender.
4. **Group messages & files**: Join multiple users to a group and send `send_group` / `send_file GROUP:<g>`. Verify only group members (except sender) receive.
5. **Missing file socket**: Stop a client’s file socket (or start a client that didn’t connect file socket). Sender should get: `User <target> has no file socket; file saved as received_from_<sender>_<filename>`.
6. **Self-send**: `send_file <your_username> foo.bin` — server acknowledges save but won’t forward back to you (avoids duplicates).

Results should show single ACKs, correct filenames, and no interleaving of binary data and chat text.

---

## 7. Error Handling & Edge Cases
- **Invalid headers / sizes**: Server logs `[FILE ERROR]` and continues without terminating other sessions.
- **Partial reads**: `recv_exact` loops until expected byte count (or socket closes) for reliable payload reads.
- **Atomic writes**: Client uses temp file + `os.replace` to avoid corrupt/incomplete files appearing as final.
- **Cleanup**: On disconnect, server removes user from `clients` and `file_clients` maps and closes sockets.
- **Locks**: All access to shared dicts is protected with `threading.Lock()` to prevent races.

---

## 8. Concurrency & Synchronization
- Each accepted connection runs in its own daemon thread.
- Shared maps (`clients`, `file_clients`) are mutated only inside `with <lock>:` blocks.
- Group operations read `groups` without a lock since the only mutation is appending a username during `join_group`. (If you expect heavy churn or deletions, wrap `groups` in a lock as well.)

---

## 9. Security Considerations (Basic)
- **No authentication**: Any username is accepted; consider adding auth.
- **No encryption**: Traffic is plaintext; consider TLS (e.g., `ssl` module) for confidentiality.
- **Path safety**: Filenames are treated as plain names; avoid directory traversal by stripping paths and rejecting `..`.
- **Resource limits**: Optionally cap `filesize` and add quotas.

---

## 10. Limitations & Future Work
- Resume/continue interrupted transfers.
- Progress bars / percentage feedback.
- Per-user offline inbox (store and forward on reconnect).
- Message history and file indexing.
- Optional compression.

---

## 11. Conclusion
This lab demonstrated a practical, concurrent TCP chat system with binary file transfer over a separate data channel. The design isolates control and data planes, applies simple and clear text headers for framing, and confirms delivery via ACKs. With modest additions (auth, TLS, persistence), this can serve as a solid foundation for a production-ready messaging service.

