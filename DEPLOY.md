# Deployment Guide — DigitalOcean

Same pattern as the Order Reconciler: systemd-managed Streamlit app behind
Nginx with a Let's Encrypt certificate, on its own subdomain.

## 1. Create a Droplet
- Ubuntu 24.04 LTS
- Basic plan: $6/month (1GB RAM) is sufficient
- Region: London (LON1)
- Add your SSH key during setup

## 2. Initial server setup
```bash
ssh root@YOUR_DROPLET_IP
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv nginx git certbot python3-certbot-nginx
adduser --disabled-password --gecos "" appuser
```

## 3. Deploy the app
```bash
su - appuser
git clone https://github.com/SimonUnify/carrier-invoice-converter.git
cd carrier-invoice-converter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env     # set a strong CONVERTER_PASSWORD
chmod 600 .env
```

## 4. Run as a systemd service
```bash
exit   # back to root

cat > /etc/systemd/system/invoice-converter.service << 'SERVICE'
[Unit]
Description=Carrier Invoice Converter (Streamlit)
After=network.target

[Service]
User=appuser
WorkingDirectory=/home/appuser/carrier-invoice-converter
Environment="PATH=/home/appuser/carrier-invoice-converter/.venv/bin"
EnvironmentFile=/home/appuser/carrier-invoice-converter/.env
ExecStart=/home/appuser/carrier-invoice-converter/.venv/bin/streamlit run app.py --server.port 8502 --server.address 127.0.0.1 --server.headless true
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable invoice-converter
systemctl start invoice-converter
```

> Note: port **8502** so it doesn't clash with the Order Reconciler on 8501 if
> they share a server.

## 5. Configure Nginx
```bash
cat > /etc/nginx/sites-available/invoice-converter << 'NGINX'
server {
    listen 80;
    server_name invoices.fidelityfulfilment.com;

    location / {
        proxy_pass http://127.0.0.1:8502;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
NGINX

ln -s /etc/nginx/sites-available/invoice-converter /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

## 6. SSL Certificate (HTTPS)
```bash
certbot --nginx -d invoices.fidelityfulfilment.com
```

## 7. Point your domain
Add a DNS A record:
- Type: A
- Name: invoices
- Value: YOUR_DROPLET_IP

## 8. Updating the app
```bash
su - appuser
cd carrier-invoice-converter
git pull
source .venv/bin/activate
pip install -r requirements.txt   # if requirements changed
exit
systemctl restart invoice-converter
```

## Security checklist
- [ ] CONVERTER_PASSWORD is strong and unique
- [ ] .env file permissions: chmod 600 .env
- [ ] UFW firewall: ufw allow 22,80,443/tcp && ufw enable
- [ ] SSL certificate installed (HTTPS enforced)
- [ ] Streamlit bound to 127.0.0.1 only (not public)
