#!/bin/bash
# start_fotobox.sh - Autostart-Skript fuer die Fotobox.
# Startet app_with_hw.py mit sudo (fuer GPIO/SPI-Hardwarezugriff) und
# startet bei einem Absturz automatisch neu (Endlosschleife).

LOG_DIR="$HOME/photobooth/data/logs"
LOG_FILE="$LOG_DIR/fotobox.log"
mkdir -p "$LOG_DIR"

while true; do
    sudo python3 /home/photobox/photobooth/app_with_hw.py >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?
    echo "[$(date)] App beendet (Exit-Code: $EXIT_CODE) - Neustart in 3s" >> "$LOG_FILE"
    sleep 3
done
