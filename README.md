# FormPilot

FormPilot is a web UI plus Python/Playwright backend for inspecting Google Forms, generating structured synthetic/test-response datasets, and running authorized form automation.

## Project layout

```
.
├── index.html
├── formpilot.py
├── open_form.py
├── requirements.txt
├── Dockerfile
├── render.yaml
├── run_formpilot.bat
├── .dockerignore
├── .env.example
├── .gitignore
└── LICENSE
```

## Run locally

```bash
pip install -r requirements.txt
python -m playwright install chromium
python formpilot.py
```

Open `http://127.0.0.1:5000`.

On Windows, `run_formpilot.bat` can also start the application.

## Deploy the complete application

**Do not deploy only `index.html` with GitHub Pages.** FormPilot requires Flask and Playwright, so a static GitHub Pages deployment will return errors such as:

```
POST /api/generate -> 405 Method Not Allowed
```

The repository includes a Docker configuration that runs Flask and Chromium together.

### Render

1. Open Render and choose **New → Blueprint**.
2. Connect `omsanap907-dotcom/google-from-auto-bot`.
3. Render will detect `render.yaml`.
4. Deploy the `formpilot` web service.
5. Open the generated Render URL.

The Docker image installs Chromium and Xvfb so Playwright can run in the server environment.

### Why this works

The browser sends requests such as:

```
POST /api/generate
POST /api/inspect
POST /api/start
POST /api/stop
GET  /api/status
```

When the frontend and Flask backend are served by the same Render service, these relative API URLs work without changing the frontend code.

## GitHub Pages

GitHub Pages is suitable for the source repository but **cannot execute `formpilot.py`**. If GitHub Pages is used as the live site, the UI may load while API calls fail with HTTP 405.

Use the Render service URL for the actual FormPilot application.

## Responsible use

Use FormPilot only with Google Forms and data you are authorized to test or automate. Do not use it to spam forms, bypass access controls, submit deceptive data, or interfere with services.

## Security

- Never commit API keys, passwords, cookies, or other secrets.
- Keep secrets in environment variables.
- Test automation against forms you own or are authorized to test.
- Do not commit `__pycache__` or browser artifacts.

## License

MIT License. See [LICENSE](LICENSE).
