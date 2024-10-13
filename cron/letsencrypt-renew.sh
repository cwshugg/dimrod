#!/bin/bash
# Simple script to renew my LetsEncrypt certificate.

# stop nginx, which is hogging port 80 (certbot needs this)
systemctl stop nginx.service

# renew
certbot renew

# restart nginx
systemctl start nginx.service

