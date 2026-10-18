#!/bin/sh
set -eu
exec /usr/bin/python3 /usr/local/lib/singbox-manager/network_isolation.py dhcp-hook
