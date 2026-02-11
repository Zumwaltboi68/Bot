#!/bin/bash

# Wait for X server to be ready
echo "Starting services..."

# Start supervisor
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
