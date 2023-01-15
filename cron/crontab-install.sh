#!/bin/bash
# Installs my crontab file with the 'crontab' command.

exe="$(which crontab)"
dir="$(dirname $(realpath $0))"
file="${dir}/crontab"

# install the crontab file
cat ${file} | ${exe}

