---
description: Tag, push, and create a GitHub release for BOT_Exrate
---
# Release Workflow

1. Determine the new version number. Ask the user what version to release if not specified.

2. Bump the version string across all project files:
```bash
cd /Users/d7y1ce/AAE/Projects/BOT_Exrate && sed -i '' "s/v$OLD_VERSION/v$NEW_VERSION/g; s/V$OLD_VERSION/V$NEW_VERSION/g; s/Version $OLD_VERSION/Version $NEW_VERSION/g" main.py gui/app.py core/engine.py core/database.py core/api_client.py core/backup_manager.py core/logic.py tests/__init__.py CLAUDE.md README.md
```

3. Stage and commit all changes:
```bash
cd /Users/d7y1ce/AAE/Projects/BOT_Exrate && git add -A && git commit -m "release: bump version to v$NEW_VERSION"
```

4. Create an annotated tag:
```bash
cd /Users/d7y1ce/AAE/Projects/BOT_Exrate && git tag -a "v$NEW_VERSION" -m "v$NEW_VERSION release"
```

5. Push the commit and tag:
```bash
cd /Users/d7y1ce/AAE/Projects/BOT_Exrate && git push origin main --tags
```

6. Create the GitHub release with detailed release notes:
```bash
cd /Users/d7y1ce/AAE/Projects/BOT_Exrate && gh release create "v$NEW_VERSION" --title "v$NEW_VERSION — [Summary]" --notes "[Release notes in markdown]"
```
