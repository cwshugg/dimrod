#!/bin/bash
# Simple script to install flask for python.

# Globals
C_NONE="\033[0m"
C_ACC1="\033[36m"
C_GOOD="\033[32m"
C_BAD="\033[31m"

script_dir="$(dirname $(realpath "$0"))"

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

echo -e "${C_ACC1}Installing timezonefinder...${C_NONE}"
python3 -m pip install timezonefinder
echo ""

echo -e "${C_ACC1}Installing dateutil...${C_NONE}"
python3 -m pip install python-dateutil
echo ""

echo -e "${C_ACC1}Installing pyTelegramBotAPI...${C_NONE}"
python3 -m pip install pyTelegramBotAPI
echo ""

echo -e "${C_ACC1}Installing openai...${C_NONE}"
python3 -m pip install openai
echo ""

echo -e "${C_ACC1}Installing MycroftAI Adapt intent parser...${C_NONE}"
python3 -m pip install -e git+https://github.com/mycroftai/adapt#egg=adapt-parser
echo ""

echo -e "${C_ACC1}Installing Todoist Python SDK...${C_NONE}"
python3 -m pip install todoist-api-python
echo ""

echo -e "${C_ACC1}Installing Google API/Auth libraries...${C_NONE}"
python3 -m pip install google-api-python-client
python3 -m pip install google-auth-httplib2
python3 -m pip install google-auth-oauthlib
echo ""

echo -e "${C_ACC1}Installing Wyze SDK...${C_NONE}"
python3 -m pip install wyze-sdk
echo ""

echo -e "${C_ACC1}Installing LIFX LAN SDK...${C_NONE}"
lifxlan_dir="${script_dir}/lifxlan"
git clone "https://github.com/mclarkk/lifxlan" "${lifxlan_dir}"
pushd "${lifxlan_dir}" 2> /dev/null
python3 -m pip install ./
popd 2> /dev/null
rm -rf "${lifxlan_dir}"
echo ""

# nmap
echo -e "${C_ACC1}Installing nmap...${C_NONE}"
sudo apt install nmap
echo ""

echo -e "${C_GOOD}Finished.${C_NONE}"

