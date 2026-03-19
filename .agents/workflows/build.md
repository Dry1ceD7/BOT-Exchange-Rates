---
description: Build a standalone executable for BOT_Exrate using PyInstaller
---
# Build Workflow

1. Activate the virtual environment:
```bash
cd /Users/d7y1ce/AAE/Projects/BOT_Exrate && source venv/bin/activate
```

2. Run PyInstaller with the project spec:
```bash
cd /Users/d7y1ce/AAE/Projects/BOT_Exrate && pyinstaller --onefile --windowed --name "BOT_ExRate" --icon assets/icon.icns main.py
```

3. Verify the build output exists:
```bash
ls -la /Users/d7y1ce/AAE/Projects/BOT_Exrate/dist/
```

4. Report the build size and location to the user.
