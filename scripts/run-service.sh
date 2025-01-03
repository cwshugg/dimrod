#!/bin/bash
# Simple bash script to run a service in a Python virtual environment.

# Globals
C_NONE="\033[0m"
C_ACC1="\033[36m"
C_GOOD="\033[32m"
C_BAD="\033[31m"

# the first argument should be a path to the executable
if [ $# -lt 1 ]; then
    echo "Please provide a path to your service's executable as the first argument."
    exit 1
fi
service_bin="$(realpath "$1")"
if [ ! -f "${service_bin}" ]; then
    echo "${C_BAD}Could not find service executable at: ${service_bin}"
    exit 2
fi
service_dir="$(dirname "$(realpath "$1")")"
echo -e "Service Executable:        ${C_ACC1}${service_bin}${C_NONE}"

# shift the arguments, such that `$@` will now refer to the remaining arguments
shift
echo -e "Service Arguments:         ${C_ACC1}$@${C_NONE}"

# -------------------------- Requirement Gathering --------------------------- #
# create a path to place the virtual environment, and gather the paths of the
# various `requirements.txt` files needed
venv_dir="${service_dir}/.venv"
venv_reqs=()
echo -e "Virtual Environment Path:  ${C_ACC1}${venv_dir}${C_NONE}"

# look for the library code's `requirements.txt`
lib_dir="$(dirname "${service_dir}")/lib"
venv_reqs_lib="${lib_dir}/requirements.txt"
if [ ! -f "${venv_reqs_lib}" ]; then
    echo -e "${C_BAD}Could not find library requirements file at: ${venv_reqs_lib}"
    exit 3
fi
venv_reqs+=("${venv_reqs_lib}")

# if the service has its own `requirements.txt`, add it
venv_reqs_service="${service_dir}/requirements.txt"
if [ -f "${venv_reqs_service}" ]; then
    venv_reqs+=("${venv_reqs_service}")
fi

echo -e "Requirement Files:         ${C_ACC1}${venv_reqs[@]}${C_NONE}"

# ------------------------ Virtual Environment Setup ------------------------- #
# create the venv
python3 -m venv "${venv_dir}"

# source the environment
source "${venv_dir}/bin/activate"
echo -e "${C_GOOD}Virtual environment initialized.${C_NONE}"

# install requirements from all files
for req in "${venv_reqs[@]}"; do
    python3 -m pip install -r "${req}"
done
echo -e "${C_GOOD}Python dependencies installed.${C_NONE}"

# finally, run the executable with all remaining arguments
echo ""
python3 "${service_bin}" "$@"

