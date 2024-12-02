#!/bin/bash
# Creates a tar archive of all my service JSON files.

root="$(dirname $(dirname $(realpath $0)))"
output_dir="${HOME}"
output_name="dimrod_config_backup_$(date +"%Y-%m-%d_%H-%M-%S").tar.gz"

# look for all config files and pipe the output to a tar command
find "${root}" -name "*.json" -o \
               -name "*.yml" | \
    tar -czf "${output_dir}/${output_name}" -T -

