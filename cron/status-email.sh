#!/bin/bash
# This script invokes code I've written to generate and send a status report
# of my machine and the DImROD services.

# find the script file
top="$(dirname $(dirname $(realpath $0)))"
script="${top}/services/taskmaster/subscribers/status_report.py"

# create JSON data to pass into the script"
jdata="{\"emails\": [\"connorshugg@gmail.com\"]}"

# execute the script
${script} "${jdata}"

