#!/bin/bash
# Simple script to install flask for python.

echo "Installing pip..."
sudo apt install python3-pip -y

echo "Installing flask..."
python3 -m pip install Flask

echo "Installing jwt..."
python3 -m pip uninstall JWT
python3 -m pip uninstall PyJWT
python3 -m pip install PyJWT
