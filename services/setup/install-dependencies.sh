#!/bin/bash
# Simple script to install flask for python.

# Globals
C_NONE="\033[0m"
C_ACC1="\033[36m"
C_GOOD="\033[32m"
C_BAD="\033[31m"

# Find Pip
pipout="$(python3 -m pip 2> /dev/null)"
if [ -z "${pipout}" ]; then
    echo "Installing pip..."
    sudo apt install python3-pip -y
fi

echo -e "${C_ACC1}Installing flask...${C_NONE}"
python3 -m pip install Flask
echo ""

echo -e "${C_ACC1}Installing gevent...${C_NONE}"
python3 -m pip install gevent
echo ""

echo -e "${C_ACC1}Installing jwt...${C_NONE}"
python3 -m pip uninstall JWT
python3 -m pip uninstall PyJWT
python3 -m pip install PyJWT
echo ""

echo -e "${C_ACC1}Installing ipaddress...${C_NONE}"
python3 -m pip install ipaddress
echo ""

echo -e "${C_ACC1}Installing geopy...${C_NONE}"
python3 -m pip install geopy
echo ""

echo -e "${C_ACC1}Installing dateutil...${C_NONE}"
python3 -m pip install python-dateutil
echo ""

echo -e "${C_ACC1}Installing pyTelegramBotAPI...${C_NONE}"
python3 -m pip install pyTelegramBotAPI
echo ""

echo -e "${C_GOOD}Finished.${C_NONE}"

