#!/bin/bash
# This script invokes my existing "holiday_lights" script.

# find the script file
script_dir="$(dirname $(realpath $0))"
top="$(dirname ${script_dir})"
script="${top}/services/gatekeeper/subscribers/holiday_lights.py"

# if a file exists with my address in it (which is NOT part of the git repo,
# for obvious reasons), pass it into the script as data
addrfile="${script_dir}/cwshugg_holiday_lights_addr.txt"
jdata=""
if [ -f "${addrfile}" ]; then
    jdata="{\"address\": \"$(cat ${addrfile})\"}"
    echo "Running script with data: ${jdata}"
fi

# execute the script
${script} "${jdata}"

