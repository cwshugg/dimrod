#!/bin/bash
# Simple script to help with getting HTTPS working on my Python HTTP services.
#   https://certbot.eff.org/instructions

# get the domain from the command-line
if [ $# -lt 1 ]; then
    echo "Usage: $0 yourdomain.com"
    exit 0
fi
domain="$1"
echo -e "DImROD Domain: ${domain}\n"

# first, install certbot if necessary
cb="$(which certbot)"
if [ -z "${cb}" ]; then
    echo "Installing certbot..."
    sudo apt install certbot -y
    echo ""
fi

# Run certbot
echo "Generating certificate. Configure it the following way:"
echo "  1. Choose to launch a standalone server."
echo "  2. Make sure your network allows for HTTP communication across the internet via port 80."
echo "     (You may have to temporarily port-forward to set up the certificate.)"
sudo ${cb} certonly -d ${domain}

