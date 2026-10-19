# Runs the app for the public site (https://ai-job-researcher-app.vercel.app):
# the backend on this PC and the ngrok tunnel the site talks to. Keep this window open.
$root = Split-Path $PSScriptRoot -Parent
$ngrok = "$env:USERPROFILE\tools\ngrok\ngrok.exe"

Start-Process -FilePath "$root\.venv\Scripts\uvicorn.exe" -WorkingDirectory "$root\backend" -ArgumentList @(
  "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1")
Start-Sleep -Seconds 5
& $ngrok http --url=equation-diligent-quaintly.ngrok-free.dev 8000
