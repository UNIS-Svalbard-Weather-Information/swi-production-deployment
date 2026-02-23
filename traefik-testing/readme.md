To generate the SSL certificate :

```bash
mkdir -p certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/local.key -out certs/local.crt \
  -subj "/CN=*.docker.localhost"
```

The passwork of the control panel available at `https://dashboard.docker.localhost` is `test123`