#!/bin/bash
# Launch BOT Exchange Rate Processor with Python 3.12 (Tk 8.6)
# The system Python 3.9 ships with Tk 8.5 which cannot render CustomTkinter widgets.
cd "$(dirname "$0")"
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 main.py "$@"
