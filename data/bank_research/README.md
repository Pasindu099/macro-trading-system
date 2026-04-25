# Bank Research Cache

This folder stores cached bank research reports and generated summaries.

Set credentials outside source control:

```powershell
$env:GOOGLE_DRIVE_API_KEY="rotated-google-drive-api-key"
$env:OPENAI_API_KEY="rotated-openai-api-key"
$env:BANK_RESEARCH_DRIVE_FOLDER_URL="https://drive.google.com/drive/folders/..."
```

Recreate the app container so Docker receives the env vars:

```powershell
docker compose up -d --build
```

Build or refresh the cache:

```powershell
docker exec -w /app macro_dashboard_app python -m scripts.build_bank_research --folder-url "https://drive.google.com/drive/folders/..."
```

The script keeps downloaded report files for `BANK_RESEARCH_RETENTION_DAYS`
days, defaulting to 7 days, and writes summaries to `index.json`.
