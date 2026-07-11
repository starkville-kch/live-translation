"""V3 quick: open 10 SSE connections, check server-side count, then close."""
import threading, http.client, time, urllib.request, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

NUM = 10
connections = []
lock = threading.Lock()

def connect(i):
    try:
        conn = http.client.HTTPConnection("localhost", 8000, timeout=15)
        conn.request("GET", "/events", headers={"Accept": "text/event-stream"})
        resp = conn.getresponse()
        with lock:
            connections.append((i, conn, resp.status))
    except Exception as e:
        with lock:
            connections.append((i, None, str(e)))

threads = [threading.Thread(target=connect, args=(i,)) for i in range(NUM)]
for t in threads: t.start()
time.sleep(2)  # let all connect

# Check server-side count
try:
    r = urllib.request.urlopen("http://localhost:8000/api/status", timeout=5)
    status = json.loads(r.read())
    attendees = status["attendees"]
except Exception as e:
    attendees = f"error: {e}"

connected = [c for c in connections if isinstance(c[2], int) and c[2] == 200]
print(f"Clients connected: {len(connected)}/{NUM}")
print(f"Server attendee count: {attendees}")

# Clean up
for _, conn, _ in connections:
    if conn:
        try: conn.close()
        except: pass

for t in threads:
    t.join(timeout=1)

if len(connected) == NUM:
    print("PASS: all 10 SSE clients connected successfully")
else:
    errors = [c for c in connections if not (isinstance(c[2], int) and c[2] == 200)]
    for i, _, err in errors:
        print(f"  Client {i} failed: {err}")
    print("FAIL")
