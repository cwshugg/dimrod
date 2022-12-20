#!/bin/bash
# Simple script to install flask for python.

pipout="$(python3 -m pip 2> /dev/null)"
if [ -z "${pipout}" ]; then
    echo "Installing pip..."
    sudo apt install python3-pip -y
fi

echo "Installing flask..."
python3 -m pip install Flask

echo "Installing jwt..."
python3 -m pip uninstall JWT
python3 -m pip uninstall PyJWT
python3 -m pip install PyJWT

echo "Installing ipaddress..."
python3 -m pip install ipaddress

echo "Installing geopy..."
python3 -m pip install geopy

echo "Installing dateutil..."
python3 -m pip install python-dateutil

