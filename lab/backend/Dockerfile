# Back-end: VERO Nginx 1.21.0 (vulnerabile a CRLF injection con $uri)
# Nginx ha iniziato a sanitizzare i CRLF negli header di risposta nella versione 1.21.1.
# Quindi 1.21.0 è l'ultima versione vulnerabile.
FROM nginx:1.21.0

LABEL org.opencontainers.image.title="crlf-backend-nginx-1.21"
LABEL org.opencontainers.image.description="Vero Nginx 1.21.0 con misconfigurazione \$uri (CRLF injection)"

# Copiamo la configurazione vulnerabile
COPY conf/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80