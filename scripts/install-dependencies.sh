#!/bin/bash
# Simple script to install dependencies that can't be built into python
# `requirements.txt` files.

# Globals
C_NONE="\033[0m"
C_ACC1="\033[36m"
C_GOOD="\033[32m"
C_BAD="\033[31m"

script_dir="$(dirname $(realpath "$0"))"

# find python
py="$(which python3 2> /dev/null)"
if [ -z "${py}" ]; then
    echo -e "${C_BAD}Failed to find python3. Please install first.${C_NONE}"
    exit 1
else
    echo -e "${C_GOOD}Python is already installed.${C_NONE}"
fi

# install pip
out="$(${py} -m pip 2> /dev/null)"
if [ -z "${out}" ]; then
    echo "Installing python3-pip..."
    sudo apt install python3-pip -y
else
    echo -e "${C_GOOD}python3-pip is already installed.${C_NONE}"
fi

# install venv
out="$(${py} -m venv -h 2> /dev/null)"
if [ -z "${out}" ]; then
    echo "Installing python3-venv..."
    sudo apt install python3-venv -y
else
    echo -e "${C_GOOD}python3-venv is already installed.${C_NONE}"
fi

# nmap
if [ -z "$(which nmap 2> /dev/null)" ]; then
    echo -e "${C_ACC1}Installing nmap...${C_NONE}"
    sudo apt install nmap
else
    echo -e "${C_GOOD}nmap is already installed.${C_NONE}"
fi

