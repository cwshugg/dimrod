#!/bin/bash
# Simple script to help with getting HTTPS working on my Python HTTP services.
#   https://certbot.eff.org/instructions

# first, install certbot
echo "Installing certbot..."
sudo apt install certbot -y
echo ""

# Run certbot
echo "Generating certificate. Configure it the following way:"
echo "  1. Choose to launch a standalone server."
echo "  2. Make sure your network allows for HTTP communication across the internet via port 80."
sudo certbot certonly

