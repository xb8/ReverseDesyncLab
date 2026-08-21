---
title: "Reverse Desync + TE.Chunked Bypass in httpx+nginx systems"
date: 2026-08-14
tags: ["HTTP Smuggling", "Reverse Desync", "CRLF Injection", "Nginx", "httpx", "Response Splitting"]
description: "From PortSwigger's research to a working PoC: how we demonstrated that Reverse Desyncs are possible today by leveraging httpx and a Transfer-Encoding chunked bypass."
---

## TL;DR

After reading the research by [Tom Stacey (@t0xodile) and Tobia Righi (@m4st3rspl1nt3r)](https://portswigger.net/research/crlf-powered-desync-attacks) on *CRLF-Powered Desync Attacks*, I wanted to dive deeper into a specific aspect: **Reverse Desyncs** (Response Splitting).

I wanted to find out if they were still possible, even if just in a lab environment, what technology stack would allow it, and whether such a configuration is plausible in the wild. I empirically tested how various HTTP clients handle stacked responses and discovered that **`httpx`** (the most widely used HTTP client in Python) follows the RFC to the letter, thus lacking the "over-read" mitigation that protects browsers, `curl`, and other libraries.

I then built a plausible lab (a Python reverse proxy with `httpx` talking to a vulnerable Nginx 1.21.0) and developed a bypass based on `Transfer-Encoding: chunked` to neutralize Nginx's default HTML body. The result? A confirmed cross-user XSS.

---

## 1. Background: CRLF-Powered Desync and Reverse Desync

PortSwigger's research demonstrated that request-side CRLF injection (Request Splitting) can escalate into a Desync Worm. But the paper also mentions *Reverse Desync Attacks*, a technique dating back to 2004 (Amit Klein) that was considered dead due to the "Stacked Response Problem".

### What is a Reverse Desync?

In a classic (Forward) Desync attack, the attacker poisons the **request** to desynchronize the request queue. In a Reverse Desync, the attacker poisons the **response** (Response Splitting): forcing the back-end to send two HTTP responses over a single keep-alive connection.

The first response goes to the attacker. The **second response** (injected) remains in the TCP buffer. When the next victim makes a request, the proxy serves them the poisoned response.

### Why was it considered "dead"? The Stacked Response Problem

Modern browsers and `curl` implement **over-read protection**: after reading a response body (based on `Content-Length`), they read a few extra bytes. If they find extra data (the injected second response), they close the connection and discard the poison.

## 2. Research: Which clients are vulnerable?

To check if widely used HTTP clients exist today that do **not** implement this protection, I wrote a Python script acting as a mini-server that deliberately sends stacked responses, testing different framing variants:

1. `cl0` (`Content-Length: 0` + 2nd response)
2. `chunked_empty` (`TE: chunked`, empty body, `0\r\n\r\n` + 2nd response)
3. `chunked_body` (`TE: chunked`, body "hello" + 2nd response)

The results were as follows:

| Client | CL:0 | Chunked | Hardening |
| --- | --- | --- | --- |
| **curl** | ✅ SAFE (Excess found) | ✅ SAFE (new conn) | Complete |
| **httpx** | ⚠️ VULN | ⚠️ VULN | **None** |
| **Node.js** | Inc | Inc | Partial |
| **urllib** | N/A | N/A | Does not reuse connections |

**`httpx`** accepts the injected second response and serves it to the next request without complaining. It follows the RFC strictly (reading exactly the declared bytes), but is insecure in this scenario.

## 3. The Lab: A plausible stack

If `httpx` is the "weak link", where is it found in the wild? It is not a standalone server; it's a client library. It is found in **custom Python reverse proxies** (FastAPI/Starlette/ASGI) acting as Backend-For-Frontend (BFF) or API Gateways.

We built a Docker lab with this architecture:

```text
Attacker/Victim → BFF (Python + httpx) → Backend (Nginx 1.21.0)

```

* **Backend**: Real Nginx 1.21.0 (the last version before they sanitized CRLF in response headers). It has the misconfiguration described in the paper: `return 302 https://$host$uri;`. This causes `$uri` to be URL-decoded, turning `%0d%0a` into actual `\r\n` in the response's `Location` header.
* **BFF**: A Python ASGI app using `httpx.AsyncClient` to forward requests to the backend with a keep-alive connection pool.

This chain is plausible: Python microservices proxying to Nginx are everywhere.

## 4. The Obstacle: Nginx's HTML body

The first test in the lab failed. Why? When Nginx executes `return 302`, it automatically generates a default HTML body (`<html><head><title>302 Found</title>...`) and sets `Content-Length: 145`.

Injecting the payload to close the first response and start the second created **two conflicting `Content-Length**` headers in the same response. `httpx` used the first one (145) and absorbed the second response as "junk body", failing the attack.

## 5. The Bypass: TE.Chunked

To solve this, a technique mentioned in the original research was used: injecting `Transfer-Encoding: chunked`.

According to RFC 7230, if a message contains both `Content-Length` and `Transfer-Encoding: chunked`, the `Content-Length` **must be ignored**.

So the injected payload in `Location` was modified to:

1. Add `Transfer-Encoding: chunked` (so `httpx` ignores Nginx's `CL: 145`).
2. End the headers with `\r\n\r\n`.
3. Send `0\r\n\r\n` (terminating chunk). This tells `httpx` that the chunked body is empty and the first response is finished.
4. Inject the **second response** containing the XSS.

At that point, Nginx's 145-byte HTML body arrives later and ends up in the buffer, but `httpx` already considers the first response complete and serves the second response (XSS) to the victim.

### The Payload

Payload construction:

```python
xss = '<script>alert("XSS via Reverse Desync + TE.Chunked Bypass")</script>'

payload = (
    "\r\n"                            # Closes the Location header
    "Transfer-Encoding: chunked\r\n"    # Ignores Nginx's Content-Length
    "\r\n"                            # End of first response headers
    "0\r\n"                           # Chunk size 0 (terminates the chunked body)
    "\r\n"                            # End of chunk
    "HTTP/1.1 200 OK\r\n"             # Start of second response (XSS)
    "Content-Type: text/html\r\n"
    f"Content-Length: {len(xss)}\r\n"
    "Connection: keep-alive\r\n"
    "\r\n"
    f"{xss}"
)

```

The attacker sends this request to the BFF:

```http
GET /%0D%0ATransfer-Encoding%3A%20chunked%0D%0A%0D%0A0%0D%0A%0D%0AHTTP%2F1.1%20200%20OK%0D%0AContent-Type%3A%20text%2Fhtml%0D%0AContent-Length%3A%2050%0D%0AConnection%3A%20keep-alive%0D%0A%0D%0A%3Cscript%3Ealert%28%22Reverse%20Desync%20%2B%20TE.Chunked%20Bypass%22%29%3C%2Fscript%3E HTTP/1.1
Host: target

```

Nginx decodes the path and constructs the poisoned response. `httpx` forwards it. The victim navigates to `/api/profile` and receives the JavaScript alert instead of the legitimate JSON.

![image1](/post/images/desync/desyncalert.png)


## 6. Conclusion

Reverse Desyncs are not dead. They live on in the "weak links" of the HTTP chain: RFC-compliant client libraries that lack anti-smuggling mitigations, such as `httpx`.

If an architecture uses a Python proxy in front of a vulnerable Nginx (or any back-end susceptible to response-side CRLF injection), an attacker can bypass the stacked response problem using `Transfer-Encoding: chunked` and poison user responses.

## 7. Gol %0D%0A Roger's Treasure

![image2](/post/images/desync/goldroger.png)

The web is probably vast enough that some vulnerable reverse desync configuration like this exists in the wild... happy hunting!

---

### References and Lab

* **Original Research**: [CRLF-Powered Desync Attacks (PortSwigger)](https://portswigger.net/research/crlf-powered-desync-attacks)
* **Authors**: Tom Stacey (@t0xodile), Tobia Righi (@m4st3rspl1nt3r)
* **Docker Lab**: Available on GitHub (https://github.com/xb8/ReverseDesyncLab)
