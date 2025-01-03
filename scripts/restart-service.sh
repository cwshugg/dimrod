#!/bin/bash
# Restarts a service, provided its name.

function __query_systemctl()
{
    query="$1"
    state="$2"
    systemctl list-units --type=service --state=${state} --plain --quiet | \
              grep "${query}" | \
              cut -d " " -f 1
}

if [ $# -lt 1 ]; then
    echo "Usage: $0 SERVICE_NAME"
    exit 1
fi
name="$1"

# is the service currently active?
search_str="dimrod_${name}"
service_name="$(__query_systemctl "${search_str}" "active")"
if [ ! -z "${service_name}" ]; then
    echo "Search \"${service_name}\" is currently active. Restarting..."
    systemctl restart "${service_name}"
    exit 0
fi

# is the service currently inactive?
service_name="$(__query_systemctl "${search_str}" "inactive")"
if [ ! -z "${service_name}" ]; then
    echo "Search \"${service_name}\" is currently inactive. Starting..."
    systemctl start "${service_name}"
    exit 0
fi

echo "Could not find service with name: \"${search_str}\"."

